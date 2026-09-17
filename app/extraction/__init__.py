"""Document intelligence: route a stored file to the right extractor, then produce LLM-ready outputs."""

from __future__ import annotations

import re

from app.extraction.base import ExtractionContext, ExtractionResult
from app.extraction.enrich import detect_language, extract_entities, text_statistics


LAYOUT_KINDS = ("pdf", "image")


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
    if kind == "eml":
        from app.extraction.mail import extract_eml

        return extract_eml
    raise ValueError(f"No extractor for kind '{kind}'")


def extract_document(path: str, kind: str, ctx: ExtractionContext) -> ExtractionResult:
    """Raw extraction (pages with text + Markdown), without enrichment. Used recursively for e-mail attachments."""
    return _extractor(kind)(path, ctx)


def run_extraction(path: str, kind: str, ctx: ExtractionContext) -> ExtractionResult:
    from app.extraction.chunking import chunk_markdown, count_tokens, tokenizer_name

    fallback_warning = None
    if ctx.options.get("pipeline") == "layout" and kind in LAYOUT_KINDS:
        from app.extraction import layout_pipeline

        if layout_pipeline.available():
            result = layout_pipeline.extract_layout(path, kind, ctx)
        else:  # the job reached a worker without the layout models (e.g. manual re-routing)
            fallback_warning = "The high-fidelity layout pipeline is not installed on this worker; standard pipeline used."
            result = extract_document(path, kind, ctx)
    else:
        result = extract_document(path, kind, ctx)
    if fallback_warning:
        result.warnings.append(fallback_warning)
    ctx.progress(0.95, "structuring", "Markdown and retrieval chunks")
    text = result.text
    markdown = result.markdown
    result.chunks = chunk_markdown(markdown, ctx.chunk_size, ctx.chunk_overlap) if markdown.strip() else []
    stats = text_statistics(text, result.pages)
    stats.update(
        token_count=count_tokens(markdown),
        tokenizer=tokenizer_name(),
        chunk_count=len(result.chunks),
        chunk_size_tokens=ctx.chunk_size,
        chunk_overlap_tokens=ctx.chunk_overlap,
        markdown_chars=len(markdown),
        tables=len(re.findall(r"^\|(?:\s*:?-+:?\s*\|)+\s*$", markdown, flags=re.M)),
    )
    ctx.progress(0.98, "enriching", "language, entities and statistics")
    outline = [
        {"level": len(m.group(1)), "title": m.group(2).strip()}
        for m in re.finditer(r"^(#{1,6})\s+(.+)$", markdown, flags=re.M)
    ][:200]
    result.metadata = {
        **result.metadata,
        "stats": stats,
        "outline": outline,
        "language": detect_language(text),
        "entities": extract_entities(text) if ctx.options.get("extract_entities", True) else None,
    }
    if not text.strip():
        result.warnings.append(
            "No text could be extracted. The document may be blank, contain only graphics, "
            "or (for images/scans) the text is not legible."
        )
    return result
