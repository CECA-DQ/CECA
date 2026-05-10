from celery import Celery

from src.config import settings

celery_app = Celery(
    "ceca",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["src.workers.pipeline_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Europe/Madrid",
    enable_utc=True,
    # Retry failed tasks up to 3 times with exponential backoff
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_retry_delay=60,
    task_max_retries=3,
)
