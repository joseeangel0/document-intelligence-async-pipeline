"""API response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.db import TERMINAL_STATUSES, Job, JobEvent

STATUS_MESSAGES = {
    "QUEUED": "Waiting for a worker.",
    "PROCESSING": "A worker is extracting the text.",
    "RETRYING": "A temporary problem occurred; the job will be retried automatically.",
    "SUCCEEDED": "Text extracted successfully.",
    "FAILED": "Processing failed. See error_code and error_message.",
    "CANCELLED": "The job was cancelled.",
}


class DocumentOut(BaseModel):
    id: UUID
    filename: str
    kind: str
    mime_type: str
    size_bytes: int
    sha256: str


class ErrorOut(BaseModel):
    code: str
    message: str


class Links(BaseModel):
    self: str
    events: str
    result: str
    text: str
    document: str


class JobOut(BaseModel):
    id: UUID
    status: str
    status_message: str
    is_terminal: bool
    stage: str
    stage_detail: str | None
    progress: float = Field(description="0-100")
    queue: str
    attempts: int
    max_attempts: int
    options: dict
    client_reference: str | None
    warnings: list[str]
    error: ErrorOut | None
    document: DocumentOut
    char_count: int | None
    word_count: int | None
    page_count: int | None
    language: dict | None
    cached_from_job_id: UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    next_retry_at: datetime | None
    duration_s: float | None
    queue_wait_s: float | None
    links: Links


class JobEventOut(BaseModel):
    at: datetime
    level: str
    event: str
    message: str
    data: dict


class JobList(BaseModel):
    items: list[JobOut]
    total: int
    limit: int
    offset: int


class ResultOut(BaseModel):
    job_id: UUID
    text: str
    pages: list[dict[str, Any]]
    metadata: dict
    warnings: list[str]


def job_to_out(job: Job) -> JobOut:
    doc = job.document
    base = f"/v1/jobs/{job.id}"
    duration = queue_wait = None
    if job.finished_at and job.started_at:
        duration = round((job.finished_at - job.started_at).total_seconds(), 2)
    if job.started_at:
        queue_wait = round((job.started_at - job.created_at).total_seconds(), 2)
    return JobOut(
        id=job.id,
        status=job.status.value,
        status_message=STATUS_MESSAGES[job.status.value],
        is_terminal=job.status in TERMINAL_STATUSES,
        stage=job.stage,
        stage_detail=job.stage_detail,
        progress=job.progress,
        queue=job.queue,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        options=job.options or {},
        client_reference=job.client_reference,
        warnings=job.warnings or [],
        error=ErrorOut(code=job.error_code, message=job.error_message or "") if job.error_code else None,
        document=DocumentOut(
            id=doc.id, filename=doc.original_filename, kind=doc.kind, mime_type=doc.mime_type,
            size_bytes=doc.size_bytes, sha256=doc.sha256,
        ),
        char_count=job.char_count,
        word_count=job.word_count,
        page_count=job.page_count,
        language=(job.metadata_ or {}).get("language"),
        cached_from_job_id=job.cached_from_job_id,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        heartbeat_at=job.heartbeat_at,
        next_retry_at=job.next_retry_at,
        duration_s=duration,
        queue_wait_s=queue_wait,
        links=Links(self=base, events=f"{base}/events", result=f"{base}/result", text=f"{base}/text",
                    document=f"{base}/document"),
    )


def event_to_out(event: JobEvent) -> JobEventOut:
    return JobEventOut(at=event.created_at, level=event.level, event=event.event, message=event.message,
                       data=event.data or {})
