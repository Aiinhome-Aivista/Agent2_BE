"""Pydantic schemas for the connector REST API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ----------------------------------------------------- Provider catalog -----
class ConnectorProvider(BaseModel):
    provider: str
    display_name: str
    description: str
    auth_type: str
    docs_url: str
    icon: str
    capabilities: List[str]
    required_config: List[str]
    maturity: Literal["production", "scaffold"]


# ----------------------------------------------------- Connector entity -----
class ConnectorPublic(BaseModel):
    """Connector as exposed via the API. NEVER includes credentials."""

    id: str
    provider: str
    name: str
    status: Literal["disconnected", "connecting", "connected", "error", "expired"]
    config: Dict[str, Any] = Field(default_factory=dict)
    last_synced_at: Optional[datetime] = None
    last_error: Optional[str] = None
    sync_enabled: bool = True
    poll_interval_sec: int = 120
    has_webhook_secret: bool = False
    webhook_url: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ConnectorCreate(BaseModel):
    provider: Literal["jira", "servicenow", "salesforce", "hubspot", "zoho"]
    name: str = Field(..., min_length=1, max_length=150)
    config: Dict[str, Any] = Field(default_factory=dict)


class ConnectorUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    sync_enabled: Optional[bool] = None
    poll_interval_sec: Optional[int] = Field(None, ge=60, le=86400)


# ---------------------------------------------------- OAuth ----------------
class OAuthStartResponse(BaseModel):
    authorize_url: str
    state: str


class BasicConnectRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    security_token: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None

    # Jira frontend credential fields
    site_url: Optional[str] = None
    email: Optional[str] = None
    api_token: Optional[str] = None


# --------------------------------------------------- Field mappings -------
class FieldMapping(BaseModel):
    local_field: str
    remote_field: str
    direction: Literal["inbound", "outbound", "both"] = "both"
    transform: Dict[str, Any] = Field(default_factory=dict)
    is_required: bool = False


class FieldMappingsUpdate(BaseModel):
    mappings: List[FieldMapping]


# ------------------------------------------------------ Health ------------
class ConnectorHealth(BaseModel):
    ok: bool
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    info: Dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------ Webhook events log -----
class WebhookEvent(BaseModel):
    id: str
    external_event_id: Optional[str] = None
    event_type: str
    signature_valid: bool
    received_at: datetime
    processed_at: Optional[datetime] = None
    process_status: str
    error: Optional[str] = None
