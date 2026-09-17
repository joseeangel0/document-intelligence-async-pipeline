"""Geometry helpers for reading order (shared by OCR box ordering and PDF layout analysis)."""

from __future__ import annotations

import re
import statistics


def line_records(boxes: list[dict]) -> list[dict]:
    """Boxes grouped into visual lines (top-to-bottom, left-to-right) with each line's height and left edge."""
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: (b["y0"] + b["y1"]) / 2)
    median_h = statistics.median(b["y1"] - b["y0"] for b in boxes) or 1
    lines: list[list[dict]] = []
    for b in boxes:
        cy = (b["y0"] + b["y1"]) / 2
        if lines and abs(cy - statistics.mean((x["y0"] + x["y1"]) / 2 for x in lines[-1])) < 0.5 * median_h:
            lines[-1].append(b)
        else:
            lines.append([b])
    records = []
    for line in lines:
        line = sorted(line, key=lambda b: b["x0"])
        records.append({
            "text": " ".join(b["text"] for b in line),
            "height": statistics.median(b["y1"] - b["y0"] for b in line),
            "x0": line[0]["x0"],
        })
    return records


def group_lines(boxes: list[dict]) -> str:
    """Join boxes into lines: top-to-bottom, boxes on the same line left-to-right."""
    return "\n".join(r["text"] for r in line_records(boxes))


def merge_wrapped_headings(parts: list[str]) -> list[str]:
    """`## The Right to Pay No More than the` + `## Correct Amount of Tax` -> one heading (titles wrapped over lines)."""
    merged: list[str] = []
    for part in parts:
        match = re.match(r"^(#{1,6}) (.+)$", part)
        previous = re.match(r"^(#{1,6}) (.+)$", merged[-1]) if merged else None
        if match and previous and match.group(1) == previous.group(1) and not re.search(r"[.:;!?]$", previous.group(2)) \
                and "\n" not in part and len(previous.group(2)) + len(match.group(2)) < 160:
            merged[-1] = f"{previous.group(1)} {previous.group(2)} {match.group(2)}"
        else:
            merged.append(part)
    return merged


def largest_gap(intervals: list[tuple[float, float]]) -> tuple[float, float]:
    """Largest empty gap in the 1-D projection of intervals -> (gap size, cut position)."""
    intervals = sorted(intervals)
    best, cut, reach = 0.0, 0.0, intervals[0][1]
    for lo, hi in intervals[1:]:
        if lo - reach > best:
            best, cut = lo - reach, (lo + reach) / 2
        reach = max(reach, hi)
    return best, cut


def xy_cut(boxes: list[dict], median_h: float) -> list[list[dict]]:
    """Recursive XY-cut: split on the widest whitespace gap (columns need a gap >= 1 line height)."""
    if len(boxes) <= 1:
        return [boxes]
    gap_y, cut_y = largest_gap([(b["y0"], b["y1"]) for b in boxes])
    gap_x, cut_x = largest_gap([(b["x0"], b["x1"]) for b in boxes])
    if gap_x >= median_h and gap_x > gap_y:
        left = [b for b in boxes if (b["x0"] + b["x1"]) / 2 < cut_x]
        right = [b for b in boxes if (b["x0"] + b["x1"]) / 2 >= cut_x]
        return xy_cut(left, median_h) + xy_cut(right, median_h)
    if gap_y > 0:
        top = [b for b in boxes if (b["y0"] + b["y1"]) / 2 < cut_y]
        bottom = [b for b in boxes if (b["y0"] + b["y1"]) / 2 >= cut_y]
        return xy_cut(top, median_h) + xy_cut(bottom, median_h)
    return [boxes]
