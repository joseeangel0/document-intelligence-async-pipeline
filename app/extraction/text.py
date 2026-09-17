"""Textual formats: plain text (any encoding), HTML and RTF."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from charset_normalizer import from_bytes
from striprtf.striprtf import rtf_to_text

from app.extraction.base import CorruptedFileError, ExtractionContext, ExtractionResult, PageResult


def _decode(raw: bytes) -> tuple[str, dict, list[str]]:
    warnings: list[str] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), {"encoding": "utf-8-sig", "encoding_confidence": 1.0}, warnings
    try:
        return raw.decode("utf-8"), {"encoding": "utf-8", "encoding_confidence": 1.0}, warnings
    except UnicodeDecodeError:
        pass
    best = from_bytes(raw).best()
    if best is None:
        raise CorruptedFileError("The file is not valid text in any known encoding (it may be binary).")
    confidence = round(1 - best.percent_chaos / 100, 2)
    if confidence < 0.9:
        warnings.append(f"Text encoding guessed as {best.encoding} with low confidence; some characters may be wrong.")
    return str(best), {"encoding": best.encoding, "encoding_confidence": confidence}, warnings


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text(path: str, ctx: ExtractionContext) -> ExtractionResult:
    with open(path, "rb") as fh:
        raw = fh.read()
    text, meta, warnings = _decode(raw)
    control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\r\t\f")
    if text and control / len(text) > 0.05:
        raise CorruptedFileError("The file contains binary data and does not look like a text document.")
    text = _normalize(text)
    meta["line_count"] = text.count("\n") + 1 if text else 0
    return ExtractionResult(pages=[PageResult(1, text, "parser", len(text))], metadata={"text": meta}, warnings=warnings)


def extract_html(path: str, ctx: ExtractionContext) -> ExtractionResult:
    with open(path, "rb") as fh:
        raw = fh.read()
    soup = BeautifulSoup(raw, "lxml")  # bs4 sniffs <meta charset> itself
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else None
    html_tag = soup.find("html")
    description = soup.find("meta", attrs={"name": "description"})
    body = soup.body or soup
    text = _normalize(body.get_text("\n"))
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    metadata = {
        "html": {
            "title": title,
            "lang": html_tag.get("lang") if html_tag else None,
            "description": description.get("content") if description else None,
            "links": len(soup.find_all("a")),
            "encoding": soup.original_encoding,
        }
    }
    return ExtractionResult(pages=[PageResult(1, text, "parser", len(text))], metadata=metadata)


def extract_rtf(path: str, ctx: ExtractionContext) -> ExtractionResult:
    with open(path, "rb") as fh:
        raw = fh.read()
    if not raw.lstrip().startswith(b"{\\rtf"):
        raise CorruptedFileError("The RTF header is missing; the file is damaged.")
    try:
        text = _normalize(rtf_to_text(raw.decode("latin-1"), errors="ignore"))
    except Exception as exc:
        raise CorruptedFileError(f"The RTF file could not be parsed: {exc}") from exc
    return ExtractionResult(pages=[PageResult(1, text, "parser", len(text))], metadata={})
