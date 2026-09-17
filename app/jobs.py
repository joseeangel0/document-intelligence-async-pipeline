"""Job lifecycle operations shared by the API, the workers and the recovery sweeper."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

import redis
from sqlalchemy import event, or_, select, text, update

from app.config import settings
from app.db import Job, JobStatus, add_event, session_scope, utcnow
from app.worker.celery_app import celery_app

log = logging.getLogger(__name__)

PROCESS_TASK = "docintel.process_document"
QUEUES = ("light", "heavy", "layout")


def dispatch(session, job: Job, reason: str = "queued", countdown: int | None = None) -> None:
    """Publish the claim check (just the job id) to the broker *after* the transaction commits.

    Publishing before the commit would let a fast worker receive the message while the job row is
    still invisible to it (it would drop the message as "not claimable"). Failure to publish is not
    fatal either: the row is durable and the recovery sweeper re-dispatches any QUEUED job that is
    missing from the broker.
    """
    job.dispatch_count += 1
    job.last_dispatched_at = utcnow()
    task_id = f"{job.id}.{job.dispatch_count}"
    add_event(session, job.id, "dispatched", f"Sent to the '{job.queue}' queue ({reason}).", queue=job.queue, task_id=task_id)
    pending = session.info.setdefault("pending_dispatch", [])
    if not pending:
        event.listen(session, "after_commit", _publish_pending, once=True)
        event.listen(session, "after_rollback", _discard_pending, once=True)
    pending.append((str(job.id), job.queue, task_id, countdown))


def _discard_pending(session) -> None:
    session.info.pop("pending_dispatch", None)


def _publish_pending(session) -> None:
    failed = []
    for job_id, queue, task_id, countdown in session.info.pop("pending_dispatch", []):
        try:
            celery_app.send_task(
                PROCESS_TASK,
                kwargs={"job_id": job_id},
                queue=queue,
                task_id=task_id,
                countdown=countdown,
                retry=True,
                retry_policy={"max_retries": 2, "interval_start": 0, "interval_step": 0.5, "interval_max": 1},
            )
        except Exception as exc:  # broker down
            log.warning("dispatch of job %s failed: %s", job_id, exc)
            failed.append((job_id, str(exc)[:500]))
    session.info["dispatch_failed"] = [job_id for job_id, _ in failed]
    if not failed:
        return
    try:
        with session_scope() as other:
            for job_id, error in failed:
                other.execute(update(Job).where(Job.id == uuid.UUID(job_id)).values(last_dispatched_at=None))
                add_event(
                    other, uuid.UUID(job_id), "dispatch_failed",
                    "The queue broker is unreachable. The job is safely stored and will be dispatched "
                    "automatically as soon as the broker is back.",
                    level="warning", error=error,
                )
    except Exception:
        log.exception("could not record dispatch failure (the sweeper will still recover the jobs)")


# --------------------------------------------------------------------------- broker inspection


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, socket_timeout=3, socket_connect_timeout=3)


def _job_id_from_message(raw: bytes | str) -> str | None:
    try:
        payload = json.loads(raw)
        if isinstance(payload, list):  # unacked entries are [message, exchange, routing_key]
            payload = payload[0]
        task_id = payload.get("headers", {}).get("id", "")
        return task_id.split(".")[0] if task_id else None
    except (ValueError, AttributeError, IndexError, TypeError):
        return None


def broker_job_ids() -> set[str]:
    """Job ids that currently have a message in Redis (waiting, reserved/unacked or scheduled)."""
    r = _redis()
    ids: set[str] = set()
    for queue in QUEUES:
        for key in r.scan_iter(match=f"{queue}*"):
            if r.type(key) == b"list":
                ids.update(filter(None, (_job_id_from_message(m) for m in r.lrange(key, 0, -1))))
    ids.update(filter(None, (_job_id_from_message(m) for m in r.hvals("unacked"))))
    return ids


def queue_depths() -> dict[str, int]:
    r = _redis()
    depths = {}
    for queue in (*QUEUES, "maintenance"):
        depths[queue] = sum(r.llen(k) for k in r.scan_iter(match=f"{queue}*") if r.type(k) == b"list")
    depths["reserved_or_scheduled"] = r.hlen("unacked")
    return depths


# --------------------------------------------------------------------------- recovery sweeper


def recover_jobs() -> dict:
    """Guarantee that every job eventually reaches a terminal state.

    * PROCESSING jobs whose heartbeat stopped (worker killed, container restarted, OOM) are
      re-queued, or failed once they exhausted their attempts (poison documents).
    * QUEUED/RETRYING jobs whose message is not in the broker (Redis flushed/restarted, publish
      failed) are re-dispatched. Duplicated messages are harmless: claiming a job is atomic.
    * Jobs waiting longer than the queue TTL are failed with an explicit reason.
    """
    stats = {"requeued": 0, "failed": 0, "redispatched": 0, "expired": 0, "broker_checked": False}
    now = utcnow()
    with session_scope() as session:
        # Only one sweeper at a time (beat + every worker on startup may call this).
        if not session.execute(text("SELECT pg_try_advisory_xact_lock(777001)")).scalar():
            stats["skipped"] = "another sweeper is running"
            return stats

        stale_before = now - timedelta(seconds=settings.stale_heartbeat_s)
        stale = session.scalars(
            select(Job)
            .where(Job.status == JobStatus.PROCESSING, or_(Job.heartbeat_at.is_(None), Job.heartbeat_at < stale_before))
            .with_for_update(skip_locked=True)
        ).all()
        for job in stale:
            silent_for = int((now - (job.heartbeat_at or job.started_at or job.created_at)).total_seconds())
            if job.cancel_requested:
                # The user asked to cancel and the worker died before reaching a checkpoint: honour the request
                # instead of re-queueing a job that no worker would ever claim.
                job.status, job.stage, job.stage_detail, job.finished_at = JobStatus.CANCELLED, "cancelled", None, now
                add_event(session, job.id, "cancelled",
                          f"Cancelled: cancellation was requested and the worker stopped responding "
                          f"(last heartbeat {silent_for}s ago).", level="warning")
                notify_terminal(job)
                stats["cancelled"] = stats.get("cancelled", 0) + 1
            elif job.attempts >= job.max_attempts:
                mark_failed(
                    session, job, "WORKER_LOST",
                    f"The worker processing this document stopped responding (last heartbeat {silent_for}s ago) "
                    f"on {job.attempts} of {job.max_attempts} attempts. This usually means the document makes the "
                    "worker crash or run out of memory. Giving up.",
                )
                stats["failed"] += 1
            else:
                job.status = JobStatus.QUEUED
                job.stage = "requeued"
                job.stage_detail = f"worker lost, waiting for attempt {job.attempts + 1}/{job.max_attempts}"
                add_event(
                    session, job.id, "worker_lost",
                    f"Worker '{job.worker_id or 'unknown'}' stopped responding (last heartbeat {silent_for}s ago; "
                    f"crash, restart or out-of-memory). Re-queued for attempt {job.attempts + 1}/{job.max_attempts}.",
                    level="warning",
                )
                job.worker_id = None
                dispatch(session, job, reason="recovered after worker loss")
                stats["requeued"] += 1

        expired = session.scalars(
            select(Job)
            .where(
                Job.status.in_([JobStatus.QUEUED, JobStatus.RETRYING]),
                Job.created_at < now - timedelta(seconds=settings.queued_ttl_s),
            )
            .with_for_update(skip_locked=True)
        ).all()
        for job in expired:
            mark_failed(
                session, job, "EXPIRED",
                f"The job waited more than {settings.queued_ttl_s // 3600} h without being processed "
                "(no workers available?). Upload the document again or retry the job.",
            )
            stats["expired"] += 1

        try:
            in_broker = broker_job_ids()
            stats["broker_checked"] = True
        except redis.RedisError as exc:
            log.warning("recovery: broker not reachable, skipping re-dispatch check: %s", exc)
            in_broker = None

        if in_broker is not None:
            waiting = session.scalars(
                select(Job)
                .where(
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RETRYING]),
                    or_(Job.last_dispatched_at.is_(None),
                        Job.last_dispatched_at < now - timedelta(seconds=settings.redispatch_after_s)),
                    or_(Job.next_retry_at.is_(None), Job.next_retry_at < now),
                )
                .with_for_update(skip_locked=True)
                .limit(500)
            ).all()
            for job in waiting:
                if str(job.id) in in_broker:
                    continue
                if job.status == JobStatus.RETRYING:
                    job.status = JobStatus.QUEUED
                add_event(
                    session, job.id, "message_missing",
                    "The job was waiting but its message was not in the queue (broker restart or publish "
                    "failure). Re-dispatching it.",
                    level="warning",
                )
                dispatch(session, job, reason="recovered missing queue message")
                stats["redispatched"] += 1
    if any(stats[k] for k in ("requeued", "failed", "redispatched", "expired")):
        log.warning("recovery sweep: %s", stats)
    return stats


# --------------------------------------------------------------------------- state transitions


def mark_failed(session, job: Job, code: str, message: str) -> None:
    job.status = JobStatus.FAILED
    job.stage = "failed"
    job.stage_detail = None
    job.error_code = code
    job.error_message = message
    job.finished_at = utcnow()
    add_event(session, job.id, "failed", message, level="error", error_code=code)
    notify_terminal(job)


def notify_terminal(job: Job) -> None:
    if job.callback_url:
        try:
            celery_app.send_task("docintel.send_webhook", kwargs={"job_id": str(job.id)}, countdown=1)
        except Exception as exc:
            log.warning("could not schedule webhook for job %s: %s", job.id, exc)


def claim(job_id: str, worker_id: str) -> ClaimedJob | None:
    """Atomically move QUEUED/RETRYING -> PROCESSING. Returns None if someone else owns the job."""
    now = utcnow()
    with session_scope() as session:
        claimed = session.execute(
            update(Job)
            .where(
                Job.id == uuid.UUID(job_id),
                Job.status.in_([JobStatus.QUEUED, JobStatus.RETRYING]),
                Job.cancel_requested.is_(False),
                Job.attempts < Job.max_attempts,
            )
            .values(
                status=JobStatus.PROCESSING,
                attempts=Job.attempts + 1,
                worker_id=worker_id,
                started_at=now,
                heartbeat_at=now,
                next_retry_at=None,
                stage="starting",
                stage_detail=None,
                progress=0.0,
                error_code=None,
                error_message=None,
                updated_at=now,
            )
            .returning(Job.id)
        ).first()
        if not claimed:
            return None
        job = session.get(Job, uuid.UUID(job_id))
        add_event(
            session, job.id, "started",
            f"Processing started on {worker_id} (attempt {job.attempts}/{job.max_attempts}).",
            worker=worker_id, attempt=job.attempts,
        )
        doc = job.document
        return ClaimedJob(
            id=str(job.id), attempts=job.attempts, max_attempts=job.max_attempts, options=dict(job.options or {}),
            kind=doc.kind, filename=doc.original_filename, extension=doc.extension, sha256=doc.sha256,
            size_bytes=doc.size_bytes, bucket=doc.storage_bucket, key=doc.storage_key, queue=job.queue,
        )


@dataclass
class ClaimedJob:
    id: str
    attempts: int
    max_attempts: int
    options: dict
    kind: str
    filename: str
    extension: str
    sha256: str
    size_bytes: int
    bucket: str
    key: str
    queue: str
