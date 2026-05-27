
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from app.connectors.base.connector import IncidentPayload
from app.core.logger import logger
from app.db import get_db
from app.repositories.incident_repository import IncidentRepository

def upsert_from_zoho_ticket(connector_id: str, payload: IncidentPayload) -> str:
    """Take a Zoho Ticket payload and upsert a local incident."""
    external_id = payload.external_id
    external_key = payload.external_key

    # Look up existing sync link
    link = _find_sync_state(connector_id, "ticket", external_id)

    if link and link.get("internal_id"):
        # ---- Update existing local incident -------------------------------
        incident_id = link["internal_id"]
        IncidentRepository.update(incident_id, {
            "subject": payload.subject,
            "description": payload.description,
            "priority": payload.priority,
            "status": payload.status,
            "category": payload.category,
            "source": "zoho",
        })
        _update_sync_state(link["id"])
        logger.info(f"[zoho-sync] updated incident {incident_id} from {external_key}")
        return incident_id

    # ---- Create a new local incident ----------------------------------------
    local_payload = {
        "subject": payload.subject[:200] or f"Zoho ticket {external_key}",
        "description": payload.description or "(no description)",
        "caller": payload.caller or "Zoho sync",
        "caller_email": payload.caller_email,
        "source": "zoho",
        "priority": payload.priority or "P3",
        "category": payload.category or "Uncategorised",
        "status": payload.status or "new",
    }
    incident_id = IncidentRepository.create(local_payload)

    _create_sync_state(
        connector_id=connector_id,
        external_id=external_id,
        external_key=external_key,
        internal_id=incident_id,
    )
    logger.info(f"[zoho-sync] created incident {incident_id} from {external_key}")
    return incident_id

def _find_sync_state(connector_id: str, resource_type: str, external_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT * FROM connector_sync_state "
                "WHERE connector_id = %s AND resource_type = %s AND external_id = %s LIMIT 1",
                (connector_id, resource_type, external_id),
            )
            return cur.fetchone()

def _create_sync_state(
    connector_id: str,
    external_id: str,
    external_key: Optional[str],
    internal_id: Optional[str],
    resource_type: str = "ticket",
    direction: str = "bidirectional",
) -> str:
    sid = f"SYNC-{uuid.uuid4().hex[:12]}"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO connector_sync_state "
                "(id, connector_id, resource_type, external_id, external_key, internal_id, "
                " sync_direction, sync_status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'synced') "
                "ON DUPLICATE KEY UPDATE "
                "  external_key = VALUES(external_key), "
                "  internal_id  = COALESCE(VALUES(internal_id), internal_id), "
                "  sync_status  = 'synced', "
                "  last_synced_at = CURRENT_TIMESTAMP",
                (sid, connector_id, resource_type, external_id, external_key, internal_id, direction),
            )
        conn.commit()
    return sid

def _update_sync_state(sync_id: str) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE connector_sync_state SET last_synced_at = CURRENT_TIMESTAMP WHERE id = %s",
                (sync_id,),
            )
        conn.commit()
