"""Structure-aware chunking for retrieval (RAG).

The document Markdown is split into blocks (headings, paragraphs, lists, tables, code). Blocks are packed into
chunks up to a token budget while keeping provenance: page range, heading path and character offsets into
`document.md`, so an LLM answer can cite "page 3, section Payment terms".

Rules
* a new heading closes the current chunk once that chunk holds at least `min_tokens` (no micro-chunks);
* tables are never split mid-row; a table larger than the budget is split by rows and the header row is
  repeated in every part, so each chunk is still a readable table;
* an oversized paragraph is split by sentences, then (last resort) by tokens;
* `overlap` tokens from the end of the previous chunk are prepended when both chunks belong to the same section.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache

log = logging.getLogger(__name__)

PAGE_MARKER_RE = re.compile(r"^<!-- page: (\d+) -->$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
SENTENCE_RE = re.compile(r"(?<=[.!?¡¿…])\s+")
ENCODING = "cl100k_base"


@lru_cache
def _encoder():
    try:
        import tiktoken

        return tiktoken.get_encoding(ENCODING)
    except Exception as exc:  # offline without cached encoding: fall back to an estimate
        log.warning("tiktoken unavailable (%s); estimating tokens as chars/4", exc)
        return None


def count_tokens(text: str) -> int:
    enc = _encoder()
    return len(enc.encode(text, disallowed_special=())) if enc else max(1, len(text) // 4) if text else 0


def tokenizer_name() -> str:
    return f"tiktoken:{ENCODING}" if _encoder() else "estimate:chars/4"


def _tail_tokens(text: str, n: int) -> str:
    """Last ~n tokens of `text`, starting at a word boundary (never mid-word)."""
    if n <= 0 or not text:
        return ""
    enc = _encoder()
    if enc:
        tokens = enc.encode(text, disallowed_special=())
        tail = enc.decode(tokens[-n:]) if len(tokens) > n else text
    else:
        tail = text[-n * 4:]
    if tail != text:
        cut = re.search(r"\s", tail)
        tail = tail[cut.end():] if cut else ""
    return tail.strip()


@dataclass
class Block:
    kind: str  # heading | paragraph | list | table | code
    text: str
    page: int
    char_start: int
    char_end: int
    heading_path: list[str]
    level: int = 0
    tokens: int = 0


@dataclass
class Chunk:
    id: str
    index: int
    text: str
    token_count: int
    page_start: int
    page_end: int
    heading_path: list[str]
    char_start: int
    char_end: int
    kinds: list[str] = field(default_factory=list)
    overlap_tokens: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def parse_blocks(markdown: str) -> list[Block]:
    """Split document Markdown into structural blocks, tracking page markers and the heading stack."""
    blocks: list[Block] = []
    page = 1
    headings: list[tuple[int, str]] = []
    lines = markdown.split("\n")
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        start = offsets[i]
        if not stripped:
            i += 1
            continue
        marker = PAGE_MARKER_RE.match(stripped)
        if marker:
            page = int(marker.group(1))
            i += 1
            continue
        heading = HEADING_RE.match(stripped)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            headings = [h for h in headings if h[0] < level] + [(level, title)]
            blocks.append(Block("heading", stripped, page, start, start + len(line), [h[1] for h in headings], level))
            i += 1
            continue
        if stripped.startswith("```"):
            fence = re.match(r"^`{3,}", stripped).group(0)
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(fence):
                j += 1
            j = min(j + 1, len(lines))
            kind, end_line = "code", j
        elif stripped.startswith("|"):
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            kind, end_line = "table", j
        else:
            j = i
            while (
                j < len(lines)
                and lines[j].strip()
                and not PAGE_MARKER_RE.match(lines[j].strip())
                and not HEADING_RE.match(lines[j].strip())
                and not lines[j].strip().startswith(("|", "```"))
            ):
                j += 1
            kind = "list" if re.match(r"^(?:[-*+]|\d+\.)\s", stripped) else "paragraph"
            end_line = j
        text = "\n".join(lines[i:end_line]).strip("\n")
        end = offsets[end_line - 1] + len(lines[end_line - 1])
        blocks.append(Block(kind, text, page, start, end, [h[1] for h in headings]))
        i = end_line
    for block in blocks:
        block.tokens = count_tokens(block.text)
    return blocks


def _split_oversized(block: Block, budget: int) -> list[Block]:
    if block.tokens <= budget:
        return [block]
    parts: list[str] = []
    if block.kind == "table":
        rows = block.text.split("\n")
        header, rows = (rows[:2], rows[2:]) if len(rows) > 2 and set(rows[1].replace("|", "").strip()) <= {"-", ":", " "} else ([], rows)
        header_tokens = count_tokens("\n".join(header))
        current: list[str] = []
        for row in rows:
            if current and count_tokens("\n".join(current + [row])) + header_tokens > budget:
                parts.append("\n".join(header + current))
                current = []
            current.append(row)
        if current:
            parts.append("\n".join(header + current))
    else:
        units = SENTENCE_RE.split(block.text) if block.kind != "code" else block.text.split("\n")
        current_text = ""
        for unit in units:
            candidate = f"{current_text} {unit}".strip() if block.kind != "code" else f"{current_text}\n{unit}".strip("\n")
            if current_text and count_tokens(candidate) > budget:
                parts.append(current_text)
                current_text = unit
            else:
                current_text = candidate
        if current_text:
            parts.append(current_text)
        # sentences longer than the budget: hard split by tokens
        hard: list[str] = []
        enc = _encoder()
        for part in parts:
            if count_tokens(part) <= budget:
                hard.append(part)
            elif enc:
                tokens = enc.encode(part, disallowed_special=())
                hard += [enc.decode(tokens[k:k + budget]) for k in range(0, len(tokens), budget)]
            else:
                hard += [part[k:k + budget * 4] for k in range(0, len(part), budget * 4)]
        parts = hard
    return [
        Block(block.kind, p, block.page, block.char_start, block.char_end, block.heading_path, block.level, count_tokens(p))
        for p in parts
    ]


def chunk_markdown(markdown: str, chunk_size: int = 512, overlap: int = 64, min_tokens: int | None = None) -> list[Chunk]:
    chunk_size = max(32, chunk_size)
    overlap = max(0, min(overlap, chunk_size // 2))
    min_tokens = chunk_size // 4 if min_tokens is None else min_tokens
    budget = chunk_size - overlap

    blocks: list[Block] = []
    for block in parse_blocks(markdown):
        blocks.extend(_split_oversized(block, budget))

    chunks: list[Chunk] = []
    current: list[Block] = []
    current_tokens = 0
    last_kind = ""
    prose = ("paragraph", "list")

    def flush() -> None:
        nonlocal current, current_tokens, last_kind
        content = [b for b in current if b.kind != "heading"]
        if not content:  # a chunk made only of headings is folded into the next chunk
            return
        body = "\n\n".join(b.text for b in current)
        previous = chunks[-1] if chunks else None
        prefix = ""
        # Overlap only joins prose to prose inside the same top-level section (tables repeat their header instead).
        if (previous and overlap and last_kind in prose and current[0].kind in prose
                and previous.heading_path[:1] == content[0].heading_path[:1]):
            prefix = _tail_tokens(previous.text, overlap)
        text = f"{prefix}\n\n{body}".strip() if prefix else body
        chunks.append(
            Chunk(
                id=f"c{len(chunks):05d}",
                index=len(chunks),
                text=text,
                token_count=count_tokens(text),
                page_start=min(b.page for b in current),
                page_end=max(b.page for b in current),
                heading_path=content[0].heading_path,
                char_start=current[0].char_start,
                char_end=current[-1].char_end,
                kinds=sorted({b.kind for b in content}),
                overlap_tokens=count_tokens(prefix) if prefix else 0,
            )
        )
        last_kind = content[-1].kind
        current, current_tokens = [], 0

    for block in blocks:
        separator = 1 if current else 0  # "\n\n" is ~1 token
        if block.kind == "heading" and current and current_tokens >= min_tokens:
            flush()
        elif current and current_tokens + block.tokens + separator > budget:
            trailing_headings = []
            while current and current[-1].kind == "heading":  # keep headings with their content
                trailing_headings.insert(0, current.pop())
            flush()
            current = trailing_headings
            current_tokens = sum(b.tokens for b in current)
        current.append(block)
        current_tokens += block.tokens + separator
    flush()
    return chunks
