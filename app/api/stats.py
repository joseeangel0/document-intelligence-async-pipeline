"""Aggregations for the monitoring dashboard."""

from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import case, func, select, text

from app import jobs
from app.config import settings
from app.db import Document, Job, JobEvent, JobStatus, session_scope, utcnow
from app.worker.celery_app import celery_app

_workers_cache: dict = {"at": 0.0, "value": []}


def workers_info() -> list[dict]:
    """Online Celery workers with the queues they consume and what they are running (cached 3s)."""
    if time.monotonic() - _workers_cache["at"] < 3:
        return _workers_cache["value"]
    inspector = celery_app.control.inspect(timeout=1.0)
    try:
        active = inspector.active() or {}
        queues = inspector.active_queues() or {}
        stats = inspector.stats() or {}
    except Exception:
        active, queues, stats = {}, {}, {}
    value = [
        {
            "name": name,
            "queues": sorted(q["name"] for q in queues.get(name, [])),
            "active_tasks": len(active.get(name, [])),
            "concurrency": stats.get(name, {}).get("pool", {}).get("max-concurrency"),
            "processed": sum((stats.get(name, {}).get("total") or {}).values()),
        }
        for name in sorted(set(active) | set(queues) | set(stats))
    ]
    _workers_cache.update(at=time.monotonic(), value=value)
    return value


def collect(window_minutes: int) -> dict:
    now = utcnow()
    since = now - timedelta(minutes=window_minutes)
    bucket_s = 60 if window_minutes <= 120 else 300 if window_minutes <= 720 else 3600

    with session_scope() as session:
        status_counts = dict(session.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        status_counts = {s.value: status_counts.get(s, 0) for s in JobStatus}

        window = session.execute(
            select(
                func.count().label("created"),
                func.count().filter(Job.status == JobStatus.SUCCEEDED).label("succeeded"),
                func.count().filter(Job.status == JobStatus.FAILED).label("failed"),
                func.count().filter(Job.cached_from_job_id.is_not(None)).label("cache_hits"),
                func.avg(func.extract("epoch", Job.started_at - Job.created_at))
                .filter(Job.cached_from_job_id.is_(None)).label("avg_wait"),
                func.percentile_cont(0.5).within_group(func.extract("epoch", Job.finished_at - Job.started_at))
                .filter(Job.status == JobStatus.SUCCEEDED, Job.cached_from_job_id.is_(None)).label("p50_duration"),
                func.percentile_cont(0.95).within_group(func.extract("epoch", Job.finished_at - Job.started_at))
                .filter(Job.status == JobStatus.SUCCEEDED, Job.cached_from_job_id.is_(None)).label("p95_duration"),
                func.sum(Job.page_count).filter(Job.status == JobStatus.SUCCEEDED).label("pages"),
                func.sum(Job.char_count).filter(Job.status == JobStatus.SUCCEEDED).label("chars"),
                func.sum(Job.token_count).filter(Job.status == JobStatus.SUCCEEDED).label("tokens"),
                func.sum(Job.chunk_count).filter(Job.status == JobStatus.SUCCEEDED).label("chunks"),
                func.count().filter(func.jsonb_array_length(Job.warnings) > 0).label("with_warnings"),
            ).where(Job.created_at >= since)
        ).one()._asdict()

        by_kind = [
            dict(row._mapping)
            for row in session.execute(
                select(
                    Document.kind,
                    func.count().label("jobs"),
                    func.count().filter(Job.status == JobStatus.SUCCEEDED).label("succeeded"),
                    func.count().filter(Job.status == JobStatus.FAILED).label("failed"),
                    func.avg(func.extract("epoch", Job.finished_at - Job.started_at))
                    .filter(Job.status == JobStatus.SUCCEEDED, Job.cached_from_job_id.is_(None)).label("avg_duration_s"),
                    func.sum(Job.page_count).label("pages"),
                )
                .join(Document)
                .where(Job.created_at >= since)
                .group_by(Document.kind)
                .order_by(func.count().desc())
            )
        ]

        bucket = func.to_timestamp(func.floor(func.extract("epoch", Job.finished_at) / bucket_s) * bucket_s)
        timeline_rows = session.execute(
            select(
                bucket.label("bucket_start"),
                func.count().filter(Job.status == JobStatus.SUCCEEDED).label("succeeded"),
                func.count().filter(Job.status == JobStatus.FAILED).label("failed"),
            )
            .where(Job.finished_at >= since)
            .group_by("bucket_start")
            .order_by("bucket_start")
        ).all()
        timeline = {int(r.bucket_start.timestamp()): {"succeeded": r.succeeded, "failed": r.failed} for r in timeline_rows}
        start = int(since.timestamp()) // bucket_s * bucket_s
        series = [
            {"t": t, **timeline.get(t, {"succeeded": 0, "failed": 0})}
            for t in range(start, int(now.timestamp()) + 1, bucket_s)
        ]

        errors = [
            {"code": code, "count": count}
            for code, count in session.execute(
                select(Job.error_code, func.count())
                .where(Job.status == JobStatus.FAILED, Job.finished_at >= since)
                .group_by(Job.error_code)
                .order_by(func.count().desc())
            ).all()
        ]

        reliability = dict(
            session.execute(
                select(JobEvent.event, func.count())
                .where(JobEvent.created_at >= since,
                       JobEvent.event.in_(["worker_lost", "message_missing", "retry_scheduled", "dispatch_failed",
                                           "manual_retry", "cancelled"]))
                .group_by(JobEvent.event)
            ).all()
        )

        active = [
            {
                "id": str(j.id), "filename": j.document.original_filename, "kind": j.document.kind,
                "status": j.status.value, "stage": j.stage, "stage_detail": j.stage_detail, "progress": j.progress,
                "attempts": j.attempts, "worker": j.worker_id,
                "heartbeat_age_s": round((now - j.heartbeat_at).total_seconds(), 1) if j.heartbeat_at else None,
                "created_at": j.created_at.isoformat(),
            }
            for j in session.scalars(
                select(Job).where(Job.status.in_([JobStatus.PROCESSING, JobStatus.RETRYING, JobStatus.QUEUED]))
                .order_by(case((Job.status == JobStatus.PROCESSING, 0), else_=1), Job.created_at).limit(25)
            )
        ]

        recent_failures = [
            {"id": str(j.id), "filename": j.document.original_filename, "error_code": j.error_code,
             "error_message": j.error_message, "finished_at": j.finished_at.isoformat() if j.finished_at else None}
            for j in session.scalars(
                select(Job).where(Job.status == JobStatus.FAILED).order_by(Job.finished_at.desc()).limit(8)
            )
        ]
        db_size = session.execute(text("SELECT pg_database_size(current_database())")).scalar()

    try:
        queues = jobs.queue_depths()
        broker_ok = True
    except Exception:
        queues, broker_ok = {}, False

    def _round(value):
        return round(float(value), 2) if value is not None else None

    return {
        "generated_at": now.isoformat(),
        "window_minutes": window_minutes,
        "bucket_seconds": bucket_s,
        "status_counts": status_counts,
        "window": {k: _round(v) if k.startswith(("avg", "p50", "p95")) else int(v or 0) for k, v in window.items()},
        "by_kind": [{**k, "avg_duration_s": _round(k["avg_duration_s"]), "pages": k["pages"] or 0} for k in by_kind],
        "timeline": series,
        "errors": errors,
        "reliability_events": reliability,
        "active_jobs": active,
        "recent_failures": recent_failures,
        "queues": queues,
        "broker_ok": broker_ok,
        "workers": workers_info() if broker_ok else [],
        "database_size_bytes": db_size,
        "links": {"flower": settings.flower_url, "minio_console": settings.minio_console_url, "api_docs": "/docs"},
    }
