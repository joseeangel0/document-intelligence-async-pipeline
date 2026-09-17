"""Image extraction via OCR, with decompression-bomb protection and multi-frame (TIFF/GIF) support."""

from __future__ import annotations

import time
import warnings as py_warnings

from PIL import ExifTags, Image, ImageSequence, UnidentifiedImageError

from app.config import settings
from app.extraction.base import (
    CorruptedFileError,
    ExtractionContext,
    ExtractionResult,
    LimitExceededError,
    PageResult,
)
from app.extraction.ocr import get_engine

Image.MAX_IMAGE_PIXELS = settings.max_image_pixels
MAX_FRAMES = 200


def _exif(img: Image.Image) -> dict:
    try:
        raw = img.getexif()
    except Exception:
        return {}
    wanted = {"Make", "Model", "DateTime", "Software", "Orientation"}
    out = {}
    for tag_id, value in raw.items():
        name = ExifTags.TAGS.get(tag_id)
        if name in wanted:
            out[name] = str(value)
    return out


def extract_image(path: str, ctx: ExtractionContext) -> ExtractionResult:
    if ctx.ocr_mode == "off":
        return ExtractionResult(
            pages=[PageResult(1, "", "none", warnings=["OCR disabled (ocr_mode=off)"])],
            warnings=["Images only contain text through OCR, and ocr_mode=off was requested: no text extracted."],
        )
    try:
        with py_warnings.catch_warnings():
            py_warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(path)
            probe.verify()  # structural check (truncated / corrupted files)
        img = Image.open(path)
        img.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise LimitExceededError(
            f"Image is too large to process safely (limit {settings.max_image_pixels / 1e6:.0f} MP): {exc}"
        ) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise CorruptedFileError(f"The image is corrupted or truncated: {exc}") from exc

    engine = get_engine(ctx.ocr_engine)
    n_frames = getattr(img, "n_frames", 1)
    warnings: list[str] = []
    if n_frames > MAX_FRAMES:
        raise LimitExceededError(f"Image has {n_frames} frames; the limit is {MAX_FRAMES}.")
    if img.format == "GIF" and n_frames > 1:
        n_frames = 1  # animated GIF: frames are near-identical, OCR the first one only
        warnings.append("Animated GIF: only the first frame was processed.")

    dpi = img.info.get("dpi")
    metadata = {
        "image": {
            "format": img.format,
            "width": img.width,
            "height": img.height,
            "mode": img.mode,
            "frames": getattr(img, "n_frames", 1),
            "dpi": [round(float(d)) for d in dpi] if dpi else None,
            "exif": _exif(img),
        },
        "ocr_engine": engine.name,
        "ocr_languages": ctx.languages,
    }
    if min(img.size) < 300:
        warnings.append(f"Low resolution image ({img.width}x{img.height}); OCR accuracy may be poor.")

    pages: list[PageResult] = []
    for index, frame in enumerate(ImageSequence.Iterator(img)):
        if index >= n_frames:
            break
        ctx.check_cancelled()
        ctx.progress(index / n_frames, "ocr", f"frame {index + 1}/{n_frames}" if n_frames > 1 else "running OCR")
        started = time.perf_counter()
        result = engine.recognize(frame.copy(), ctx.languages)
        page_warnings = []
        if result.confidence is not None and result.confidence < settings.low_confidence_threshold:
            page_warnings.append(f"Low OCR confidence ({result.confidence:.0f}/100)")
        if result.rotated_degrees:
            page_warnings.append(f"Image appeared rotated {result.rotated_degrees}°; corrected before OCR")
        if not result.text.strip():
            page_warnings.append("No text found")
        pages.append(
            PageResult(
                number=index + 1,
                text=result.text,
                method=f"ocr:{engine.name}",
                char_count=len(result.text),
                ocr_confidence=result.confidence,
                duration_ms=int((time.perf_counter() - started) * 1000),
                warnings=page_warnings,
            )
        )

    for page in pages:
        for w in page.warnings:
            prefix = f"Frame {page.number}: " if len(pages) > 1 else ""
            warnings.append(prefix + w + ("; the text may contain recognition errors." if "confidence" in w else "."))
    return ExtractionResult(pages=pages, metadata=metadata, warnings=warnings)
