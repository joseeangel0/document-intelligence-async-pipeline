"""Small Markdown builders shared by the extractors (LLM-ready output)."""

from __future__ import annotations

import re

MAX_TABLE_ROWS = 2000  # beyond this a table is still in `text`, but Markdown gets truncated with a note


def escape_cell(value) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text.replace("|", "\\|")).strip()


def table_to_markdown(rows: list[list], header: bool = True) -> str:
    """Rows of cells -> GitHub-flavoured pipe table. The first row is the header (LLMs read it as column names)."""
    rows = [[escape_cell(c) for c in row] for row in rows if any(str(c or "").strip() for c in row)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    truncated = len(rows) > MAX_TABLE_ROWS + 1
    rows = rows[: MAX_TABLE_ROWS + 1]
    head = rows[0] if header else [f"Column {i + 1}" for i in range(width)]
    body = rows[1:] if header else rows
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * width) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    if truncated:
        lines.append(f"\n*(table truncated after {MAX_TABLE_ROWS} rows)*")
    return "\n".join(lines)


BULLET_RE = re.compile(r"^[•●▪◦‣∙·\-–—*]\s+")
NUMBERED_RE = re.compile(r"^\(?(\d{1,3})[.)]\s+")


def lines_to_markdown(text: str) -> str:
    """Plain text (e.g. OCR output) -> Markdown: blank-line separated paragraphs, bullet/numbered lists kept as
    list items. Line breaks inside a paragraph are preserved (OCR'd tables keep one row per line)."""
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        parts: list[str] = []
        paragraph: list[str] = []
        for line in (l.strip() for l in block.splitlines()):
            if not line:
                continue
            numbered = NUMBERED_RE.match(line)
            if numbered or BULLET_RE.match(line):
                if paragraph:
                    parts.append("\n".join(paragraph))
                    paragraph = []
                parts.append(f"{numbered.group(1)}. {line[numbered.end():]}" if numbered
                             else "- " + BULLET_RE.sub("", line, count=1))
            else:
                paragraph.append(line)
        if paragraph:
            parts.append("\n".join(paragraph))
        if parts:
            out.append("\n".join(parts))
    return "\n\n".join(out)


def fenced(text: str, language: str = "") -> str:
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}{language}\n{text.rstrip()}\n{fence}"


def normalize_markdown(md: str) -> str:
    md = md.replace("\r\n", "\n")
    md = re.sub(r"[ \t]+\n", "\n", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def strip_markdown(md: str) -> str:
    """Rough Markdown -> text (used for sanity metrics, not for output)."""
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    md = re.sub(r"^#{1,6}\s+", "", md, flags=re.M)
    md = re.sub(r"^\|?[\s:|-]+\|?$", "", md, flags=re.M)
    md = md.replace("|", " ")
    md = re.sub(r"[*_`]{1,3}", "", md)
    return re.sub(r"[ \t]+", " ", md)
