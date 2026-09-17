"""Document intelligence: route a stored file to the right extractor and enrich the result."""

from __future__ import annotations

from app.extraction.base import ExtractionContext, ExtractionResult
from app.extraction.enrich import detect_language, extract_entities, text_statistics


def _extractor(kind: str):
    if kind == "pdf":
        from app.extraction.pdf import extract_pdf

        return extract_pdf
    if kind == "image":
        from app.extraction.image import extract_image

        return extract_image
    if kind in ("text", "html", "rtf"):
        from app.extraction import text

        return getattr(text, f"extract_{kind}")
    if kind in ("docx", "pptx", "xlsx"):
        from app.extraction import office

        return getattr(office, f"extract_{kind}")
    raise ValueError(f"No extractor for kind '{kind}'")


def run_extraction(path: str, kind: str, ctx: ExtractionContext) -> ExtractionResult:
    result = _extractor(kind)(path, ctx)
    ctx.progress(0.97, "enriching", "language, entities and statistics")
    text = result.text
    stats = text_statistics(text, result.pages)
    result.metadata = {
        **result.metadata,
        "stats": stats,
        "language": detect_language(text),
        "entities": extract_entities(text) if ctx.options.get("extract_entities", True) else None,
    }
    if not text.strip():
        result.warnings.append(
            "No text could be extracted. The document may be blank, contain only graphics, "
            "or (for images/scans) the text is not legible."
        )
    return result
