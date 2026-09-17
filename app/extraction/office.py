"""Office Open XML documents (docx, pptx, xlsx) with structure-preserving Markdown and OCR of embedded images."""

from __future__ import annotations

import io
import zipfile

from app.config import settings
from app.extraction.base import (
    CorruptedFileError,
    ExtractionContext,
    ExtractionResult,
    LimitExceededError,
    PageResult,
)
from app.extraction.markdown import MAX_TABLE_ROWS, escape_cell, lines_to_markdown, table_to_markdown

MAX_XLSX_CELLS = 2_000_000
MAX_EMBEDDED_IMAGES = 50
MIN_IMAGE_SIDE = 150  # smaller pictures are icons/logos: not worth OCR
MIN_IMAGE_AREA = 90_000


def _guard_zip(path: str) -> None:
    """OOXML files are zips: reject damaged archives and zip bombs before parsing."""
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            total = sum(info.file_size for info in zf.infolist())
    except zipfile.BadZipFile as exc:
        raise CorruptedFileError(f"The document archive is damaged: {exc}") from exc
    if bad:
        raise CorruptedFileError(f"The document archive is damaged (bad entry: {bad}).")
    if total > settings.max_archive_uncompressed_mb * 1024 * 1024:
        raise LimitExceededError(
            f"The document expands to {total / 1e6:.0f} MB; the limit is {settings.max_archive_uncompressed_mb} MB."
        )


def embedded_media_bytes(path: str) -> int:
    """Total size of images inside an OOXML container (used to route image-heavy files to the OCR queue)."""
    try:
        with zipfile.ZipFile(path) as zf:
            return sum(i.file_size for i in zf.infolist() if "/media/" in i.filename)
    except (zipfile.BadZipFile, OSError):
        return 0


def _core_properties(props) -> dict:
    fields = ("title", "author", "subject", "keywords", "last_modified_by", "created", "modified")
    out = {}
    for name in fields:
        value = getattr(props, name, None)
        out[name] = value.isoformat() if hasattr(value, "isoformat") else (value or None)
    return out


class EmbeddedImageOcr:
    """OCR for pictures pasted into Office documents (scans in Word, screenshots in slides). Cached per image."""

    def __init__(self, ctx: ExtractionContext):
        self.ctx = ctx
        self.enabled = ctx.ocr_mode != "off"
        self.cache: dict[str, str] = {}
        self.processed = 0
        self.skipped_small = 0
        self.confidences: list[float] = []

    def __call__(self, key: str, blob: bytes) -> str:
        if not self.enabled:
            return ""
        if key in self.cache:
            return self.cache[key]
        if self.processed >= MAX_EMBEDDED_IMAGES:
            return ""
        from PIL import Image, UnidentifiedImageError

        try:
            image = Image.open(io.BytesIO(blob))
            image.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            self.cache[key] = ""
            return ""
        if min(image.size) < MIN_IMAGE_SIDE or image.width * image.height < MIN_IMAGE_AREA:
            self.skipped_small += 1
            self.cache[key] = ""
            return ""
        from app.extraction.ocr import get_engine

        self.ctx.check_cancelled()
        result = get_engine(self.ctx.ocr_engine).recognize(image, self.ctx.languages)
        self.processed += 1
        if result.confidence is not None:
            self.confidences.append(result.confidence)
        self.cache[key] = result.text.strip()
        return self.cache[key]

    def metadata(self) -> dict:
        return {
            "embedded_images_ocr": self.processed,
            "embedded_images_skipped_small": self.skipped_small,
            "embedded_images_mean_confidence": round(sum(self.confidences) / len(self.confidences), 1)
            if self.confidences else None,
        }


def _image_block(text: str) -> tuple[str, str]:
    """(plain, markdown) rendering of text recognised inside an embedded picture."""
    md = "\n".join(f"> {line}" if line else ">" for line in lines_to_markdown(text).split("\n"))
    return text, f"> **[Text in image]**\n>\n{md}"


# ------------------------------------------------------------------------------------------------ DOCX


def _docx_heading_level(paragraph, offset: int) -> int:
    """Title -> #, Heading N -> #(N + offset). offset is 1 when the document has a Title, so it stays the only H1."""
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    if name == "Title":
        return 1
    if name.startswith("Heading"):
        digits = "".join(ch for ch in name if ch.isdigit())
        return min((int(digits) if digits else 1) + offset, 6)
    outline = paragraph._p.xpath("./w:pPr/w:outlineLvl/@w:val")
    return min(int(outline[0]) + 1 + offset, 6) if outline and int(outline[0]) < 6 else 0


def _docx_list_prefix(paragraph) -> str | None:
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    num_pr = paragraph._p.xpath("./w:pPr/w:numPr")
    if not num_pr and "List" not in name:
        return None
    level = paragraph._p.xpath("./w:pPr/w:numPr/w:ilvl/@w:val")
    indent = "  " * (int(level[0]) if level else 0)
    return f"{indent}1. " if "Number" in name else f"{indent}- "


def extract_docx(path: str, ctx: ExtractionContext) -> ExtractionResult:
    _guard_zip(path)
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(path)
    except Exception as exc:
        raise CorruptedFileError(f"The Word document could not be parsed: {exc}") from exc

    ocr = EmbeddedImageOcr(ctx)
    text_blocks: list[str] = []
    md_blocks: list[str] = []
    tables = headings = 0

    def table_rows(table: Table) -> list[list[str]]:
        rows = []
        for row in table.rows:
            cells: list[str] = []
            previous = None
            for cell in row.cells:
                if cell._tc is previous:  # horizontally merged cells repeat the same element
                    continue
                previous = cell._tc
                cells.append(cell.text.strip())
            rows.append(cells)
        return rows

    offset = 1 if any(p.style is not None and p.style.name == "Title" for p in document.paragraphs) else 0
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            content = paragraph.text.strip()
            if content:
                level = _docx_heading_level(paragraph, offset)
                prefix = _docx_list_prefix(paragraph)
                text_blocks.append(content)
                if level:
                    headings += 1
                    md_blocks.append(f"{'#' * level} {content}")
                elif prefix:
                    md_blocks.append(prefix + content)
                else:
                    md_blocks.append(content)
            for rid in child.xpath(".//a:blip/@r:embed"):
                part = document.part.related_parts.get(rid)
                recognised = ocr(rid, part.blob) if part is not None else ""
                if recognised:
                    plain, md = _image_block(recognised)
                    text_blocks.append(plain)
                    md_blocks.append(md)
        elif tag == "tbl":
            tables += 1
            rows = table_rows(Table(child, document))
            text_blocks.append("\n".join("\t".join(r) for r in rows))
            md_blocks.append(table_to_markdown(rows))

    header_lines = []
    for section in document.sections:
        header = "\n".join(p.text for p in section.header.paragraphs if p.text.strip())
        if header and header not in header_lines:
            header_lines.append(header)
    if header_lines:
        text_blocks.insert(0, "\n".join(header_lines))

    # Consecutive list items belong together; everything else is separated by a blank line.
    markdown_parts: list[str] = []
    for block in md_blocks:
        is_item = block.lstrip().startswith(("- ", "1. "))
        if markdown_parts and is_item and markdown_parts[-1].split("\n")[-1].lstrip().startswith(("- ", "1. ")):
            markdown_parts[-1] += "\n" + block
        else:
            markdown_parts.append(block)
    text = "\n".join(text_blocks).strip()
    markdown = "\n\n".join(markdown_parts)

    metadata = {
        "docx": {
            **_core_properties(document.core_properties),
            "paragraphs": len(document.paragraphs),
            "headings": headings,
            "tables": tables,
            "images": len(document.inline_shapes),
            **ocr.metadata(),
        }
    }
    warnings = []
    if len(document.inline_shapes) and not ocr.enabled:
        warnings.append("The document contains images; their text was not read because ocr_mode=off.")
    elif len(document.inline_shapes) and len(text) < 50 and not ocr.processed:
        warnings.append("The document is mostly images and no readable text was found in them.")
    page = PageResult(1, text, "parser+ocr" if ocr.processed else "parser", len(text), markdown=markdown)
    return ExtractionResult(pages=[page], metadata=metadata, warnings=warnings)


# ------------------------------------------------------------------------------------------------ PPTX


def extract_pptx(path: str, ctx: ExtractionContext) -> ExtractionResult:
    _guard_zip(path)
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    try:
        prs = Presentation(path)
    except Exception as exc:
        raise CorruptedFileError(f"The presentation could not be parsed: {exc}") from exc

    ocr = EmbeddedImageOcr(ctx)

    def render_shape(shape, title_shape) -> tuple[list[str], list[str]]:
        plain: list[str] = []
        md: list[str] = []
        if shape is title_shape:
            return plain, md
        if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
            lines = []
            for paragraph in shape.text_frame.paragraphs:
                content = "".join(run.text for run in paragraph.runs).strip() or paragraph.text.strip()
                if not content:
                    continue
                plain.append(content)
                is_body_placeholder = shape.is_placeholder and paragraph.level >= 0
                lines.append(("  " * paragraph.level + "- " + content) if is_body_placeholder or paragraph.level else content)
            md.append("\n".join(lines))
        if getattr(shape, "has_table", False):
            rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
            plain.append("\n".join("\t".join(r) for r in rows))
            md.append(table_to_markdown(rows))
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            try:
                recognised = ocr(shape.image.sha1, shape.image.blob)
            except (AttributeError, ValueError):
                recognised = ""
            if recognised:
                p, m = _image_block(recognised)
                plain.append(p)
                md.append(m)
        for sub in getattr(shape, "shapes", []):  # grouped shapes
            sub_plain, sub_md = render_shape(sub, title_shape)
            plain += sub_plain
            md += sub_md
        return plain, md

    pages = []
    slides = list(prs.slides)
    for i, slide in enumerate(slides, start=1):
        ctx.check_cancelled()
        ctx.progress((i - 1) / max(len(slides), 1), "extracting", f"slide {i}/{len(slides)}")
        title_shape = slide.shapes.title
        title = title_shape.text_frame.text.strip() if title_shape is not None and title_shape.has_text_frame else ""
        plain = [title] if title else []
        md = [f"## Slide {i}: {escape_cell(title)}" if title else f"## Slide {i}"]
        # Reading order on a slide: top-to-bottom, then left-to-right.
        shapes = sorted(slide.shapes, key=lambda s: ((s.top or 0) // 20, s.left or 0))
        for shape in shapes:
            shape_plain, shape_md = render_shape(shape, title_shape)
            plain += shape_plain
            md += shape_md
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                plain.append(f"[Notes] {notes}")
                md.append(f"**Speaker notes:** {notes}")
        text = "\n".join(plain).strip()
        pages.append(PageResult(i, text, "parser", len(text), markdown="\n\n".join(md),
                                warnings=[] if text else ["No text on slide"]))
    metadata = {"pptx": {**_core_properties(prs.core_properties), "slides": len(slides),
                         **ocr.metadata()}}
    return ExtractionResult(pages=pages or [PageResult(1, "", "parser")], metadata=metadata)


# ------------------------------------------------------------------------------------------------ XLSX


def extract_xlsx(path: str, ctx: ExtractionContext) -> ExtractionResult:
    _guard_zip(path)
    import openpyxl

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise CorruptedFileError(f"The spreadsheet could not be parsed: {exc}") from exc

    pages, cells_seen, warnings = [], 0, []
    try:
        sheets = wb.worksheets
        for i, sheet in enumerate(sheets, start=1):
            ctx.check_cancelled()
            ctx.progress((i - 1) / max(len(sheets), 1), "extracting", f"sheet {i}/{len(sheets)}: {sheet.title}")
            lines = [f"# {sheet.title}"]
            rows: list[list[str]] = []
            for row in sheet.iter_rows(values_only=True):
                cells_seen += len(row)
                if cells_seen > MAX_XLSX_CELLS:
                    warnings.append(f"Spreadsheet truncated after {MAX_XLSX_CELLS:,} cells.")
                    break
                values = ["" if v is None else str(v) for v in row]
                while values and values[-1] == "":
                    values.pop()
                if values:
                    lines.append("\t".join(values))
                    rows.append(values)
            text = "\n".join(lines)
            if len(rows) > MAX_TABLE_ROWS + 1:
                warnings.append(f"Sheet '{sheet.title}' has {len(rows):,} rows; its Markdown table keeps the first "
                                f"{MAX_TABLE_ROWS:,} (plain text keeps all rows).")
            markdown = f"## Sheet: {escape_cell(sheet.title)}\n\n" + (table_to_markdown(rows) or "*(empty sheet)*")
            pages.append(PageResult(i, text, "parser", len(text), markdown=markdown))
            if cells_seen > MAX_XLSX_CELLS:
                break
        metadata = {"xlsx": {**_core_properties(wb.properties), "sheets": [s.title for s in sheets]}}
    finally:
        wb.close()
    return ExtractionResult(pages=pages, metadata=metadata, warnings=warnings)
