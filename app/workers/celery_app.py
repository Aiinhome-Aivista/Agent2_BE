"""Celery worker — async remediation tasks.

The synchronous orchestrator handles the immediate response, but long-running
remediation jobs (multi-step infrastructure changes) should be offloaded to
Celery workers so the request thread doesn't block.
"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "incident_agent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks", "app.workers.sync_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes hard limit
    task_soft_time_limit=240,
)

# Initialize beat_schedule safely if not already present.
if not hasattr(celery_app.conf, "beat_schedule") or celery_app.conf.beat_schedule is None:
    celery_app.conf.beat_schedule = {}

# Ensure periodic tasks are registered by importing the sync_tasks module.
# This must happen after the app and beat_schedule are initialized.
from app.workers import sync_tasks  # noqa: E402, F401
