"""File-type detection by *content* (magic bytes), not by the name the client sent."""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass, field

import magic
from charset_normalizer import from_path

from app.config import settings

# kind -> (canonical mimes, extensions, queue, description)
FORMATS: dict[str, dict] = {
    "pdf": {
        "mimes": {"application/pdf"},
        "extensions": [".pdf"],
        "queue": "heavy",
        "description": "PDF (born-digital text layer; scanned pages are OCR'd automatically)",
    },
    "image": {
        "mimes": {"image/png", "image/jpeg", "image/tiff", "image/webp", "image/bmp", "image/gif"},
        "extensions": [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp", ".gif"],
        "queue": "heavy",
        "description": "Raster images (OCR). Multi-page TIFF supported.",
    },
    "text": {
        "mimes": {"text/plain", "text/csv", "text/markdown", "application/json", "text/xml", "application/xml"},
        "extensions": [".txt", ".md", ".markdown", ".csv", ".tsv", ".log", ".json", ".xml"],
        "queue": "light",
        "description": "Plain text (any encoding, auto-detected): txt, md, csv, tsv, log, json, xml",
    },
    "html": {
        "mimes": {"text/html", "application/xhtml+xml"},
        "extensions": [".html", ".htm", ".xhtml"],
        "queue": "light",
        "description": "HTML pages (scripts/styles removed)",
    },
    "docx": {
        "mimes": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        "extensions": [".docx"],
        "queue": "light",
        "description": "Microsoft Word (paragraphs + tables, in document order)",
    },
    "pptx": {
        "mimes": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
        "extensions": [".pptx"],
        "queue": "light",
        "description": "Microsoft PowerPoint (one page per slide, including notes)",
    },
    "xlsx": {
        "mimes": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        "extensions": [".xlsx"],
        "queue": "light",
        "description": "Microsoft Excel (one page per sheet, tab-separated cells)",
    },
    "rtf": {
        "mimes": {"text/rtf", "application/rtf"},
        "extensions": [".rtf"],
        "queue": "light",
        "description": "Rich Text Format",
    },
}

MIME_TO_KIND = {mime: kind for kind, spec in FORMATS.items() for mime in spec["mimes"]}
EXT_TO_KIND = {ext: kind for kind, spec in FORMATS.items() for ext in spec["extensions"]}
OOXML_MARKERS = {"docx": "word/document.xml", "pptx": "ppt/presentation.xml", "xlsx": "xl/workbook.xml"}
TEXTUAL_EXTENSIONS = set(FORMATS["text"]["extensions"]) | set(FORMATS["html"]["extensions"])

MIME_ALIASES = {
    "image/x-ms-bmp": "image/bmp",
    "text/x-csv": "text/csv",
    "application/csv": "text/csv",
    "text/rtf": "text/rtf",
}


class UnsupportedFormatError(Exception):
    def __init__(self, detected_mime: str, extension: str):
        self.detected_mime = detected_mime
        self.extension = extension
        super().__init__(f"Unsupported format: content looks like '{detected_mime}' (extension '{extension or 'none'}')")


@dataclass
class DetectedFormat:
    kind: str
    mime: str
    extension: str
    queue: str
    warnings: list[str] = field(default_factory=list)


def _sniff_ooxml(path: str) -> str | None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return None
    for kind, marker in OOXML_MARKERS.items():
        if marker in names:
            return kind
    return None


def _looks_like_text(path: str) -> bool:
    result = from_path(path, steps=5, chunk_size=2048).best()
    return result is not None and result.percent_chaos < 20


def detect_format(path: str, filename: str) -> DetectedFormat:
    ext = os.path.splitext(filename or "")[1].lower()
    mime = magic.from_file(path, mime=True) or "application/octet-stream"
    mime = MIME_ALIASES.get(mime, mime)
    kind = MIME_TO_KIND.get(mime)
    warnings: list[str] = []

    # Office documents are ZIP containers; libmagic sometimes only says "zip".
    if kind is None and mime in ("application/zip", "application/octet-stream", "application/x-zip-compressed"):
        kind = _sniff_ooxml(path)
        if kind:
            mime = next(iter(FORMATS[kind]["mimes"]))

    # Text-ish files that libmagic labels oddly (e.g. "text/x-python" for a .md, "text/x-c" for code).
    if kind is None and mime.startswith("text/"):
        kind = EXT_TO_KIND.get(ext) if ext in TEXTUAL_EXTENSIONS else "text"
        if kind == "html" and not mime.endswith("html"):
            kind = "text"
    # UTF-16 / unusual encodings reported as octet-stream but declared textual.
    if kind is None and ext in FORMATS["text"]["extensions"] and _looks_like_text(path):
        kind = "text"
        mime = "text/plain"

    if kind is None:
        raise UnsupportedFormatError(mime, ext)

    # Plain text files keep their declared flavour (csv/md/json) for the consumer.
    if kind == "text" and ext in FORMATS["text"]["extensions"]:
        canonical_ext = ext
    else:
        canonical_ext = ext if ext in FORMATS[kind]["extensions"] else FORMATS[kind]["extensions"][0]

    declared_kind = EXT_TO_KIND.get(ext)
    if ext and declared_kind != kind:
        warnings.append(
            f"File extension '{ext}' does not match its content ({mime}); it was processed as '{kind}'."
        )
    elif not ext:
        warnings.append(f"File has no extension; content detected as {mime}.")

    return DetectedFormat(kind=kind, mime=mime, extension=canonical_ext, queue=FORMATS[kind]["queue"], warnings=warnings)


def supported_formats_summary() -> dict:
    return {
        "formats": [
            {"kind": kind, "extensions": spec["extensions"], "mime_types": sorted(spec["mimes"]),
             "description": spec["description"], "queue": spec["queue"]}
            for kind, spec in FORMATS.items()
        ],
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_pdf_pages": settings.max_pdf_pages,
            "max_image_megapixels": round(settings.max_image_pixels / 1_000_000, 1),
            "max_office_uncompressed_mb": settings.max_archive_uncompressed_mb,
            "processing_time_limit_s": settings.job_soft_time_limit_s,
            "max_attempts": settings.max_attempts,
        },
    }
