"""Periodic sync tasks for connectors.

Two scheduled tasks:

  * `connectors.poll_all` (every minute)
       Iterates over connectors with status='connected' AND sync_enabled=True
       and for each one whose `last_synced_at` is older than `poll_interval_sec`,
       enqueues `connectors.poll_one(connector_id)`.

  * `connectors.refresh_jira_webhooks` (daily)
       Atlassian dynamic webhooks expire every 30 days. This task lists all
       webhooks for each Jira connector and calls PUT /webhook/refresh.

Task `connectors.poll_one(connector_id)` is the unit of work — it calls
`ConnectorService.sync_now(connector_id)` and updates the audit log.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict

from celery.schedules import crontab

from app.core.logger import logger
from app.repositories.connector_repository import ConnectorRepository
from app.services.connector_service import ConnectorService
from app.workers.celery_app import celery_app


# --------------------------------------------------------------------- helpers
def _is_due(connector: Dict[str, Any], now: datetime) -> bool:
    """A connector is 'due' for polling if last_synced_at is older than its
    configured poll_interval_sec."""
    interval = int(connector.get("poll_interval_sec") or 120)
    last = connector.get("last_synced_at")
    if last is None:
        return True
    if isinstance(last, str):
        try:
            last = datetime.fromisoformat(last.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return True
    return (now - last) >= timedelta(seconds=interval)


# --------------------------------------------------------------------- tasks
@celery_app.task(name="connectors.poll_all")
def poll_all() -> Dict[str, int]:
    """Scheduler task — fans out to per-connector polls."""
    now = datetime.now()
    connectors = ConnectorRepository.list(status="connected")
    enqueued = 0
    for c in connectors:
        if not c.get("sync_enabled", True):
            continue
        if _is_due(c, now):
            poll_one.delay(c["id"])  # type: ignore[attr-defined]
            enqueued += 1
    logger.info(f"[connectors.poll_all] enqueued {enqueued} of {len(connectors)} connectors")
    return {"checked": len(connectors), "enqueued": enqueued}


@celery_app.task(name="connectors.poll_one", bind=True, max_retries=2, default_retry_delay=60)
def poll_one(self, connector_id: str) -> Dict[str, Any]:
    try:
        result = ConnectorService.sync_now(connector_id)
        logger.info(f"[connectors.poll_one] {connector_id} -> {result}")
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[connectors.poll_one] {connector_id} failed: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(name="connectors.refresh_jira_webhooks")
def refresh_jira_webhooks() -> Dict[str, Any]:
    """Atlassian dynamic webhooks expire after 30 days. We refresh weekly,
    well within the window."""
    from app.connectors import instantiate

    summary = {"refreshed": 0, "errors": 0}
    jira_connectors = ConnectorRepository.list(provider="jira", status="connected")
    for c in jira_connectors:
        try:
            connector = instantiate("jira", c["id"], c.get("config") or {})
            api = connector.api()  # type: ignore[attr-defined]
            existing = api.list_webhooks()
            ids = [w["id"] for w in existing.get("values", [])]
            if ids:
                api.refresh_webhooks(ids)
                summary["refreshed"] += len(ids)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[refresh_jira_webhooks] {c['id']} failed: {e}")
            summary["errors"] += 1
    return summary


# --------------------------------------------------------------------- beat
# Adds entries to celery_app.conf.beat_schedule when this module is imported.
celery_app.conf.beat_schedule.update({
    "connectors-poll-all": {
        "task": "connectors.poll_all",
        "schedule": 60.0,                # every minute → respects per-connector intervals
    },
    "connectors-refresh-jira-webhooks": {
        "task": "connectors.refresh_jira_webhooks",
        "schedule": crontab(hour=3, minute=0),  # daily at 03:00
    },
})
