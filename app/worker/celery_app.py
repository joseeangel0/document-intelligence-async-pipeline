"""Celery application. Redis is only the transport; Postgres is the source of truth for job state."""

from celery import Celery
from kombu import Exchange, Queue

from app.config import settings

celery_app = Celery("docintel", broker=settings.redis_url, include=["app.worker.tasks"])

celery_app.conf.update(
    task_default_queue="light",
    # Explicit exchange/routing key per queue, otherwise every queue would bind to the default one.
    task_queues=[Queue(name, Exchange(name, type="direct"), routing_key=name) for name in ("light", "heavy", "maintenance")],
    task_default_exchange="light",
    task_default_routing_key="light",
    task_routes={
        "docintel.recover_jobs": {"queue": "maintenance"},
        "docintel.send_webhook": {"queue": "maintenance"},
    },
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,  # results live in Postgres + object storage, not in Redis
    # --- At-least-once delivery ---
    task_acks_late=True,  # ack only after the task body finished
    task_reject_on_worker_lost=True,  # child killed (OOM/SIGKILL) => message goes back to the queue
    worker_prefetch_multiplier=1,  # a worker never hoards messages it cannot start
    broker_transport_options={
        "visibility_timeout": settings.job_hard_time_limit_s + 300,  # unacked msgs are redelivered after this
    },
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=None,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_soft_time_limit=settings.job_soft_time_limit_s,
    task_time_limit=settings.job_hard_time_limit_s,
    worker_max_tasks_per_child=200,  # contain leaks from native libs (MuPDF, Tesseract)
    worker_send_task_events=True,  # for Flower
    task_send_sent_event=True,
    beat_schedule={
        "recover-stuck-jobs": {
            "task": "docintel.recover_jobs",
            "schedule": float(settings.reaper_interval_s),
            "options": {"expires": settings.reaper_interval_s},
        }
    },
    timezone="UTC",
)
