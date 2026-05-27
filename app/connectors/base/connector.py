"""BaseConnector — abstract interface every provider implementation must satisfy.

A connector is the bridge between our internal incident model and a third-party
ticketing/CRM system. It owns:

  - OAuth (build authorize URL, exchange code, refresh access token)
  - HTTP client construction (with the right base_url and auth headers)
  - Webhook signature verification + event normalisation
  - Inbound sync   (provider event/poll → upsert local incident)
  - Outbound sync  (local incident change → upsert provider record)

Concrete classes:
    JiraConnector            (full reference implementation)
    ServiceNowConnector      (scaffold)
    SalesforceConnector      (scaffold)
    HubSpotConnector         (scaffold)
    ZohoConnector            (scaffold)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.connectors.base.http_client import HttpClient


@dataclass
class ConnectorMeta:
    """Static metadata describing a connector type."""

    provider: str
    display_name: str
    description: str
    auth_type: str  # 'oauth2' | 'oauth2_pkce' | 'basic' | 'api_key'
    docs_url: str
    icon: str  # lucide-react icon name
    capabilities: List[str] = field(default_factory=list)  # ['inbound', 'outbound', 'webhooks', 'polling']
    required_config: List[str] = field(default_factory=list)  # config keys the user must supply


@dataclass
class IncidentPayload:
    """Provider-agnostic representation of an incoming or outgoing incident.

    Connectors translate provider-specific records to/from this struct using
    their field-mapping config.
    """

    external_id: str
    external_key: Optional[str]
    subject: str
    description: str
    status: str
    priority: str
    severity: str
    category: str
    caller: str
    caller_email: Optional[str]
    tags: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


class BaseConnector(ABC):
    """Abstract connector. Concrete classes must implement the marked methods."""

    meta: ConnectorMeta

    def __init__(self, connector_id: str, config: Dict[str, Any]) -> None:
        self.connector_id = connector_id
        self.config = config or {}

    # --------------------------------------------------------- OAuth lifecycle
    def build_authorize_url(self, redirect_uri: str, state_payload: Dict[str, str]) -> str:
        """Return the URL the user's browser should be redirected to."""
        raise NotImplementedError("OAuth is not supported by this connector.")

    def exchange_code(self, code: str, redirect_uri: str, state_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Exchange the OAuth `code` for tokens."""
        raise NotImplementedError("OAuth is not supported by this connector.")

    def refresh_token(self) -> Dict[str, Any]:
        """Refresh the access token."""
        raise NotImplementedError("OAuth is not supported by this connector.")

    # ------------------------------------------------------------ HTTP client
    @abstractmethod
    def http(self) -> HttpClient:
        """Construct a ready-to-use HttpClient with auth headers attached."""

    # -------------------------------------------------------------- Webhooks
    @abstractmethod
    def verify_webhook(self, raw_body: bytes, headers: Dict[str, str], url_token: Optional[str]) -> bool:
        """Verify an incoming webhook. Return True if signature/token is valid."""

    @abstractmethod
    def parse_webhook_event(self, body: Dict[str, Any]) -> Optional[IncidentPayload]:
        """Translate a webhook body into our `IncidentPayload`.
        Returns `None` if the event isn't relevant (e.g. unrelated event type).
        """

    # --------------------------------------------------------------- Polling
    def poll_for_changes(self, since: Optional[str] = None) -> List[IncidentPayload]:
        """Pull incremental changes since the given cursor.

        Default implementation returns []; connectors override this if they
        expose a list/search endpoint. Polling is the safety net when
        webhooks are unavailable or missed.
        """
        return []

    # ------------------------------------------------------ Outbound sync (us → them)
    def push_create(self, incident: Dict[str, Any]) -> Optional[str]:
        """Create a remote record for a freshly-ingested local incident.
        Returns the new external_id, or None if outbound is unsupported."""
        return None

    def push_update(self, external_id: str, incident: Dict[str, Any]) -> bool:
        """Update an existing remote record. Returns True on success."""
        return False

    def push_comment(self, external_id: str, comment: str) -> bool:
        """Append a comment/note to the remote record."""
        return False

    # ------------------------------------------------------------- Healthcheck
    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """Make one cheap authenticated request to confirm credentials work.
        Returns a dict like {ok: bool, latency_ms: int, info: {...}}.
        """
