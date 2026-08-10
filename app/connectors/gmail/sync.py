"""Email (IMAP) ↔ local incident sync.

Inbound: IMAP poll → upsert local incident
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.logger import logger
from app.db import get_db
from app.repositories import IncidentRepository


def upsert_from_email(connector_id: str, email_data: Dict[str, Any]) -> str:
    """Take an email dictionary and upsert a local incident. Returns the local incident_id."""
    external_id = email_data["message_id"]

    # Look up existing sync link
    link = _find_sync_state(connector_id, "email", external_id)

    if link and link.get("internal_id"):
        # We don't typically update an existing incident from the same email,
        # but if we do, here is the logic.
        incident_id = link["internal_id"]
        _update_sync_state(
            link["id"],
            external_updated_at=datetime.now(),
            sync_status="synced"
        )
        logger.info(f"[gmail-sync] skipped update for already synced email {incident_id} (msg: {external_id})")
        return incident_id

    # Create a new local incident
    payload = {
        "subject": email_data.get("subject", "No Subject")[:200],
        "description": email_data.get("body", "(no content)"),
        "caller": email_data.get("from_name", "Unknown"),
        "caller_email": email_data.get("from_email", "unknown@example.com"),
        "source": "email",
        "priority": "P3",
        "category": "Email Request",
        "tags": ["email"]
    }
    incident_id = IncidentRepository.create(payload)

    _create_sync_state(
        connector_id=connector_id,
        external_id=external_id,
        external_key=external_id[:50],  # using truncated msg id as key
        internal_id=incident_id,
        resource_type="email",
    )
    logger.info(f"[gmail-sync] created incident {incident_id} from email {external_id}")

    try:
        from app.agents.orchestrator import orchestrator
        fresh = IncidentRepository.find_by_id(incident_id) or payload | {"id": incident_id}
        orchestrator.process_new(fresh)
        logger.info(f"[gmail-sync] orchestrator processed {incident_id}")
    except Exception as e:
        logger.exception(f"[gmail-sync] orchestrator failed on {incident_id}: {e}")

    return incident_id


# Helper methods for sync state
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
    resource_type: str = "email",
    direction: str = "inbound",
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


def _update_sync_state(
    sync_id: str,
    *,
    external_updated_at: Optional[datetime] = None,
    internal_updated_at: Optional[datetime] = None,
    sync_status: str = "synced",
    last_error: Optional[str] = None,
) -> None:
    fields: Dict[str, Any] = {"sync_status": sync_status, "last_error": last_error}
    if external_updated_at:
        fields["external_updated_at"] = external_updated_at
    if internal_updated_at:
        fields["internal_updated_at"] = internal_updated_at
    cols = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [sync_id]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE connector_sync_state SET {cols}, last_synced_at = CURRENT_TIMESTAMP WHERE id = %s",
                tuple(values),
            )
        conn.commit()
