"""E-mail messages (.eml): headers, body and supported attachments processed in the same job."""

from __future__ import annotations

import os
import tempfile
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from app.config import settings
from app.extraction.base import CorruptedFileError, ExtractionContext, ExtractionError, ExtractionResult, PageResult
from app.extraction.markdown import escape_cell, lines_to_markdown

MAX_ATTACHMENTS = 20


def _addresses(value) -> list[str]:
    return [addr for _, addr in getaddresses([str(value)]) if addr] if value else []


def extract_eml(path: str, ctx: ExtractionContext) -> ExtractionResult:
    from app.extraction import extract_document
    from app.extraction.formats import UnsupportedFormatError, detect_format
    from app.extraction.text import html_to_markdown

    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as exc:
        raise CorruptedFileError(f"The e-mail could not be parsed: {exc}") from exc
    if not msg.keys():
        raise CorruptedFileError("The file has no e-mail headers.")

    subject = str(msg.get("subject", "") or "")
    date = None
    if msg.get("date"):
        try:
            date = parsedate_to_datetime(str(msg["date"])).isoformat()
        except (TypeError, ValueError):
            date = str(msg["date"])
    headers = {
        "from": _addresses(msg.get("from")),
        "to": _addresses(msg.get("to")),
        "cc": _addresses(msg.get("cc")),
        "date": date,
        "subject": subject,
        "message_id": str(msg.get("message-id", "") or "") or None,
    }

    body_part = msg.get_body(preferencelist=("html", "plain"))
    body_text, body_md = "", ""
    if body_part is not None:
        content = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            body_text, body_md, _ = html_to_markdown(content)
        else:
            body_text, body_md = content.strip(), lines_to_markdown(content)

    header_rows = [f"| {k.title()} | {escape_cell(', '.join(v) if isinstance(v, list) else v)} |"
                   for k, v in headers.items() if v and k != "message_id"]
    markdown = f"# {escape_cell(subject) or '(no subject)'}\n\n| Field | Value |\n|---|---|\n" + "\n".join(header_rows)
    markdown += f"\n\n{body_md}" if body_md else "\n\n*(empty body)*"
    text = "\n".join(f"{k.title()}: {', '.join(v) if isinstance(v, list) else v}" for k, v in headers.items() if v)
    text += f"\n\n{body_text}"

    pages = [PageResult(1, text, "parser", len(text), markdown=markdown)]
    warnings: list[str] = []
    attachments = []
    for index, part in enumerate(msg.iter_attachments()):
        name = part.get_filename() or f"attachment-{index + 1}"
        payload = part.get_payload(decode=True) or b""
        info = {"filename": name, "content_type": part.get_content_type(), "size_bytes": len(payload), "processed": False}
        attachments.append(info)
        if index >= MAX_ATTACHMENTS:
            info["skipped"] = "too many attachments"
            continue
        if not payload or len(payload) > settings.max_upload_bytes:
            info["skipped"] = "empty or larger than the upload limit"
            continue
        ctx.check_cancelled()
        with tempfile.TemporaryDirectory(prefix="eml-") as tmp:
            local = os.path.join(tmp, "attachment" + os.path.splitext(name)[1].lower())
            with open(local, "wb") as out:
                out.write(payload)
            try:
                fmt = detect_format(local, name)
                if fmt.kind == "eml":
                    info["skipped"] = "nested e-mails are not expanded"
                    continue
                typed_path = os.path.join(tmp, "attachment" + fmt.extension)  # extractors read the extension
                os.replace(local, typed_path)
                result = extract_document(typed_path, fmt.kind, ctx)
            except UnsupportedFormatError as exc:
                info["skipped"] = f"unsupported format ({exc.detected_mime})"
                continue
            except ExtractionError as exc:
                info["skipped"] = f"{exc.code}: {exc}"
                warnings.append(f"Attachment '{name}' could not be processed: {exc}")
                continue
        info.update(processed=True, kind=fmt.kind, pages=len(result.pages))
        for page_index, page in enumerate(result.pages):
            page_md = page.markdown or lines_to_markdown(page.text)
            if page_index == 0:
                page_md = f"## Attachment: {escape_cell(name)}\n\n{page_md}"
            pages.append(PageResult(len(pages) + 1, page.text, page.method, page.char_count, markdown=page_md,
                                    ocr_confidence=page.ocr_confidence, duration_ms=page.duration_ms,
                                    warnings=page.warnings))
        warnings += [f"Attachment '{name}': {w}" for w in result.warnings]
        info["metadata"] = {k: v for k, v in result.metadata.items() if k not in ("stats",)}

    if attachments:
        listing = "\n".join(
            f"- {escape_cell(a['filename'])} ({a['content_type']}, {a['size_bytes']:,} bytes)"
            + ("" if a["processed"] else f" — not processed: {a.get('skipped', '')}")
            for a in attachments
        )
        pages[0].markdown += f"\n\n## Attachments\n\n{listing}"
    return ExtractionResult(pages=pages, metadata={"email": {**headers, "attachments": attachments}}, warnings=warnings)
