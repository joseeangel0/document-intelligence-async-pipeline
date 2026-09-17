"""High-fidelity pipeline (opt-in, `pipeline=layout`): Docling layout analysis + TableFormer + RapidOCR.

Chosen by the structure benchmark (benchmark/results/structure): it was the only candidate that rebuilt tables
from scanned PDFs and photos (cell F1 1.00 vs 0.00 for OCR lines) at the cost of ~4-8 s/page and ~3 GB RAM, so it
runs on its own worker (compose profile "layout") and queue instead of slowing down the default path.
"""

from __future__ import annotations

import logging
import os
import time
from functools import lru_cache

from app.config import settings
from app.extraction.base import (
    CorruptedFileError,
    EncryptedDocumentError,
    ExtractionContext,
    ExtractionResult,
    LimitExceededError,
    PageResult,
)
from app.extraction.markdown import normalize_markdown

log = logging.getLogger(__name__)
BATCH_PAGES = 4
ENGINE_NAME = "docling"


def available() -> bool:
    try:
        import docling  # noqa: F401

        return bool(os.environ.get("DOCLING_ARTIFACTS"))
    except ImportError:
        return False


@lru_cache
def _converter(full_page_ocr: bool):
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

    threads = int(os.environ.get("OCR_THREADS", "2"))
    opts = PdfPipelineOptions(artifacts_path=os.environ["DOCLING_ARTIFACTS"])
    opts.do_ocr = True
    # RapidOCR, not Tesseract: with the Tesseract backend Docling returned empty table cells on scans (benchmark).
    opts.ocr_options = RapidOcrOptions(force_full_page_ocr=full_page_ocr)
    opts.do_table_structure = True
    opts.table_structure_options.do_cell_matching = True
    opts.accelerator_options = AcceleratorOptions(num_threads=threads, device=AcceleratorDevice.CPU)
    log.info("loading Docling converter (full_page_ocr=%s, threads=%s)", full_page_ocr, threads)
    return DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
    })


def _export(document, page_no: int) -> tuple[str, str]:
    markdown = document.export_to_markdown(
        page_no=page_no, compact_tables=True, escape_underscores=False, image_placeholder=""
    )
    text = document.export_to_text(page_no=page_no)
    return normalize_markdown(markdown), text.strip()


def extract_layout(path: str, kind: str, ctx: ExtractionContext) -> ExtractionResult:
    from docling.datamodel.base_models import ConversionStatus

    warnings: list[str] = []
    metadata: dict = {"pipeline": "layout", "engine": "docling (layout + TableFormer + RapidOCR)"}

    if kind == "pdf":
        import pymupdf

        try:
            with pymupdf.open(path, filetype="pdf") as doc:
                if doc.needs_pass and not doc.authenticate(""):
                    raise EncryptedDocumentError("The PDF is password-protected; remove the password and upload it again.")
                page_count = doc.page_count
                if page_count > settings.max_pdf_pages:
                    raise LimitExceededError(
                        f"The PDF has {page_count} pages; the limit is {settings.max_pdf_pages}. Split it and retry."
                    )
                scanned = [len("".join(p.get_text().split())) < settings.pdf_min_text_chars for p in doc]
                meta = doc.metadata or {}
                metadata["pdf"] = {"title": meta.get("title") or None, "author": meta.get("author") or None,
                                   "producer": meta.get("producer") or None}
        except (EncryptedDocumentError, LimitExceededError):
            raise
        except Exception as exc:
            raise CorruptedFileError(f"The PDF could not be opened ({type(exc).__name__}).") from exc
        if page_count == 0:
            raise CorruptedFileError("The PDF has no pages (it may be truncated or damaged).")
    else:
        from PIL import Image, UnidentifiedImageError

        Image.MAX_IMAGE_PIXELS = settings.max_image_pixels
        try:
            with Image.open(path) as probe:
                probe.verify()
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
            raise CorruptedFileError(f"The image is corrupted, truncated or too large: {exc}") from exc
        page_count, scanned = 1, [True]

    pages: list[PageResult] = []
    for start in range(1, page_count + 1, BATCH_PAGES):
        end = min(start + BATCH_PAGES - 1, page_count)
        ctx.check_cancelled()
        ctx.progress((start - 1) / page_count, "layout", f"pages {start}-{end}/{page_count} (layout, tables, OCR)")
        started = time.perf_counter()
        # Pages without a text layer get full-page OCR; digital pages only OCR their embedded bitmaps.
        converter = _converter(full_page_ocr=any(scanned[start - 1:end]))
        result = converter.convert(path, page_range=(start, end), raises_on_error=False)
        if result.status == ConversionStatus.FAILURE:
            reasons = "; ".join(str(e.error_message) for e in (result.errors or []))[:300]
            raise CorruptedFileError(f"Layout analysis could not read pages {start}-{end}: {reasons or 'unknown error'}")
        if result.status == ConversionStatus.PARTIAL_SUCCESS:
            warnings.append(f"Pages {start}-{end} were only partially converted by the layout pipeline.")
        elapsed_ms = int((time.perf_counter() - started) * 1000 / (end - start + 1))
        for page_no in range(start, end + 1):
            markdown, text = _export(result.document, page_no)
            method = f"layout:{ENGINE_NAME}" + ("+ocr" if scanned[page_no - 1] else "")
            page_warnings = [] if text else ["No text found on this page"]
            pages.append(PageResult(page_no, text, method, len(text), markdown=markdown, duration_ms=elapsed_ms,
                                    warnings=page_warnings))

    empty = [p.number for p in pages if not p.text]
    if empty:
        warnings.append(f"No text was found on page(s) {', '.join(map(str, empty))}.")
    metadata["ocr_pages"] = [i + 1 for i, s in enumerate(scanned) if s]
    return ExtractionResult(pages=pages, metadata=metadata, warnings=warnings)
