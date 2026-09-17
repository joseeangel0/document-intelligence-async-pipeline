"""Born-digital PDF page -> Markdown using PyMuPDF layout information.

* headings: font size relative to the document's body size (computed once per document), or short bold lines;
* tables: PyMuPDF `find_tables()` (ruled and aligned tables) rendered as pipe tables, their text removed from
  the surrounding flow;
* lists: bullet / numbered line prefixes;
* paragraphs: lines of a text block joined, de-hyphenated.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter

import pymupdf

from app.extraction.layout import merge_wrapped_headings, xy_cut
from app.extraction.markdown import BULLET_RE, NUMBERED_RE, table_to_markdown

BULLET_CHARS = {"•", "●", "▪", "◦", "‣", "∙", "·", "-", "–", "*"}

BOLD_FLAG = 16
MAX_PROFILE_PAGES = 30


def font_profile(doc: pymupdf.Document) -> dict:
    """Body font size (the size carrying most characters) and the larger sizes used for headings."""
    sizes: Counter = Counter()
    for page in doc.pages(0, min(doc.page_count, MAX_PROFILE_PAGES)):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    text = span["text"].strip()
                    if text:
                        sizes[round(span["size"] * 2) / 2] += len(text)
    if not sizes:
        return {"body": 0.0, "levels": {}}
    body = sizes.most_common(1)[0][0]
    total = sum(sizes.values())
    heading_sizes = sorted(
        (size for size, chars in sizes.items() if size >= body * 1.15 and chars < total * 0.3), reverse=True
    )[:3]
    return {"body": body, "levels": {size: i + 1 for i, size in enumerate(heading_sizes)}}


def _heading_level(size: float, bold: bool, text: str, profile: dict) -> int:
    body = profile["body"]
    if not body or len(text) > 150 or text.endswith((".", ",", ";", ":")) and len(text) > 60:
        return 0
    rounded = round(size * 2) / 2
    if rounded in profile["levels"]:
        return profile["levels"][rounded]
    larger = [s for s in profile["levels"] if rounded >= s]
    if rounded >= body * 1.15 and larger:
        return profile["levels"][max(larger)]
    if bold and rounded >= body and len(text) <= 80 and not text.endswith("."):
        return min(len(profile["levels"]) + 1, 4)
    return 0


def _inside(rect: tuple, boxes: list[pymupdf.Rect]) -> bool:
    r = pymupdf.Rect(rect)
    center = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
    return any(center in box for box in boxes)


def _visual_lines(block: dict, table_boxes: list[pymupdf.Rect]) -> list[dict]:
    """PyMuPDF lines that share a baseline (a bullet glyph and its text, label and value) merged left-to-right."""
    raw = []
    for line in block["lines"]:
        if _inside(line["bbox"], table_boxes):
            continue
        spans = [s for s in line["spans"] if s["text"].strip()]
        if spans:
            raw.append({"bbox": line["bbox"], "spans": spans})
    raw.sort(key=lambda l: ((l["bbox"][1] + l["bbox"][3]) / 2, l["bbox"][0]))
    merged: list[dict] = []
    for line in raw:
        cy = (line["bbox"][1] + line["bbox"][3]) / 2
        height = line["bbox"][3] - line["bbox"][1]
        if merged and abs(cy - merged[-1]["cy"]) < 0.5 * max(height, 1):
            merged[-1]["parts"].append(line)
        else:
            merged.append({"cy": cy, "parts": [line]})
    out = []
    for group in merged:
        parts = sorted(group["parts"], key=lambda l: l["bbox"][0])
        spans = [s for p in parts for s in p["spans"]]
        cells = ["".join(s["text"] for s in p["spans"]).strip() for p in parts]
        size = max(s["size"] for s in spans)
        gaps = [parts[i + 1]["bbox"][0] - parts[i]["bbox"][2] for i in range(len(parts) - 1)]
        out.append({
            "cells": cells,
            # Several pieces on one baseline separated by wide gaps: a row of an (unruled) table.
            "columnar": len(parts) >= 2 and min(gaps) > 1.5 * size,
            "text": " ".join(cells),
            "size": size,
            "bold": all(s["flags"] & BOLD_FLAG or "bold" in s["font"].lower() for s in spans),
            "bbox": (min(p["bbox"][0] for p in parts), min(p["bbox"][1] for p in parts),
                     max(p["bbox"][2] for p in parts), max(p["bbox"][3] for p in parts)),
        })
    return out


def page_to_markdown(page: pymupdf.Page, profile: dict) -> tuple[str, int]:
    """Returns (markdown, number of tables found)."""
    tables = []
    try:
        for table in page.find_tables().tables:
            rows = table.extract()
            if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
                tables.append((pymupdf.Rect(table.bbox), rows))
    except Exception:  # table detection is best effort
        tables = []
    table_boxes = [box for box, _ in tables]

    blocks = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") == 0:
            lines = _visual_lines(block, table_boxes)
            if lines:
                x0, y0, x1, y1 = (min(l["bbox"][0] for l in lines), min(l["bbox"][1] for l in lines),
                                  max(l["bbox"][2] for l in lines), max(l["bbox"][3] for l in lines))
                blocks.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "lines": lines})
    for box, rows in tables:
        blocks.append({"x0": box.x0, "y0": box.y0, "x1": box.x1, "y1": box.y1, "table": rows})
    if not blocks:
        return "", len(tables)

    line_heights = [l["bbox"][3] - l["bbox"][1] for b in blocks for l in b.get("lines", [])]
    median_h = statistics.median(line_heights) if line_heights else 10
    # Short bold lines are headings only when nothing else sits on the same baseline (otherwise: table header cells).
    baselines = [((l["bbox"][1] + l["bbox"][3]) / 2, id(b)) for b in blocks for l in b.get("lines", [])]

    def shares_baseline(line: dict, block: dict) -> bool:
        cy = (line["bbox"][1] + line["bbox"][3]) / 2
        return any(abs(cy - other) < median_h * 0.5 and owner != id(block) for other, owner in baselines)

    parts: list[str] = []
    unruled_tables = 0
    # XY-cut gives the reading order: full-width headings first, then each column top to bottom.
    ordered = [b for group in xy_cut(blocks, median_h) for b in sorted(group, key=lambda b: (b["y0"], b["x0"]))]
    i = 0
    while i < len(ordered):
        block = ordered[i]
        if "table" in block:
            parts.append(table_to_markdown(block["table"]))
            i += 1
            continue
        # Consecutive single-row columnar blocks with the same number of aligned cells -> unruled table.
        run = []
        j = i
        while j < len(ordered) and _table_row(ordered[j]) is not None and (
            not run or len(_table_row(ordered[j])) == len(run[0])
        ):
            run.append(_table_row(ordered[j]))
            j += 1
        if len(run) >= 3 or (len(run) == 2 and ordered[i]["lines"][0]["bold"]):
            parts.append(table_to_markdown(run))
            unruled_tables += 1
            i = j
            continue
        parts.extend(_block_markdown(block, profile, shares_baseline))
        i += 1
    parts = merge_wrapped_headings([p for p in parts if p.strip()])
    return "\n\n".join(_merge_lists(parts)), len(tables) + unruled_tables


def _table_row(block: dict) -> list[str] | None:
    lines = block.get("lines")
    if not lines or "table" in block:
        return None
    if all(line["columnar"] for line in lines) and len({len(line["cells"]) for line in lines}) == 1 and len(lines) == 1:
        return lines[0]["cells"]
    return None


def _block_markdown(block: dict, profile: dict, shares_baseline) -> list[str]:
    out: list[str] = []
    paragraph: list[str] = []
    block_width = max(block["x1"] - block["x0"], 1)
    lines = block["lines"]

    def flush() -> None:
        if paragraph:
            joined = "".join(paragraph).strip()
            out.append(re.sub(r"(\w)- (\w)", r"\1\2", joined))  # undo end-of-line hyphenation
            paragraph.clear()

    for line in lines:
        text, size, bold = line["text"], line["size"], line["bold"]
        level = _heading_level(size, bold, text, profile)
        if level and size < profile["body"] * 1.15 and (len(lines) > 3 or line["columnar"] or shares_baseline(line, block)):
            level = 0  # bold-only emphasis inside a paragraph or a table header cell
        numbered = NUMBERED_RE.match(text)
        if level:
            flush()
            out.append(f"{'#' * level} {text}")
        elif numbered:
            flush()
            out.append(f"{numbered.group(1)}. {text[numbered.end():]}")
        elif BULLET_RE.match(text) or text in BULLET_CHARS:
            flush()
            out.append("- " + BULLET_RE.sub("", text, count=1) if text not in BULLET_CHARS else "- ")
        elif out and out[-1] == "- ":
            out[-1] += text  # bullet glyph on its own line, item text on the next
        elif out and re.match(r"^(?:- |\d+\. )", out[-1]) and not paragraph and line["bbox"][0] > block["x0"] + 2:
            out[-1] += " " + text  # wrapped list item (indented continuation)
        else:
            # Wrapped prose runs to the right margin and is re-flowed; short or column-aligned lines
            # (addresses, key-value pairs, invoice rows) keep their line break.
            keeps_break = (line["bbox"][2] - line["bbox"][0]) < 0.75 * block_width or "   " in text
            paragraph.append(text + ("\n" if keeps_break else " "))
    flush()
    return [o for o in out if o != "- "]


def _merge_lists(parts: list[str]) -> list[str]:
    """Keep consecutive list items in one block (a Markdown list), everything else separated."""
    merged: list[str] = []
    for part in parts:
        is_item = bool(re.match(r"^(?:- |\d+\. )", part)) and "\n\n" not in part
        if merged and is_item and re.match(r"^(?:- |\d+\. )", merged[-1].split("\n")[-1]):
            merged[-1] += "\n" + part
        else:
            merged.append(part)
    return merged
