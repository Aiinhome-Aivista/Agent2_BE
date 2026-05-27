"""Connectors REST API.

  GET    /connectors/providers                — catalog of providers
  GET    /connectors                          — list configured connectors
  POST   /connectors                          — create a new connector record
  GET    /connectors/{id}                     — fetch one connector
  PATCH  /connectors/{id}                     — update name/config/sync_enabled
  DELETE /connectors/{id}                     — delete + revoke creds
  POST   /connectors/{id}/connect             — start OAuth flow
  GET    /connectors/oauth/callback           — OAuth callback (set on provider)
  GET    /connectors/{id}/health              — call provider /myself or equivalent
  POST   /connectors/{id}/sync                — run one sync cycle now
  POST   /connectors/{id}/register-webhook    — register webhook on provider
  GET    /connectors/{id}/events              — recent webhook events log
  GET    /connectors/{id}/mappings            — list field mappings
  PUT    /connectors/{id}/mappings            — replace field mappings
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import get_current_user, require_admin
from app.schemas.connector import (
    ConnectorCreate,
    ConnectorHealth,
    ConnectorPublic,
    ConnectorProvider,
    ConnectorUpdate,
    FieldMapping,
    FieldMappingsUpdate,
    OAuthStartResponse,
    BasicConnectRequest,
    WebhookEvent,
)
from app.core.config import settings
from app.services.connector_service import ConnectorService, _webhook_url


router = APIRouter(prefix="/connectors", tags=["connectors"])


# ---------- helper: convert DB row → Pydantic public shape -------------------
def _to_public(row: dict) -> ConnectorPublic:
    webhook_url = None
    if row.get("webhook_secret"):
        webhook_url = _webhook_url(row["id"], row["webhook_secret"])
    return ConnectorPublic(
        id=row["id"],
        provider=row["provider"],
        name=row["name"],
        status=row["status"],
        config=row.get("config") or {},
        last_synced_at=row.get("last_synced_at"),
        last_error=row.get("last_error"),
        sync_enabled=bool(row.get("sync_enabled", True)),
        poll_interval_sec=int(row.get("poll_interval_sec") or 120),
        has_webhook_secret=bool(row.get("webhook_secret")),
        webhook_url=webhook_url,
        created_by=row.get("created_by"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------- Catalog ----
@router.get("/providers", response_model=List[ConnectorProvider])
def list_providers(_user=Depends(get_current_user)):
    return ConnectorService.list_providers()


# ---------------------------------------------------------------- CRUD -------
@router.get("", response_model=List[ConnectorPublic])
def list_connectors(
    provider: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    _user=Depends(get_current_user),
):
    rows = ConnectorService.list(provider=provider, status=status)
    return [_to_public(r) for r in rows]


@router.post("", response_model=ConnectorPublic, status_code=201)
def create_connector(payload: ConnectorCreate, user=Depends(require_admin)):
    row = ConnectorService.create(
        provider=payload.provider,
        name=payload.name,
        config=payload.config,
        user_id=user["id"],
    )
    return _to_public(row)


@router.get("/{connector_id}", response_model=ConnectorPublic)
def get_connector(connector_id: str, _user=Depends(get_current_user)):
    row = ConnectorService.get(connector_id)
    if not row:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _to_public(row)


@router.patch("/{connector_id}", response_model=ConnectorPublic)
def update_connector(connector_id: str, patch: ConnectorUpdate, _user=Depends(require_admin)):
    update_fields = patch.model_dump(exclude_unset=True)
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    ConnectorService.update(connector_id, update_fields)
    row = ConnectorService.get(connector_id)
    if not row:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _to_public(row)


@router.delete("/{connector_id}", status_code=204, response_class=Response, response_model=None)
def delete_connector(connector_id: str, _user=Depends(require_admin)):
    ConnectorService.delete(connector_id)
    return None


# ---------------------------------------------------------------- OAuth ------
@router.post("/{connector_id}/connect", response_model=OAuthStartResponse)
def start_connect(connector_id: str, user=Depends(require_admin)):
    return ConnectorService.start_oauth(connector_id, user_id=user["id"])


@router.get("/oauth/callback")
def oauth_callback(state: str = Query(...), code: str = Query(...)):
    """Public OAuth callback — providers redirect here after consent."""
    try:
        result = ConnectorService.handle_oauth_callback(state=state, code=code)
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(
            f"<html><body><h2>Connector setup failed</h2><pre>{e}</pre>"
            "<p>You can close this window.</p></body></html>",
            status_code=400,
        )
    # Redirect into the SPA detail page.
    frontend_base = settings.frontend_url.rstrip("/")
    target = f"{frontend_base}/connectors/{result['connector_id']}?connected=1"
    return RedirectResponse(url=target, status_code=302)


@router.post("/{connector_id}/connect-basic")
def connect_basic(connector_id: str, payload: BasicConnectRequest, user=Depends(require_admin)):
    return ConnectorService.connect_basic(connector_id, payload.dict(exclude_unset=True))


# ---------------------------------------------------------------- Operations -
@router.get("/{connector_id}/health", response_model=ConnectorHealth)
def health(connector_id: str, _user=Depends(get_current_user)):
    return ConnectorService.health_check(connector_id)


@router.post("/{connector_id}/sync")
def sync_now(connector_id: str, since: Optional[str] = Query(None), _user=Depends(require_admin)):
    return ConnectorService.sync_now(connector_id, since=since)


@router.post("/{connector_id}/register-webhook")
def register_webhook(connector_id: str, _user=Depends(require_admin)):
    return ConnectorService.register_webhook(connector_id)


@router.get("/{connector_id}/events", response_model=List[WebhookEvent])
def list_events(connector_id: str, limit: int = 50, _user=Depends(get_current_user)):
    return ConnectorService.list_webhook_events(connector_id, limit=limit)


# ----------------------------------------------------------- Field mappings --
@router.get("/{connector_id}/mappings", response_model=List[FieldMapping])
def list_mappings(connector_id: str, _user=Depends(get_current_user)):
    return ConnectorService.list_field_mappings(connector_id)


@router.put("/{connector_id}/mappings", response_model=List[FieldMapping])
def replace_mappings(connector_id: str, payload: FieldMappingsUpdate, _user=Depends(require_admin)):
    ConnectorService.replace_field_mappings(
        connector_id, [m.model_dump() for m in payload.mappings]
    )
    return ConnectorService.list_field_mappings(connector_id)
