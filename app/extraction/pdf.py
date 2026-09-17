"""PDF extraction: native text layer first, OCR only for pages that need it (hybrid strategy)."""

from __future__ import annotations

import re
import time

import pymupdf
from PIL import Image

from app.config import settings
from app.extraction.base import (
    CorruptedFileError,
    EncryptedDocumentError,
    ExtractionContext,
    ExtractionResult,
    LimitExceededError,
    PageResult,
)
from app.extraction.ocr import get_engine
from app.extraction.pdf_markdown import font_profile, page_to_markdown

pymupdf.TOOLS.mupdf_display_errors(False)


def _pdf_date(value: str | None) -> str | None:
    # "D:20240131120000+01'00'" -> "2024-01-31T12:00:00"
    if not value or not value.startswith("D:") or len(value) < 16:
        return value or None
    v = value[2:]
    return f"{v[0:4]}-{v[4:6]}-{v[6:8]}T{v[8:10]}:{v[10:12]}:{v[12:14]}"


def _image_coverage(page: pymupdf.Page) -> float:
    area = abs(page.rect) or 1
    covered = 0.0
    for info in page.get_image_info():
        bbox = pymupdf.Rect(info["bbox"]) & page.rect
        covered += abs(bbox)
    return min(covered / area, 1.0)


def _render(page: pymupdf.Page, dpi: int) -> Image.Image:
    # Keep rendered pages below the pixel budget (huge posters/plans would otherwise explode memory).
    w_in, h_in = page.rect.width / 72, page.rect.height / 72
    max_dpi = int((settings.max_image_pixels / max(w_in * h_in, 1e-6)) ** 0.5)
    dpi = max(72, min(dpi, max_dpi))
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def extract_pdf(path: str, ctx: ExtractionContext) -> ExtractionResult:
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as exc:  # pymupdf raises FileDataError / RuntimeError variants
        raise CorruptedFileError(
            "The PDF could not be opened: the file is damaged or is not really a PDF "
            f"({type(exc).__name__})."
        ) from exc

    with doc:
        if doc.needs_pass and not doc.authenticate(""):
            raise EncryptedDocumentError("The PDF is password-protected; remove the password and upload it again.")
        if doc.page_count == 0:
            raise CorruptedFileError("The PDF has no pages (it may be truncated or damaged).")
        if doc.page_count > settings.max_pdf_pages:
            raise LimitExceededError(
                f"The PDF has {doc.page_count} pages; the limit is {settings.max_pdf_pages}. Split it and retry."
            )

        warnings: list[str] = []
        pages: list[PageResult] = []
        mode = ctx.ocr_mode
        profile = font_profile(doc)
        table_count = 0
        engine = None
        repaired = bool(doc.is_repaired)
        if repaired:
            warnings.append("The PDF structure was damaged and has been repaired automatically; check the output.")

        for index in range(doc.page_count):
            ctx.check_cancelled()
            started = time.perf_counter()
            number = index + 1
            ctx.progress(index / doc.page_count, "extracting", f"page {number}/{doc.page_count}")
            page_warnings: list[str] = []
            ocr_markdown = ""
            try:
                page = doc.load_page(index)
                native = page.get_text("text", sort=True).strip()
            except Exception as exc:
                pages.append(PageResult(number, "", "error", warnings=[f"Page could not be read: {exc}"]))
                warnings.append(f"Page {number} could not be read and was skipped ({exc}).")
                continue

            needs_ocr = mode == "force" or (
                mode == "auto"
                and (
                    len("".join(native.split())) < settings.pdf_min_text_chars
                    or (_image_coverage(page) > 0.6 and len(native) < 200)
                )
            )
            if needs_ocr:
                engine = engine or get_engine(ctx.ocr_engine)
                ctx.progress(index / doc.page_count, "ocr", f"page {number}/{doc.page_count} (no text layer, running OCR)")
                ocr = engine.recognize(_render(page, settings.ocr_dpi), ctx.languages)
                # In auto mode keep whichever is richer: OCR of a page that had some native text can be worse.
                if mode == "auto" and len(native) > len(ocr.text):
                    text, method, confidence = native, "text_layer", None
                else:
                    text, method, confidence = ocr.text, f"ocr:{engine.name}", ocr.confidence
                    ocr_markdown = ocr.markdown
                    if confidence is not None and confidence < settings.low_confidence_threshold:
                        page_warnings.append(f"Low OCR confidence ({confidence:.0f}/100)")
                    if ocr.rotated_degrees:
                        page_warnings.append(f"Page appeared rotated {ocr.rotated_degrees}°; corrected before OCR")
            else:
                text, method, confidence = native, "text_layer", None
                if mode == "off" and len(native) < settings.pdf_min_text_chars:
                    page_warnings.append("No text layer and OCR is disabled (ocr_mode=off)")

            markdown = ocr_markdown if method.startswith("ocr") else ""
            if method == "text_layer" and text.strip():
                try:
                    markdown, found_tables = page_to_markdown(page, profile)
                    # Coverage guard: layout analysis must never silently drop text (seen on real-world PDFs).
                    if _word_coverage(markdown, text) < 0.9:
                        page_warnings.append("Layout analysis lost text on this page; Markdown falls back to plain text")
                        markdown = ""
                    else:
                        table_count += found_tables
                except Exception as exc:  # structure is best effort; plain text is always available
                    page_warnings.append(f"Layout analysis failed ({type(exc).__name__}); Markdown has no structure")
            if not text.strip():
                page_warnings.append("No text found on this page")
            pages.append(
                PageResult(
                    number=number,
                    text=text,
                    markdown=markdown,
                    method=method,
                    char_count=len(text),
                    ocr_confidence=confidence,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    warnings=page_warnings,
                )
            )

        meta = doc.metadata or {}
        ocr_pages = [p.number for p in pages if p.method.startswith("ocr")]
        metadata = {
            "pdf": {
                "title": meta.get("title") or None,
                "author": meta.get("author") or None,
                "subject": meta.get("subject") or None,
                "keywords": meta.get("keywords") or None,
                "creator": meta.get("creator") or None,
                "producer": meta.get("producer") or None,
                "created": _pdf_date(meta.get("creationDate")),
                "modified": _pdf_date(meta.get("modDate")),
                "format": meta.get("format") or None,
                "encrypted": bool(meta.get("encryption")),
                "repaired": repaired,
                "page_size_pt": [round(doc[0].rect.width), round(doc[0].rect.height)],
                "has_outline": bool(doc.get_toc(simple=True)),
            },
            "text_layer_pages": [p.number for p in pages if p.method == "text_layer"],
            "tables_detected": table_count,
            "body_font_size": profile["body"] or None,
            "ocr_pages": ocr_pages,
        }
        low = [p.number for p in pages if any("Low OCR confidence" in w for w in p.warnings)]
        empty = [p.number for p in pages if not p.text.strip()]
        if ocr_pages and len(ocr_pages) != len(pages):
            warnings.append(f"Mixed document: {_pages(ocr_pages)} had no usable text layer and were OCR'd.")
        if low:
            warnings.append(f"Low OCR confidence on {_pages(low)}; the text may contain recognition errors.")
        if empty:
            warnings.append(f"No text was found on {_pages(empty)}.")
        return ExtractionResult(pages=pages, metadata=metadata, warnings=warnings)


def _word_coverage(markdown: str, text: str) -> float:
    from collections import Counter

    reference = Counter(re.findall(r"\w+", text.lower()))
    if not reference:
        return 1.0
    produced = Counter(re.findall(r"\w+", markdown.lower()))
    return sum(min(count, produced[word]) for word, count in reference.items()) / sum(reference.values())


def _pages(numbers: list[int]) -> str:
    return f"page {numbers[0]}" if len(numbers) == 1 else f"pages {_ranges(numbers)}"


def _ranges(numbers: list[int]) -> str:
    """[1,2,3,5] -> '1-3, 5'"""
    if not numbers:
        return ""
    out, start, prev = [], numbers[0], numbers[0]
    for n in numbers[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        out.append(f"{start}-{prev}" if start != prev else str(start))
        if n is not None:
            start = prev = n
    return ", ".join(out)
