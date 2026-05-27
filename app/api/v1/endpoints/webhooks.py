"""Webhook receiver — public, no auth header (verified by signature/URL token).

  POST /connectors/{connector_id}/webhook?token=...
        ^                                  ^
        |                                  per-connector secret
        the connector to route this event to

Flow:
  1. Read the raw body (don't let FastAPI auto-decode — signatures need exact bytes)
  2. Look up connector → instantiate
  3. Call `connector.verify_webhook(raw_body, headers, url_token)` — reject 401 if invalid
  4. Insert into webhook_events; if duplicate (UNIQUE), return 200 immediately
  5. Parse event → IncidentPayload via `connector.parse_webhook_event`
  6. If payload returned, call provider-specific sync (Jira: upsert_from_jira_issue)
  7. Mark webhook event as processed
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.connectors import instantiate
from app.connectors.base.exceptions import WebhookSignatureError
from app.core.logger import logger
from app.repositories.connector_repository import ConnectorRepository


router = APIRouter(prefix="/connectors", tags=["webhooks"])


def _extract_event_id(provider: str, body: Dict[str, Any], headers: Dict[str, str]) -> Optional[str]:
    """Extract a stable per-event ID for idempotency. Best-effort per provider."""
    # Header-based first (most reliable):
    for h in ("x-atlassian-webhook-identifier", "x-hubspot-request-id", "x-request-id"):
        if h in headers:
            return headers[h]
    # Body-based fallbacks:
    if provider == "jira":
        # Jira sends `timestamp` + issue id; combine for uniqueness.
        ts = body.get("timestamp")
        issue_id = (body.get("issue") or {}).get("id")
        if ts and issue_id:
            return f"jira:{issue_id}:{ts}"
    return None


@router.post("/{connector_id}/webhook")
async def receive_webhook(
    connector_id: str,
    request: Request,
    token: Optional[str] = Query(None),
):
    raw_body: bytes = await request.body()
    headers: Dict[str, str] = {k.lower(): v for k, v in request.headers.items()}

    connector_row = ConnectorRepository.find_by_id(connector_id)
    if not connector_row:
        raise HTTPException(status_code=404, detail="Connector not found")

    provider = connector_row["provider"]
    connector = instantiate(provider, connector_id, connector_row.get("config") or {})

    # ----------------------------------------------------- signature check
    is_valid = False
    try:
        is_valid = connector.verify_webhook(raw_body, headers, token)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webhook:{provider}] verify error: {e}")

    if not is_valid:
        # Log the rejected event for audit, but with minimal payload to avoid abuse.
        ConnectorRepository.record_webhook(
            connector_id=connector_id,
            external_event_id=None,
            event_type="rejected",
            payload={"reason": "signature_or_token_invalid"},
            signature_valid=False,
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature/token")

    # ----------------------------------------------------- parse JSON body
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}

    event_id = _extract_event_id(provider, body, headers)
    event_type = body.get("webhookEvent") or body.get("eventType") or "unknown"

    # ----------------------------------------------------- idempotency log
    record = ConnectorRepository.record_webhook(
        connector_id=connector_id,
        external_event_id=event_id,
        event_type=event_type,
        payload=body,
        signature_valid=True,
    )
    if record["duplicate"]:
        # We've seen this event before — ack and exit.
        return Response(status_code=200, content='{"status":"duplicate"}', media_type="application/json")

    webhook_id = record["id"]

    # ----------------------------------------------------- dispatch & sync
    try:
        payload = connector.parse_webhook_event(body)
        if payload is None:
            ConnectorRepository.mark_webhook_processed(webhook_id, status="processed", error=None)
            return {"status": "ignored"}

        # Provider-specific inbound sync
        if provider == "jira":
            connector.sync_inbound(payload)  # type: ignore[attr-defined]
        else:
            # Scaffold connectors: parse_webhook_event returns None today,
            # so this branch is unreachable until you implement the TODOs.
            logger.info(f"[webhook:{provider}] payload received but sync not yet implemented")

        ConnectorRepository.mark_webhook_processed(webhook_id, status="processed")
        return {"status": "processed", "webhook_id": webhook_id}
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webhook:{provider}] processing failed: {e}")
        ConnectorRepository.mark_webhook_processed(webhook_id, status="failed", error=str(e)[:500])
        # Returning 200 so the provider doesn't endlessly retry on our bug;
        # the failure is in our log for ops to retry manually.
        return {"status": "failed", "error": str(e)[:200]}
