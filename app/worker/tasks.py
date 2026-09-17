"""Celery tasks: document processing, recovery sweep and webhooks."""

from __future__ import annotations

import json
import logging
import os
import socket
import tempfile
import threading
import time
import uuid
from datetime import timedelta

import httpx
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_process_init, worker_ready
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app import jobs, storage
from app.config import settings
from app.db import Job, JobStatus, add_event, init_db, session_scope, utcnow
from app.extraction import run_extraction
from app.extraction.base import ExtractionContext, ExtractionError, JobCancelled
from app.worker.celery_app import celery_app

log = logging.getLogger(__name__)

PREVIEW_CHARS = 1000


class LostOwnership(Exception):
    """The sweeper re-assigned this job (we were considered dead). Stop without writing results."""


@worker_ready.connect
def _on_worker_ready(**_):
    # A restarted stack should heal itself immediately, not only on the next beat tick.
    def sweep():
        try:
            init_db()
            storage.ensure_buckets()
            log.info("startup recovery sweep: %s", jobs.recover_jobs())
        except Exception:
            log.exception("startup recovery sweep failed (the scheduler will retry)")

    threading.Thread(target=sweep, daemon=True).start()


@worker_process_init.connect
def _on_process_init(**_):
    # Never share DB connections across forked processes.
    from app.db import engine

    engine.dispose(close=False)


class Heartbeat(threading.Thread):
    """Keeps `heartbeat_at` fresh while a job runs and relays cancellation / ownership loss."""

    def __init__(self, job_id: str, worker_id: str):
        super().__init__(daemon=True)
        self.job_id = uuid.UUID(job_id)
        self.worker_id = worker_id
        self.stop_event = threading.Event()
        self.cancel_requested = False
        self.lost = False

    def beat(self) -> None:
        with session_scope() as session:
            row = session.execute(
                update(Job)
                .where(Job.id == self.job_id, Job.status == JobStatus.PROCESSING, Job.worker_id == self.worker_id)
                .values(heartbeat_at=utcnow())
                .returning(Job.cancel_requested)
            ).first()
        if row is None:
            self.lost = True
        else:
            self.cancel_requested = bool(row[0])

    def run(self) -> None:
        while not self.stop_event.wait(settings.heartbeat_interval_s):
            try:
                self.beat()
            except Exception as exc:  # DB blip: keep trying, the sweeper tolerates a few missed beats
                log.warning("heartbeat for %s failed: %s", self.job_id, exc)


class ProgressReporter:
    """Maps extractor progress into the job row, throttled to avoid hammering the DB."""

    def __init__(self, job_id: str, heartbeat: Heartbeat, worker_id: str):
        self.job_id = uuid.UUID(job_id)
        self.heartbeat = heartbeat
        self.worker_id = worker_id
        self._last_write = 0.0
        self._last_stage = None

    def set(self, percent: float, stage: str, detail: str | None = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and stage == self._last_stage and now - self._last_write < 1.0:
            return
        self._last_write, self._last_stage = now, stage
        with session_scope() as session:
            session.execute(
                update(Job)
                .where(Job.id == self.job_id, Job.worker_id == self.worker_id, Job.status == JobStatus.PROCESSING)
                .values(progress=round(min(percent, 99.0), 1), stage=stage, stage_detail=detail, heartbeat_at=utcnow())
            )

    def extraction_callback(self, fraction: float, stage: str, detail: str | None = None) -> None:
        self.check()
        self.set(5 + fraction * 85, stage, detail)  # extraction spans 5% -> 90%

    def check(self) -> None:
        if self.heartbeat.lost:
            raise LostOwnership()
        if self.heartbeat.cancel_requested:
            raise JobCancelled()


def _worker_id() -> str:
    # unique per attempt, so a restarted process with a recycled pid can never impersonate an old owner
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


@celery_app.task(name="docintel.process_document", bind=True, acks_late=True)
def process_document(self, job_id: str) -> dict:
    worker_id = _worker_id()
    try:
        claimed = jobs.claim(job_id, worker_id)
    except OperationalError as exc:
        # Postgres unavailable: put the message back later; the job row is untouched.
        log.warning("DB unavailable while claiming %s, retrying: %s", job_id, exc)
        raise self.retry(countdown=15, max_retries=None) from exc
    if claimed is None:
        log.info("job %s not claimable (duplicate delivery, cancelled or already handled); skipping", job_id)
        return {"job_id": job_id, "skipped": True}

    heartbeat = Heartbeat(job_id, worker_id)
    heartbeat.start()
    progress = ProgressReporter(job_id, heartbeat, worker_id)
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="docintel-") as tmp:
            progress.set(1, "downloading", "fetching document from object storage", force=True)
            local_path = os.path.join(tmp, f"input{claimed.extension}")
            storage.download_file(claimed.bucket, claimed.key, local_path)
            if os.path.getsize(local_path) != claimed.size_bytes:
                raise storage.StorageError("downloaded file size does not match the uploaded size")

            ctx = ExtractionContext(
                options=claimed.options,
                progress=progress.extraction_callback,
                check_cancelled=progress.check,
            )
            progress.set(5, "extracting", f"{claimed.kind} document", force=True)
            t0 = time.perf_counter()
            result = run_extraction(local_path, claimed.kind, ctx)
            extraction_ms = int((time.perf_counter() - t0) * 1000)

        progress.check()
        progress.set(92, "storing", "saving extracted text", force=True)
        text = result.text
        metadata = {**result.metadata, "timings_ms": {"extraction": extraction_ms}}
        markdown = result.markdown
        chunks = [c.to_dict() for c in result.chunks]
        text_key = f"jobs/{job_id}/text.txt"
        markdown_key = f"jobs/{job_id}/document.md"
        chunks_key = f"jobs/{job_id}/chunks.jsonl"
        json_key = f"jobs/{job_id}/result.json"
        storage.put_bytes(settings.s3_bucket_results, text_key, text.encode("utf-8"), "text/plain; charset=utf-8")
        storage.put_bytes(settings.s3_bucket_results, markdown_key, markdown.encode("utf-8"), "text/markdown; charset=utf-8")
        storage.put_bytes(
            settings.s3_bucket_results, chunks_key,
            "".join(json.dumps({"job_id": job_id, **c}, ensure_ascii=False) + "\n" for c in chunks).encode("utf-8"),
            "application/x-ndjson",
        )
        storage.put_json(
            settings.s3_bucket_results,
            json_key,
            {
                "job_id": job_id,
                "document": {"filename": claimed.filename, "kind": claimed.kind, "sha256": claimed.sha256},
                "options": claimed.options,
                "text": text,
                "markdown": markdown,
                "pages": [p.to_dict() for p in result.pages],
                "chunks": chunks,
                "metadata": metadata,
                "warnings": result.warnings,
            },
        )
        progress.check()
        metadata["timings_ms"]["total"] = int((time.perf_counter() - started) * 1000)
        stats = metadata.get("stats", {})
        with session_scope() as session:
            done = session.execute(
                update(Job)
                .where(Job.id == uuid.UUID(job_id), Job.worker_id == worker_id, Job.status == JobStatus.PROCESSING)
                .values(
                    status=JobStatus.SUCCEEDED,
                    stage="done",
                    stage_detail=None,
                    progress=100.0,
                    result_bucket=settings.s3_bucket_results,
                    result_text_key=text_key,
                    result_json_key=json_key,
                    result_markdown_key=markdown_key,
                    result_chunks_key=chunks_key,
                    token_count=stats.get("token_count"),
                    chunk_count=stats.get("chunk_count"),
                    text_preview=text[:PREVIEW_CHARS],
                    char_count=stats.get("char_count"),
                    word_count=stats.get("word_count"),
                    page_count=stats.get("page_count"),
                    metadata_=metadata,
                    warnings=_merge_warnings(session, job_id, result.warnings),
                    finished_at=utcnow(),
                )
                .returning(Job.id)
            ).first()
            if not done:
                raise LostOwnership()
            msg = (f"Extracted {stats.get('char_count', 0):,} characters from {stats.get('page_count', 0)} page(s): "
                   f"{stats.get('token_count', 0):,} tokens in {stats.get('chunk_count', 0)} retrieval chunk(s).")
            if result.warnings:
                msg += f" {len(result.warnings)} warning(s)."
            add_event(session, uuid.UUID(job_id), "succeeded", msg, level="warning" if result.warnings else "info")
            job = session.get(Job, uuid.UUID(job_id))
            jobs.notify_terminal(job)
        return {"job_id": job_id, "status": "SUCCEEDED"}

    except LostOwnership:
        log.warning("job %s was reassigned while %s was processing it; discarding this attempt", job_id, worker_id)
        return {"job_id": job_id, "lost_ownership": True}
    except JobCancelled:
        _finish(job_id, worker_id, JobStatus.CANCELLED, None, "Cancelled by user request while processing.")
        return {"job_id": job_id, "status": "CANCELLED"}
    except ExtractionError as exc:
        _finish(job_id, worker_id, JobStatus.FAILED, exc.code, str(exc))
        return {"job_id": job_id, "status": "FAILED", "error_code": exc.code}
    except storage.ObjectMissingError as exc:
        _finish(
            job_id, worker_id, JobStatus.FAILED, "DOCUMENT_MISSING",
            f"The stored document is no longer available in object storage ({exc}). It may have been deleted by a "
            "retention policy; upload the file again.",
        )
        return {"job_id": job_id, "status": "FAILED", "error_code": "DOCUMENT_MISSING"}
    except Exception as exc:
        # The soft time limit can surface wrapped by native libraries (e.g. as an ONNXRuntimeError), so timeouts
        # are recognised by cause chain or elapsed time, before any retry decision.
        if _is_timeout(exc, started):
            _finish(
                job_id, worker_id, JobStatus.FAILED, "TIMEOUT",
                f"Processing exceeded the time limit ({settings.job_soft_time_limit_s}s). Large scanned documents "
                "are slow to OCR: split the document, use ocr_engine=tesseract (faster), or ocr_mode=off if it has "
                "a text layer.",
            )
            return {"job_id": job_id, "status": "FAILED", "error_code": "TIMEOUT"}
        if isinstance(exc, (storage.StorageError, OperationalError, OSError)):
            _transient_failure(job_id, worker_id, claimed, "INFRASTRUCTURE_ERROR", exc)
            return {"job_id": job_id, "status": "RETRYING"}
        if isinstance(exc, ValueError):  # invalid options that slipped past API validation (e.g. OCR language)
            _finish(job_id, worker_id, JobStatus.FAILED, "INVALID_OPTIONS", str(exc))
            return {"job_id": job_id, "status": "FAILED", "error_code": "INVALID_OPTIONS"}
        log.exception("unexpected error processing job %s", job_id)
        _transient_failure(job_id, worker_id, claimed, "INTERNAL_ERROR", exc)
        return {"job_id": job_id, "status": "RETRYING"}
    finally:
        heartbeat.stop_event.set()


def _is_timeout(exc: BaseException, started: float) -> bool:
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, SoftTimeLimitExceeded) or "SoftTimeLimitExceeded" in str(seen):
            return True
        seen = seen.__cause__ or seen.__context__
    return time.perf_counter() - started >= settings.job_soft_time_limit_s


def _merge_warnings(session, job_id: str, new: list[str]) -> list[str]:
    existing = session.get(Job, uuid.UUID(job_id)).warnings or []
    return existing + [w for w in new if w not in existing]


def _finish(job_id: str, worker_id: str, status: JobStatus, code: str | None, message: str) -> None:
    with session_scope() as session:
        job = session.get(Job, uuid.UUID(job_id), with_for_update=True)
        if job is None or job.worker_id != worker_id or job.status != JobStatus.PROCESSING:
            return
        if status == JobStatus.FAILED:
            jobs.mark_failed(session, job, code, message)
        else:
            job.status = status
            job.stage = status.value.lower()
            job.stage_detail = None
            job.finished_at = utcnow()
            add_event(session, job.id, status.value.lower(), message, level="warning")
            jobs.notify_terminal(job)


def _transient_failure(job_id: str, worker_id: str, claimed, code: str, exc: Exception) -> None:
    """Retry with exponential backoff while attempts remain, then fail with a clear explanation."""
    detail = f"{type(exc).__name__}: {str(exc)[:300]}"
    try:
        with session_scope() as session:
            job = session.get(Job, uuid.UUID(job_id), with_for_update=True)
            if job is None or job.worker_id != worker_id or job.status != JobStatus.PROCESSING:
                return
            if job.attempts >= job.max_attempts:
                jobs.mark_failed(
                    session, job, code,
                    f"Processing failed on all {job.max_attempts} attempts. Last error: {detail}",
                )
                return
            delay = settings.retry_backoff_base_s * 2 ** (job.attempts - 1)
            job.status = JobStatus.RETRYING
            job.stage = "retrying"
            job.stage_detail = f"attempt {job.attempts + 1}/{job.max_attempts} in {delay}s"
            job.worker_id = None
            job.next_retry_at = utcnow() + timedelta(seconds=delay)
            add_event(
                session, job.id, "retry_scheduled",
                f"Temporary problem ({detail}). Retrying automatically in {delay}s "
                f"(attempt {job.attempts + 1}/{job.max_attempts}).",
                level="warning", error_code=code,
            )
            jobs.dispatch(session, job, reason="automatic retry", countdown=delay)
    except Exception:
        # Even the DB is down: the heartbeat stops, and the sweeper will recover the job later.
        log.exception("could not record transient failure for job %s", job_id)


@celery_app.task(name="docintel.recover_jobs")
def recover_jobs_task() -> dict:
    return jobs.recover_jobs()


@celery_app.task(
    name="docintel.send_webhook",
    bind=True,
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=5,
    retry_backoff_max=300,
    max_retries=6,
)
def send_webhook(self, job_id: str) -> dict:
    from app.api.schemas import job_to_out

    with session_scope() as session:
        job = session.get(Job, uuid.UUID(job_id))
        if job is None or not job.callback_url:
            return {"skipped": True}
        payload = job_to_out(job).model_dump(mode="json")
        url = job.callback_url
    response = httpx.post(url, json={"event": f"job.{payload['status'].lower()}", "job": payload},
                          timeout=settings.webhook_timeout_s)
    response.raise_for_status()
    with session_scope() as session:
        add_event(session, uuid.UUID(job_id), "webhook_delivered", f"Callback delivered to {url} ({response.status_code}).")
    return {"status_code": response.status_code}
