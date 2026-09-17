"""Shared types for extractors."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable


class ExtractionError(Exception):
    """A document-level problem. `retryable=False` means retrying cannot help (bad input)."""

    code = "EXTRACTION_ERROR"
    retryable = False

    def __init__(self, message: str, code: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        if code:
            self.code = code
        if retryable is not None:
            self.retryable = retryable


class CorruptedFileError(ExtractionError):
    code = "CORRUPTED_FILE"


class EncryptedDocumentError(ExtractionError):
    code = "ENCRYPTED_DOCUMENT"


class LimitExceededError(ExtractionError):
    code = "LIMIT_EXCEEDED"


class JobCancelled(Exception):
    pass


@dataclass
class PageResult:
    number: int  # 1-based
    text: str
    method: str  # text_layer | ocr:<engine> | parser
    char_count: int = 0
    ocr_confidence: float | None = None
    duration_ms: int | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExtractionResult:
    pages: list[PageResult]
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        # Form feed is the conventional page separator in plain-text extraction (pdftotext does the same).
        return "\n\f\n".join(p.text for p in self.pages) if len(self.pages) > 1 else (
            self.pages[0].text if self.pages else ""
        )


@dataclass
class ExtractionContext:
    options: dict
    progress: Callable[[float, str, str | None], None] = lambda fraction, stage, detail=None: None
    check_cancelled: Callable[[], None] = lambda: None

    @property
    def ocr_mode(self) -> str:
        return self.options.get("ocr_mode", "auto")

    @property
    def ocr_engine(self) -> str:
        from app.config import settings

        return self.options.get("ocr_engine") or settings.default_ocr_engine

    @property
    def languages(self) -> str:
        from app.config import settings

        return self.options.get("language") or settings.default_ocr_languages
