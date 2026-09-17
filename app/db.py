"""Database layer: document lifecycle, job tracking and an append-only event log."""

from __future__ import annotations

import enum
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"  # stored + waiting for a worker
    PROCESSING = "PROCESSING"  # claimed by a worker (heartbeat keeps it alive)
    RETRYING = "RETRYING"  # transient failure, will be retried automatically
    SUCCEEDED = "SUCCEEDED"  # terminal
    FAILED = "FAILED"  # terminal
    CANCELLED = "CANCELLED"  # terminal


TERMINAL_STATUSES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
ACTIVE_STATUSES = {JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.RETRYING}


class Base(DeclarativeBase):
    pass


class Document(Base):
    """An uploaded file. Content-addressed: identical bytes are stored only once."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16), index=True)  # pdf, image, text, docx...
    mime_type: Mapped[str] = mapped_column(String(128))
    extension: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_bucket: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    jobs: Mapped[list[Job]] = relationship(back_populates="document")


class Job(Base):
    """One processing request for a document. Source of truth for its lifecycle."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="job_status"), index=True)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    stage_detail: Mapped[str | None] = mapped_column(String(512))
    progress: Mapped[float] = mapped_column(default=0.0)  # 0..100
    queue: Mapped[str] = mapped_column(String(32))
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    client_reference: Mapped[str | None] = mapped_column(String(256), index=True)
    callback_url: Mapped[str | None] = mapped_column(String(1024))

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=settings.max_attempts)
    dispatch_count: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    worker_id: Mapped[str | None] = mapped_column(String(256))

    error_code: Mapped[str | None] = mapped_column(String(64), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)

    # Result (claim check for the output as well: full text lives in object storage)
    result_bucket: Mapped[str | None] = mapped_column(String(64))
    result_text_key: Mapped[str | None] = mapped_column(String(512))
    result_json_key: Mapped[str | None] = mapped_column(String(512))
    result_markdown_key: Mapped[str | None] = mapped_column(String(512))
    result_chunks_key: Mapped[str | None] = mapped_column(String(512))
    text_preview: Mapped[str | None] = mapped_column(Text)
    char_count: Mapped[int | None] = mapped_column(Integer)
    word_count: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    cached_from_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    document: Mapped[Document] = relationship(back_populates="jobs")
    events: Mapped[list[JobEvent]] = relationship(back_populates="job", order_by="JobEvent.id")


class JobEvent(Base):
    """Append-only audit trail shown to users as a timeline."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info | warning | error
    event: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)

    job: Mapped[Job] = relationship(back_populates="events")


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # survive postgres restarts
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create tables. Guarded by an advisory lock so concurrent containers don't race."""
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(424242)"))
        Base.metadata.create_all(conn)
        # Additive, idempotent migrations for databases created by earlier versions.
        for column, ddl in (
            ("result_markdown_key", "VARCHAR(512)"),
            ("result_chunks_key", "VARCHAR(512)"),
            ("token_count", "INTEGER"),
            ("chunk_count", "INTEGER"),
        ):
            conn.execute(text(f"ALTER TABLE jobs ADD COLUMN IF NOT EXISTS {column} {ddl}"))


def add_event(session, job_id, event: str, message: str, level: str = "info", **data) -> None:
    session.add(JobEvent(job_id=job_id, event=event, message=message, level=level, data=data))


__all__ = [
    "ACTIVE_STATUSES",
    "Document",
    "Job",
    "JobEvent",
    "JobStatus",
    "SessionLocal",
    "TERMINAL_STATUSES",
    "add_event",
    "engine",
    "func",
    "init_db",
    "session_scope",
    "utcnow",
]
