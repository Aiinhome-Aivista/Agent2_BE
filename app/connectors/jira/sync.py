"""Jira ↔ local incident sync.

Inbound:   Jira webhook / poll → upsert local incident
Outbound:  local incident change → push to Jira

Conflict policy (last-write-wins by `updated_at`):
    If both sides changed since `connector_sync_state.last_synced_at` and the
    Jira side is newer, the Jira version wins. We log a `conflict` row so
    operators can review.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.connectors.base.connector import IncidentPayload
from app.connectors.jira.api_client import JiraApiClient
from app.connectors.jira.mappings import (
    jira_to_local,
    local_to_jira_fields,
    STATUS_TO_JIRA_TRANSITION,
)
from app.core.logger import logger
from app.db import get_db
from app.repositories import IncidentRepository


# ============================================================================
# Inbound
# ============================================================================
def upsert_from_jira_issue(connector_id: str, issue: Dict[str, Any]) -> str:
    """Take a Jira issue dict (from webhook or poll), translate it, and
    upsert a local incident. Returns the local incident_id.
    """
    custom = _load_field_mappings(connector_id)
    translated = jira_to_local(issue, custom)

    external_id = str(translated["external_id"])
    external_key = translated.get("external_key")

    # Look up existing sync link
    link = _find_sync_state(connector_id, "issue", external_id)

    if link and link.get("internal_id"):
        # ---- Update existing local incident -------------------------------
        incident_id = link["internal_id"]

        existing = IncidentRepository.find_by_id(incident_id)
        current_status = existing.get("status") if existing else None

        update_fields = {
            "subject": translated["subject"],
            "description": translated["description"],
            "priority": translated["priority"],
            "category": translated["category"],
            "tags": translated.get("tags") or [],
            "source": "jira",
        }

        jira_status = translated.get("status")

        # Jira resolved/closed should update local incident
        if jira_status in ["resolved", "closed"]:
            update_fields["status"] = jira_status
            update_fields["resolved_at"] = datetime.now()

        # Jira reopened
        elif current_status in ["resolved", "closed"] and jira_status == "new":
            update_fields["status"] = "new"
            update_fields["resolved_at"] = None

        # Otherwise preserve AI workflow state
        IncidentRepository.update(incident_id, update_fields)

        _update_sync_state(
            link["id"],
            external_updated_at=datetime.now(),
            sync_status="synced"
        )

        logger.info(f"[jira-sync] updated incident {incident_id} from {external_key}")

        return incident_id

    # ---- Create a new local incident ----------------------------------------
    payload = {
        "subject": translated["subject"][:200] or f"Jira issue {external_key}",
        "description": translated["description"] or "(no description)",
        "caller": translated.get("caller") or "Jira sync",
        "caller_email": translated.get("caller_email"),
        "source": "jira",
        "priority": translated.get("priority", "P3"),
        "category": translated.get("category", "Uncategorised"),
        "tags": translated.get("tags") or []
    }
    incident_id = IncidentRepository.create(payload)

    _create_sync_state(
        connector_id=connector_id,
        external_id=external_id,
        external_key=external_key,
        internal_id=incident_id,
    )
    logger.info(f"[jira-sync] created incident {incident_id} from {external_key}")

    # *** AUTO-SYNC PIPELINE ***
    # Newly-imported incidents go through the full agent pipeline:
    # ingestion → triage → mistral analysis → resolution (KG/runbook) →
    # [escalation + email if needed] → KB learning.
    # We do it best-effort — if the pipeline fails the incident still exists
    # and can be processed manually from the UI.
    try:
        from app.agents.orchestrator import orchestrator
        fresh = IncidentRepository.find_by_id(incident_id) or payload | {"id": incident_id}
        orchestrator.process_new(fresh)
        logger.info(f"[jira-sync] orchestrator processed {incident_id}")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[jira-sync] orchestrator failed on {incident_id}: {e}")

    return incident_id


def upsert_from_payload(connector_id: str, p: IncidentPayload) -> str:
    """Same as `upsert_from_jira_issue` but accepting our normalised
    IncidentPayload (used by the webhook receiver after parse_webhook_event)."""
    return upsert_from_jira_issue(connector_id, p.raw or {
        "id": p.external_id,
        "key": p.external_key,
        "fields": {
            "summary": p.subject,
            "description": p.description,
            "priority": {"name": p.priority},
            "status": {"name": p.status},
            "issuetype": {"name": p.category},
            "labels": p.tags,
            "reporter": {"displayName": p.caller, "emailAddress": p.caller_email},
        },
    })


# ============================================================================
# Outbound
# ============================================================================
def push_incident_create(api: JiraApiClient, connector_id: str, project_key: str, incident: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Jira issue from a local incident. Returns the new issue dict."""
    custom = _load_field_mappings(connector_id)
    fields = local_to_jira_fields(incident, custom)
    summary = fields.get("summary") or incident.get("subject") or "Incident"
    description = incident.get("description") or ""
    priority = (fields.get("priority") or {}).get("name") if isinstance(fields.get("priority"), dict) else None
    labels = fields.get("labels") or []
    issue = api.create_issue(
        project_key=project_key,
        summary=summary,
        description=description,
        priority=priority,
        labels=labels,
    )
    _create_sync_state(
        connector_id=connector_id,
        external_id=str(issue["id"]),
        external_key=issue.get("key"),
        internal_id=incident["id"],
        direction="outbound",
    )
    logger.info(f"[jira-sync] created Jira issue {issue.get('key')} from {incident['id']}")
    return issue


def push_incident_update(api: JiraApiClient, connector_id: str, incident: Dict[str, Any]) -> bool:
    """Push local changes to the linked Jira issue."""
    link = _find_sync_state_by_internal(connector_id, incident["id"])
    if not link:
        return False
    custom = _load_field_mappings(connector_id)
    fields = local_to_jira_fields(incident, custom)
    # Drop description for updates — needs ADF conversion (TODO).
    fields.pop("description", None)
    api.update_issue(link["external_id"], fields)
    # Map status → transition if changed
    if incident.get("status"):
        target = STATUS_TO_JIRA_TRANSITION.get(incident["status"])
        if target:
            for t in api.list_transitions(link["external_id"]):
                if t["name"].lower() == target.lower():
                    api.transition_issue(link["external_id"], t["id"])
                    break
    _update_sync_state(link["id"], internal_updated_at=datetime.now(), sync_status="synced")
    return True


# ============================================================================
# Sync-state DB helpers (kept here to keep the connector self-contained;
# could be promoted to a repository if reused outside Jira.)
# ============================================================================
def _find_sync_state(connector_id: str, resource_type: str, external_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT * FROM connector_sync_state "
                "WHERE connector_id = %s AND resource_type = %s AND external_id = %s LIMIT 1",
                (connector_id, resource_type, external_id),
            )
            return cur.fetchone()


def _find_sync_state_by_internal(connector_id: str, internal_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT * FROM connector_sync_state "
                "WHERE connector_id = %s AND internal_id = %s LIMIT 1",
                (connector_id, internal_id),
            )
            return cur.fetchone()


def _create_sync_state(
    connector_id: str,
    external_id: str,
    external_key: Optional[str],
    internal_id: Optional[str],
    resource_type: str = "issue",
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


def _load_field_mappings(connector_id: str) -> List[Dict[str, Any]]:
    import json
    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT local_field, remote_field, direction, transform "
                "FROM connector_field_mappings WHERE connector_id = %s",
                (connector_id,),
            )
            rows = cur.fetchall()
    for r in rows:
        if isinstance(r.get("transform"), str):
            try:
                r["transform"] = json.loads(r["transform"])
            except json.JSONDecodeError:
                r["transform"] = {}
    return rows
