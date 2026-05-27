"""HubSpot connector — SCAFFOLD.

Auth + HTTP wired. Tickets CRUD + parse_webhook stubbed with TODO[HS-N].
HubSpot v3 webhooks ARE signed (HMAC-SHA256 base64) — verification is real.
Reference: https://developers.hubspot.com/docs/api/oauth-quickstart-guide
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from app.connectors.base import (
    BaseConnector,
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorMeta,
    HttpClient,
    IncidentPayload,
    load_credentials,
    save_credentials,
    update_partial,
    verify_hmac_sha256_base64,
)
from app.core.logger import logger


META = ConnectorMeta(
    provider="hubspot",
    display_name="HubSpot",
    description="HubSpot CRM — Tickets / Tasks sync.",
    auth_type="oauth2",
    docs_url="https://developers.hubspot.com/docs/api/oauth-quickstart-guide",
    icon="briefcase",
    capabilities=["inbound", "outbound", "webhooks", "polling"],
    required_config=["pipeline_id"],
)

HUBSPOT_AUTHORIZE = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_API = "https://api.hubapi.com"

DEFAULT_SCOPES = "tickets crm.objects.contacts.read crm.objects.companies.read"


def _client_credentials() -> tuple[str, str]:
    cid = os.environ.get("HUBSPOT_OAUTH_CLIENT_ID", "").strip()
    secret = os.environ.get("HUBSPOT_OAUTH_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        raise ConnectorConfigError(
            "HUBSPOT_OAUTH_CLIENT_ID / SECRET required. Create an app at "
            "https://developers.hubspot.com/ → Apps → Create app."
        )
    return cid, secret


def _client_signing_secret() -> str:
    """HMAC verification key. HubSpot calls this 'Client Secret' in v1 docs but
    'App Secret' in v3 webhooks docs; same value."""
    return os.environ.get("HUBSPOT_OAUTH_CLIENT_SECRET", "").strip()


class HubSpotConnector(BaseConnector):
    meta = META

    # ===================================================================== OAuth
    def build_authorize_url(self, redirect_uri: str, state_payload: Dict[str, str]) -> str:
        cid, _ = _client_credentials()
        params = {
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "scope": (self.config or {}).get("scopes", DEFAULT_SCOPES),
            "state": state_payload["state"],
        }
        return f"{HUBSPOT_AUTHORIZE}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str, state_payload: Dict[str, Any]) -> Dict[str, Any]:
        cid, secret = _client_credentials()
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                HUBSPOT_TOKEN,
                data={
                    "grant_type": "authorization_code",
                    "client_id": cid,
                    "client_secret": secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
            )
        if resp.status_code != 200:
            raise ConnectorAuthError(
                f"HubSpot token exchange failed: {resp.status_code}",
                {"body": resp.text[:300]},
            )
        data = resp.json()
        save_credentials(
            self.connector_id,
            {
                "access_token": data["access_token"],
                "refresh_token": data["refresh_token"],
                "token_type": data.get("token_type", "bearer"),
            },
            expires_at=datetime.now() + timedelta(seconds=int(data.get("expires_in", 1800))),
        )
        from app.repositories.connector_repository import ConnectorRepository
        ConnectorRepository.update(self.connector_id, {"status": "connected"})
        return {}

    def refresh_token(self) -> Dict[str, Any]:
        creds = load_credentials(self.connector_id)
        cid, secret = _client_credentials()
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                HUBSPOT_TOKEN,
                data={
                    "grant_type": "refresh_token",
                    "client_id": cid,
                    "client_secret": secret,
                    "refresh_token": creds.get("refresh_token", ""),
                },
            )
        if resp.status_code != 200:
            raise ConnectorAuthError(f"HubSpot refresh failed: {resp.status_code}")
        data = resp.json()
        update_partial(
            self.connector_id,
            {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", creds.get("refresh_token", "")),
            },
            expires_at=datetime.now() + timedelta(seconds=int(data.get("expires_in", 1800))),
        )
        return {"refreshed_at": datetime.now().isoformat()}

    # ====================================================================== HTTP
    def http(self) -> HttpClient:
        creds = load_credentials(self.connector_id)
        return HttpClient(
            base_url=HUBSPOT_API,
            default_headers={
                "Authorization": f"Bearer {creds['access_token']}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            refresh_callback=self._refresh_callback,
        )

    def _refresh_callback(self) -> Dict[str, str]:
        self.refresh_token()
        creds = load_credentials(self.connector_id)
        return {"Authorization": f"Bearer {creds['access_token']}"}

    # ================================================================== Webhooks
    def verify_webhook(self, raw_body: bytes, headers: Dict[str, str], _url_token: Optional[str]) -> bool:
        """HubSpot v3 webhook signature: base64(HMAC-SHA256(client_secret, request_body)).

        Header: X-HubSpot-Signature-v3 (we accept v2 fallback for older accounts).
        """
        sig_v3 = headers.get("x-hubspot-signature-v3") or headers.get("X-HubSpot-Signature-v3")
        if sig_v3:
            return verify_hmac_sha256_base64(raw_body, sig_v3, _client_signing_secret())
        sig_v2 = headers.get("x-hubspot-signature") or headers.get("X-HubSpot-Signature")
        if sig_v2:
            # v2 = sha256(secret + body) — different scheme; minimal impl below.
            import hashlib
            expected = hashlib.sha256((_client_signing_secret() + raw_body.decode("utf-8")).encode()).hexdigest()
            return expected == sig_v2
        return False

    def parse_webhook_event(self, body: Dict[str, Any]) -> Optional[IncidentPayload]:
        # HubSpot sends a JSON ARRAY of subscription events at the top level.
        # Caller (webhook receiver) should pass each element as `body`.
        # TODO[HS-1]: Map subscription event → fetch full ticket via API,
        #   then translate. The event itself only contains objectId + change.
        logger.info(f"[hubspot] webhook event stub — keys={list(body.keys())}")
        return None

    # =================================================================== Polling
    def poll_for_changes(self, since: Optional[str] = None) -> List[IncidentPayload]:
        # TODO[HS-2]: POST /crm/v3/objects/tickets/search with hs_lastmodifieddate filter
        return []

    # =================================================================== Outbound
    def push_create(self, incident: Dict[str, Any]) -> Optional[str]:
        # TODO[HS-3]: POST /crm/v3/objects/tickets with properties {subject, content, hs_pipeline, ...}
        return None

    def push_update(self, external_id: str, incident: Dict[str, Any]) -> bool:
        # TODO[HS-4]: PATCH /crm/v3/objects/tickets/{external_id}
        return False

    # =================================================================== Health
    def health_check(self) -> Dict[str, Any]:
        start = time.perf_counter()
        try:
            self.http().get("/integrations/v1/me")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:300]}
        return {"ok": True, "latency_ms": int((time.perf_counter() - start) * 1000)}
