"""HTTP API: upload (claim check in), job tracking and results (claim check out)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app import jobs, storage
from app.api import stats as stats_module
from app.api.schemas import JobEventOut, JobList, JobOut, ResultOut, event_to_out, job_to_out
from app.config import settings
from app.db import ACTIVE_STATUSES, Document, Job, JobStatus, add_event, init_db, session_scope, utcnow
from app.extraction.formats import UnsupportedFormatError, detect_format, supported_formats_summary
from app.extraction.ocr import ENGINE_DESCRIPTIONS, available_engines, installed_languages

log = logging.getLogger("docintel.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, **details):
        super().__init__(status_code=status_code, detail={"code": code, "message": message, **details})


@asynccontextmanager
async def lifespan(_: FastAPI):
    for attempt in range(30):  # dependencies may still be starting
        try:
            init_db()
            storage.ensure_buckets()
            break
        except Exception as exc:
            log.warning("waiting for postgres/object storage (%s): %s", attempt + 1, exc)
            time.sleep(2)
    yield


app = FastAPI(
    title="Document Intelligence Service",
    version="1.0.0",
    description=(
        "Asynchronous document-to-text pipeline. Upload a document, receive a job id immediately, "
        "then poll the job until it reaches a terminal state and fetch the extracted text."
    ),
    lifespan=lifespan,
)


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "INVALID_REQUEST", "message": "The request parameters are invalid.", "fields": errors}},
    )


@app.exception_handler(OperationalError)
async def db_down_handler(_: Request, exc: OperationalError):
    log.error("database unavailable: %s", exc)
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "10"},
        content={"error": {"code": "DATABASE_UNAVAILABLE",
                           "message": "The job database is temporarily unavailable. Please retry in a few seconds."}},
    )


@app.exception_handler(storage.StorageError)
async def storage_down_handler(_: Request, exc: storage.StorageError):
    log.error("object storage unavailable: %s", exc)
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "10"},
        content={"error": {"code": "STORAGE_UNAVAILABLE",
                           "message": "Document storage is temporarily unavailable. Please retry in a few seconds."}},
    )


# --------------------------------------------------------------------------- upload


def _options(language, ocr_mode, ocr_engine, extract_entities) -> dict:
    language = (language or settings.default_ocr_languages).strip().replace(",", "+")
    engine = ocr_engine or settings.default_ocr_engine
    if engine not in available_engines():
        raise ApiError(422, "INVALID_OCR_ENGINE", f"Unknown OCR engine '{engine}'.", available=available_engines())
    installed = installed_languages(engine)
    missing = [lang for lang in language.split("+") if lang not in installed]
    if missing:
        raise ApiError(422, "INVALID_LANGUAGE", f"OCR language(s) not available: {', '.join(missing)}.",
                       available=sorted(installed))
    return {"language": language, "ocr_mode": ocr_mode, "ocr_engine": engine, "extract_entities": extract_entities}


def _spool(upload: UploadFile) -> tuple[str, str, int]:
    """Copy the upload to a temp file, hashing it and enforcing the size limit while streaming."""
    digest = hashlib.sha256()
    size = 0
    fd, path = tempfile.mkstemp(prefix="upload-")
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise ApiError(413, "FILE_TOO_LARGE",
                                   f"The file exceeds the {settings.max_upload_mb} MB upload limit.",
                                   max_upload_mb=settings.max_upload_mb)
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        os.unlink(path)
        raise
    return path, digest.hexdigest(), size


@app.post("/v1/documents", response_model=JobOut, status_code=202, tags=["documents"],
          responses={200: {"description": "Identical document already processed with the same options (cached)"},
                     413: {"description": "File too large"}, 415: {"description": "Unsupported format"},
                     503: {"description": "Storage or database unavailable"}})
def upload_document(
    request: Request,
    file: UploadFile = File(..., description="The document to process"),
    language: str | None = Form(None, description="OCR language(s), tesseract codes joined by '+', e.g. 'eng+spa'"),
    ocr_mode: Literal["auto", "force", "off"] = Form(
        "auto", description="auto: OCR only pages without a text layer; force: OCR everything; off: never OCR"),
    ocr_engine: str | None = Form(None, description="OCR engine (see GET /v1/formats)"),
    extract_entities: bool = Form(True, description="Extract emails, URLs, dates, amounts and phones as metadata"),
    force: bool = Form(False, description="Re-process even if the same file was already processed with the same options"),
    client_reference: str | None = Form(None, max_length=256, description="Your own id, returned with the job"),
    callback_url: str | None = Form(None, max_length=1024, description="URL to POST the job to when it finishes"),
):
    filename = os.path.basename(file.filename or "upload")[: settings.max_filename_length]
    if callback_url and urlparse(callback_url).scheme not in ("http", "https"):
        raise ApiError(422, "INVALID_CALLBACK_URL", "callback_url must be an http(s) URL.")
    options = _options(language, ocr_mode, ocr_engine, extract_entities)

    path, sha256, size = _spool(file)
    try:
        if size == 0:
            raise ApiError(400, "EMPTY_FILE", "The uploaded file is empty.")
        try:
            fmt = detect_format(path, filename)
        except UnsupportedFormatError as exc:
            raise ApiError(
                415, "UNSUPPORTED_FORMAT",
                f"'{filename}' is not a supported document type (detected content: {exc.detected_mime}).",
                detected_mime=exc.detected_mime,
                supported_extensions=sorted({e for f in supported_formats_summary()["formats"] for e in f["extensions"]}),
            ) from exc

        key = storage.document_key(sha256, fmt.extension)
        if not storage.exists(settings.s3_bucket_documents, key):
            storage.upload_file(path, settings.s3_bucket_documents, key, fmt.mime,
                                metadata={"sha256": sha256, "original-filename": filename.encode("ascii", "ignore").decode()})
    finally:
        os.unlink(path)

    with session_scope() as session:
        document = Document(
            sha256=sha256, original_filename=filename, kind=fmt.kind, mime_type=fmt.mime, extension=fmt.extension,
            size_bytes=size, storage_bucket=settings.s3_bucket_documents, storage_key=key,
        )
        session.add(document)
        session.flush()
        job = Job(
            document_id=document.id, status=JobStatus.QUEUED, stage="queued", queue=fmt.queue, options=options,
            client_reference=client_reference, callback_url=callback_url, warnings=list(fmt.warnings),
            max_attempts=settings.max_attempts,
        )
        session.add(job)
        session.flush()
        add_event(session, job.id, "received",
                  f"Received '{filename}' ({size:,} bytes, detected as {fmt.kind} / {fmt.mime}); stored as {key}.",
                  sha256=sha256)
        for warning in fmt.warnings:
            add_event(session, job.id, "format_warning", warning, level="warning")

        cached = None if force else session.scalars(
            select(Job).join(Document)
            .where(Document.sha256 == sha256, Job.status == JobStatus.SUCCEEDED, Job.options == options,
                   Job.cached_from_job_id.is_(None))
            .order_by(Job.finished_at.desc()).limit(1)
        ).first()
        if cached:
            for attr in ("result_bucket", "result_text_key", "result_json_key", "text_preview", "char_count",
                         "word_count", "page_count", "metadata_"):
                setattr(job, attr, getattr(cached, attr))
            job.warnings = list(dict.fromkeys((job.warnings or []) + (cached.warnings or [])))
            job.status, job.stage, job.progress = JobStatus.SUCCEEDED, "done", 100.0
            job.cached_from_job_id = cached.id
            job.started_at = job.finished_at = utcnow()
            add_event(session, job.id, "cache_hit",
                      f"Identical file already processed with the same options (job {cached.id}); result reused. "
                      "Send force=true to process it again.")
            session.flush()
            session.refresh(job)
            return JSONResponse(status_code=200, content=job_to_out(job).model_dump(mode="json"),
                                headers={"Location": f"/v1/jobs/{job.id}"})

        jobs.dispatch(session, job, reason="new upload")
        session.flush()
        session.refresh(job)
        out = job_to_out(job)
    # The message is published right after the commit above; tell the client if the broker was down.
    if str(out.id) in session.info.get("dispatch_failed", []):
        out.warnings.append("The queue is temporarily unavailable. Your document is stored safely and will be "
                            "processed automatically as soon as the queue recovers.")
    return JSONResponse(status_code=202, content=out.model_dump(mode="json"), headers={"Location": out.links.self})


# --------------------------------------------------------------------------- tracking


def _get_job(session, job_id: uuid.UUID) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "JOB_NOT_FOUND", f"Job {job_id} does not exist.")
    return job


@app.get("/v1/jobs", response_model=JobList, tags=["jobs"])
def list_jobs(
    status: JobStatus | None = None,
    kind: str | None = None,
    client_reference: str | None = None,
    q: str | None = Query(None, description="Filename contains"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    with session_scope() as session:
        query = select(Job).join(Document)
        if status:
            query = query.where(Job.status == status)
        if kind:
            query = query.where(Document.kind == kind)
        if client_reference:
            query = query.where(Job.client_reference == client_reference)
        if q:
            query = query.where(Document.original_filename.ilike(f"%{q}%"))
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        items = session.scalars(query.order_by(Job.created_at.desc()).limit(limit).offset(offset)).all()
        return JobList(items=[job_to_out(j) for j in items], total=total, limit=limit, offset=offset)


@app.get("/v1/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
def get_job(job_id: uuid.UUID):
    with session_scope() as session:
        job = _get_job(session, job_id)
        out = job_to_out(job)
        if job.status == JobStatus.QUEUED:
            ahead = session.scalar(
                select(func.count()).select_from(Job)
                .where(Job.queue == job.queue, Job.status == JobStatus.QUEUED, Job.created_at < job.created_at)
            )
            out.stage_detail = f"{ahead} job(s) ahead in the '{job.queue}' queue"
        elif job.status == JobStatus.PROCESSING and job.heartbeat_at:
            silent = (utcnow() - job.heartbeat_at).total_seconds()
            if silent > settings.heartbeat_interval_s * 3:
                out.warnings = out.warnings + [
                    f"No heartbeat from the worker for {silent:.0f}s. If it crashed, the job will be re-queued "
                    f"automatically after {settings.stale_heartbeat_s}s."
                ]
        return out


@app.get("/v1/jobs/{job_id}/events", response_model=list[JobEventOut], tags=["jobs"])
def get_job_events(job_id: uuid.UUID):
    with session_scope() as session:
        job = _get_job(session, job_id)
        return [event_to_out(e) for e in job.events]


def _require_result(job: Job) -> None:
    if job.status == JobStatus.SUCCEEDED:
        return
    if job.status == JobStatus.FAILED:
        raise ApiError(409, "JOB_FAILED", f"The job failed: {job.error_message}", job_error_code=job.error_code)
    if job.status == JobStatus.CANCELLED:
        raise ApiError(409, "JOB_CANCELLED", "The job was cancelled; there is no result.")
    raise ApiError(409, "RESULT_NOT_READY", f"The job is {job.status.value} ({job.stage}, {job.progress:.0f}%). "
                   "Poll the job until status is SUCCEEDED.", status=job.status.value, progress=job.progress)


@app.get("/v1/jobs/{job_id}/result", response_model=ResultOut, tags=["results"])
def get_result(job_id: uuid.UUID, include_text: bool = True, include_pages: bool = True):
    with session_scope() as session:
        job = _get_job(session, job_id)
        _require_result(job)
        bucket, key = job.result_bucket, job.result_json_key
    payload = json.loads(storage.get_bytes(bucket, key))
    return ResultOut(
        job_id=job_id,
        text=payload["text"] if include_text else "",
        pages=[p if include_text else {k: v for k, v in p.items() if k != "text"} for p in payload["pages"]]
        if include_pages else [],
        metadata=payload["metadata"],
        warnings=payload["warnings"],
    )


@app.get("/v1/jobs/{job_id}/text", response_class=PlainTextResponse, tags=["results"])
def get_text(job_id: uuid.UUID, download: bool = False):
    with session_scope() as session:
        job = _get_job(session, job_id)
        _require_result(job)
        bucket, key, name = job.result_bucket, job.result_text_key, job.document.original_filename
    body, length, _ = storage.get_object_stream(bucket, key)
    headers = {"Content-Length": str(length)} if length is not None else {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{Path(name).stem}.txt"'
    return StreamingResponse(body.iter_chunks(), media_type="text/plain; charset=utf-8", headers=headers)


@app.get("/v1/jobs/{job_id}/document", tags=["results"])
def get_original(job_id: uuid.UUID):
    with session_scope() as session:
        doc = _get_job(session, job_id).document
        bucket, key, name, mime = doc.storage_bucket, doc.storage_key, doc.original_filename, doc.mime_type
    body, length, _ = storage.get_object_stream(bucket, key)
    safe = name.encode("ascii", "ignore").decode().replace('"', "")
    return StreamingResponse(body.iter_chunks(), media_type=mime,
                             headers={"Content-Disposition": f'inline; filename="{safe}"', "Content-Length": str(length)})


@app.post("/v1/jobs/{job_id}/cancel", response_model=JobOut, tags=["jobs"])
def cancel_job(job_id: uuid.UUID):
    with session_scope() as session:
        job = session.get(Job, job_id, with_for_update=True) or _get_job(session, job_id)
        if job.status not in ACTIVE_STATUSES:
            raise ApiError(409, "JOB_NOT_ACTIVE", f"The job is already {job.status.value}; it cannot be cancelled.")
        job.cancel_requested = True
        if job.status in (JobStatus.QUEUED, JobStatus.RETRYING):
            job.status, job.stage, job.finished_at = JobStatus.CANCELLED, "cancelled", utcnow()
            add_event(session, job.id, "cancelled", "Cancelled by user before processing started.", level="warning")
            jobs.notify_terminal(job)
        else:
            add_event(session, job.id, "cancel_requested",
                      "Cancellation requested; the worker will stop at the next checkpoint (page).", level="warning")
        session.flush()
        return job_to_out(job)


@app.post("/v1/jobs/{job_id}/retry", response_model=JobOut, status_code=202, tags=["jobs"])
def retry_job(job_id: uuid.UUID):
    with session_scope() as session:
        job = session.get(Job, job_id, with_for_update=True) or _get_job(session, job_id)
        if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
            raise ApiError(409, "JOB_NOT_RETRYABLE", f"Only FAILED or CANCELLED jobs can be retried (job is {job.status.value}).")
        previous = job.error_code
        job.status, job.stage, job.stage_detail, job.progress = JobStatus.QUEUED, "queued", None, 0.0
        job.attempts, job.cancel_requested, job.finished_at = 0, False, None
        job.error_code = job.error_message = None
        job.created_at = utcnow()  # restart the queue TTL
        add_event(session, job.id, "manual_retry", f"Manual retry requested (previous outcome: {previous or 'cancelled'}).")
        jobs.dispatch(session, job, reason="manual retry")
        session.flush()
        return job_to_out(job)


# --------------------------------------------------------------------------- system


@app.get("/v1/formats", tags=["system"])
def formats():
    summary = supported_formats_summary()
    summary["ocr"] = {
        "engines": {name: sorted(installed_languages(name)) for name in available_engines()},
        "engine_descriptions": ENGINE_DESCRIPTIONS,
        "default_engine": settings.default_ocr_engine,
        "default_languages": settings.default_ocr_languages,
        "modes": ["auto", "force", "off"],
    }
    return summary


@app.get("/health", tags=["system"])
def health():
    """Liveness: the API process is up."""
    return {"status": "ok"}


@app.get("/v1/system", tags=["system"])
def system_status():
    """Readiness of every dependency plus worker and queue information."""
    components = {}
    t = time.perf_counter()
    try:
        with session_scope() as session:
            session.execute(select(1))
        components["database"] = {"ok": True}
    except SQLAlchemyError as exc:
        components["database"] = {"ok": False, "error": str(exc)[:200]}
    components["object_storage"] = {"ok": storage.ping()}
    try:
        components["broker"] = {"ok": True, "queues": jobs.queue_depths()}
    except Exception as exc:
        components["broker"] = {"ok": False, "error": str(exc)[:200]}
    workers = stats_module.workers_info() if components["broker"]["ok"] else []
    components["workers"] = {"ok": bool(workers), "online": workers}
    healthy = all(c["ok"] for c in components.values())
    messages = []
    if not components["database"]["ok"]:
        messages.append("Database unavailable: uploads and status queries will fail until it recovers.")
    if not components["object_storage"]["ok"]:
        messages.append("Object storage unavailable: uploads and result downloads will fail until it recovers.")
    if not components["broker"]["ok"]:
        messages.append("Queue broker unavailable: uploads are accepted and will be dispatched when it recovers.")
    if components["broker"]["ok"] and not workers:
        messages.append("No workers online: jobs will wait in the queue until a worker starts.")
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "messages": messages, "components": components,
                 "checked_in_ms": int((time.perf_counter() - t) * 1000)},
    )


@app.get("/v1/stats", tags=["system"])
def stats(window_minutes: int = Query(60, ge=5, le=7 * 24 * 60)):
    return stats_module.collect(window_minutes)


# --------------------------------------------------------------------------- UI

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC_DIR / "dashboard.html")


# --------------------------------------------------------------------------- body size guard (outermost ASGI layer)


class _TooLarge(HTTPException):
    # An HTTPException so FastAPI's body parser re-raises it untouched and our handler renders a 413.
    def __init__(self):
        super().__init__(413, {"code": "FILE_TOO_LARGE",
                               "message": f"The request exceeds the {settings.max_upload_mb} MB upload limit.",
                               "max_upload_mb": settings.max_upload_mb})


class BodySizeLimit:
    """Reject oversized requests *before* the multipart body is buffered to disk."""

    def __init__(self, inner, max_bytes: int):
        self.inner, self.max_bytes = inner, max_bytes

    async def _reject(self, send):
        body = json.dumps({"error": {"code": "FILE_TOO_LARGE",
                                     "message": f"The request exceeds the {settings.max_upload_mb} MB upload limit.",
                                     "max_upload_mb": settings.max_upload_mb}}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.inner(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return await self._reject(send)
        received, started = 0, False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.inner(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(send)


asgi = BodySizeLimit(app, settings.max_upload_bytes + 1024 * 1024)  # + multipart envelope
