"""Cheap, on-device enrichment computed from extracted text: stats, language, key entities."""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

MAX_ENTITIES = 25

PATTERNS = {
    "emails": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "urls": re.compile(r"\bhttps?://[^\s<>()\"']+", re.IGNORECASE),
    "dates": re.compile(
        r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
        r"|\d{1,2}\s+(?:de\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|ene|abr|ago|dic)[a-z]*\.?(?:\s+(?:de\s+)?\d{4})?)\b",
        re.IGNORECASE,
    ),
    "amounts": re.compile(
        r"(?:(?:US\$|MX\$|\$|€|£)\s?\d[\d,.]*\d|\b\d[\d,.]*\d\s?(?:USD|MXN|EUR|€|pesos|dollars)\b)", re.IGNORECASE
    ),
    "phone_numbers": re.compile(
        r"(?<!\w)(?:(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,3}\)[\s.-]?)?\d{2,4}[\s.-]\d{3,4}[\s.-]\d{3,4}|\+\d{11,13})(?!\w)"
    ),
}


def detect_language(text: str) -> dict | None:
    sample = " ".join(text.split())[:4000]
    if len(sample) < 20:
        return None
    try:
        from fast_langdetect import detect

        result = detect(sample, low_memory=True)
        return {"code": result["lang"], "confidence": round(float(result["score"]), 3)}
    except Exception as exc:  # language is a nice-to-have, never fail a job for it
        log.warning("language detection failed: %s", exc)
        return None


def extract_entities(text: str) -> dict:
    out = {}
    for name, pattern in PATTERNS.items():
        seen: list[str] = []
        for match in pattern.finditer(text):
            value = match.group(0).strip().rstrip(".,;:")
            if value not in seen:
                seen.append(value)
            if len(seen) >= MAX_ENTITIES:
                break
        if seen:
            out[name] = seen
    return out


def text_statistics(text: str, pages) -> dict:
    words = len(text.split())
    confidences = [p.ocr_confidence for p in pages if p.ocr_confidence is not None]
    methods: dict[str, int] = {}
    for p in pages:
        methods[p.method] = methods.get(p.method, 0) + 1
    return {
        "char_count": len(text),
        "word_count": words,
        "line_count": text.count("\n") + 1 if text else 0,
        "page_count": len(pages),
        "reading_time_min": round(words / 230, 1),
        "extraction_methods": methods,
        "mean_ocr_confidence": round(sum(confidences) / len(confidences), 1) if confidences else None,
    }
