"""Textual formats: plain text family (txt, md, csv, tsv, json, xml, log), HTML and RTF."""

from __future__ import annotations

import csv
import io
import json
import os
import re

from bs4 import BeautifulSoup
from charset_normalizer import from_bytes
from striprtf.striprtf import rtf_to_text

from app.extraction.base import CorruptedFileError, ExtractionContext, ExtractionResult, PageResult
from app.extraction.markdown import MAX_TABLE_ROWS, fenced, lines_to_markdown, normalize_markdown, table_to_markdown

MAX_PRETTY_JSON_BYTES = 2 * 1024 * 1024


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


def _plain_markdown(text: str) -> str:
    # Plain text is not Markdown: escape characters that would accidentally become headings/rules.
    escaped = re.sub(r"^(\s*)([#>]|={3,}|-{3,})", r"\1\\\2", text, flags=re.M)
    return lines_to_markdown(escaped)


def _csv_markdown(text: str, delimiter: str | None) -> tuple[str, dict, list[str]]:
    sample = text[:20000]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    warnings = []
    if len(rows) > MAX_TABLE_ROWS + 1:
        warnings.append(f"CSV has {len(rows) - 1:,} rows; the Markdown table keeps the first {MAX_TABLE_ROWS:,} "
                        "(the plain text keeps everything).")
    return table_to_markdown(rows), {"rows": len(rows), "columns": max((len(r) for r in rows), default=0),
                                     "delimiter": delimiter}, warnings


def extract_text(path: str, ctx: ExtractionContext) -> ExtractionResult:
    with open(path, "rb") as fh:
        raw = fh.read()
    text, meta, warnings = _decode(raw)
    control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\r\t\f")
    if text and control / len(text) > 0.05:
        raise CorruptedFileError("The file contains binary data and does not look like a text document.")
    text = _normalize(text)
    meta["line_count"] = text.count("\n") + 1 if text else 0

    ext = os.path.splitext(path)[1].lower()
    if ext in (".md", ".markdown"):
        markdown = text
    elif ext in (".csv", ".tsv"):
        markdown, table_meta, table_warnings = _csv_markdown(text, "\t" if ext == ".tsv" else None)
        meta["table"] = table_meta
        warnings += table_warnings
    elif ext == ".json":
        try:
            pretty = json.dumps(json.loads(text), indent=2, ensure_ascii=False) if len(raw) <= MAX_PRETTY_JSON_BYTES else text
        except ValueError:
            pretty = text
            warnings.append("The .json file is not valid JSON; it was kept as-is.")
        markdown = fenced(pretty, "json")
    elif ext == ".xml":
        markdown = fenced(text, "xml")
    else:
        markdown = _plain_markdown(text)
    page = PageResult(1, text, "parser", len(text), markdown=markdown)
    return ExtractionResult(pages=[page], metadata={"text": meta}, warnings=warnings)


def html_to_markdown(html: bytes | str) -> tuple[str, str, dict]:
    """HTML -> (plain text, Markdown, metadata). Scripts, styles and embedded media are dropped."""
    from markdownify import MarkdownConverter

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg", "iframe"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else None
    html_tag = soup.find("html")
    description = soup.find("meta", attrs={"name": "description"})
    body = soup.body or soup
    text = re.sub(r"\n\s*\n+", "\n\n", _normalize(body.get_text("\n")))
    markdown = MarkdownConverter(heading_style="ATX", bullets="-", strip=["img"], escape_underscores=False).convert_soup(body)
    metadata = {
        "title": title,
        "lang": html_tag.get("lang") if html_tag else None,
        "description": description.get("content") if description else None,
        "links": len(soup.find_all("a")),
        "tables": len(soup.find_all("table")),
        "encoding": getattr(soup, "original_encoding", None),
    }
    return text, normalize_markdown(markdown), metadata


def extract_html(path: str, ctx: ExtractionContext) -> ExtractionResult:
    with open(path, "rb") as fh:
        raw = fh.read()
    text, markdown, meta = html_to_markdown(raw)
    if meta["title"] and not markdown.lstrip().startswith("# "):
        markdown = f"# {meta['title']}\n\n{markdown}"
    return ExtractionResult(pages=[PageResult(1, text, "parser", len(text), markdown=markdown)], metadata={"html": meta})


def extract_rtf(path: str, ctx: ExtractionContext) -> ExtractionResult:
    with open(path, "rb") as fh:
        raw = fh.read()
    if not raw.lstrip().startswith(b"{\\rtf"):
        raise CorruptedFileError("The RTF header is missing; the file is damaged.")
    try:
        text = _normalize(rtf_to_text(raw.decode("latin-1"), errors="ignore"))
    except Exception as exc:
        raise CorruptedFileError(f"The RTF file could not be parsed: {exc}") from exc
    return ExtractionResult(pages=[PageResult(1, text, "parser", len(text), markdown=_plain_markdown(text))], metadata={})
