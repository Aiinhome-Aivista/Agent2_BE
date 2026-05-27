"""Async tasks executed by Celery workers."""
from typing import Any, Dict

from app.agents import orchestrator
from app.core.logger import logger
from app.repositories import IncidentRepository
from app.workers.celery_app import celery_app


@celery_app.task(name="incidents.process_async", bind=True, max_retries=2)
def process_incident_async(self, incident_id: str) -> Dict[str, Any]:
    """Run the agent pipeline asynchronously — useful for monitoring webhooks
    that don't want to wait for the full pipeline to complete."""
    incident = IncidentRepository.find_by_id(incident_id)
    if not incident:
        logger.warning(f"[celery] incident {incident_id} not found")
        return {"status": "not_found", "id": incident_id}
    try:
        result = orchestrator.process_new(incident)
        return {"status": result.get("status"), "id": incident_id}
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[celery] failed processing {incident_id}: {exc}")
        raise self.retry(exc=exc, countdown=10)


@celery_app.task(name="sla.check_breaches")
def check_sla_breaches() -> Dict[str, Any]:
    """Periodic task — flips sla_breached on incidents past their deadline."""
    from datetime import datetime
    from app.db import get_db

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE incidents SET sla_breached = TRUE "
                "WHERE sla_breached = FALSE AND sla_deadline < %s "
                "AND status NOT IN ('resolved', 'closed')",
                (datetime.now(),),
            )
            affected = cur.rowcount
        conn.commit()
    if affected:
        logger.info(f"[celery] flagged {affected} SLA breaches")
    return {"flagged": affected}
