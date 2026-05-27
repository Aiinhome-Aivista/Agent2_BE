"""Zoho connector — SCAFFOLD.

Auth + HTTP wired with **region-aware URLs** (US/EU/IN/AU/CN/JP).
Module-specific CRUD stubbed with TODO[ZH-N].
Reference: https://www.zoho.com/desk/developer-guide/oauth-overview.html
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
    verify_url_token,
)
from app.core.logger import logger


META = ConnectorMeta(
    provider="zoho",
    display_name="Zoho Desk / CRM",
    description="Zoho Desk Tickets (or CRM Cases) sync — region-aware.",
    auth_type="oauth2",
    docs_url="https://www.zoho.com/desk/developer-guide/oauth-overview.html",
    icon="cloud-cog",
    capabilities=["inbound", "outbound", "polling"],
    required_config=["region", "module"],   # region: 'us'/'eu'/'in'/'au'/'cn'/'jp'; module: 'desk' | 'crm'
)

# Zoho's OAuth & API domains differ by data center.
_REGION_HOSTS: Dict[str, Dict[str, str]] = {
    "us": {"accounts": "https://accounts.zoho.com",     "desk": "https://desk.zoho.com",     "crm": "https://www.zohoapis.com"},
    "eu": {"accounts": "https://accounts.zoho.eu",      "desk": "https://desk.zoho.eu",      "crm": "https://www.zohoapis.eu"},
    "in": {"accounts": "https://accounts.zoho.in",      "desk": "https://desk.zoho.in",      "crm": "https://www.zohoapis.in"},
    "au": {"accounts": "https://accounts.zoho.com.au",  "desk": "https://desk.zoho.com.au",  "crm": "https://www.zohoapis.com.au"},
    "cn": {"accounts": "https://accounts.zoho.com.cn",  "desk": "https://desk.zoho.com.cn",  "crm": "https://www.zohoapis.com.cn"},
    "jp": {"accounts": "https://accounts.zoho.jp",      "desk": "https://desk.zoho.jp",      "crm": "https://www.zohoapis.jp"},
}

DEFAULT_SCOPES = "Desk.tickets.ALL,Desk.contacts.READ"


def _client_credentials() -> tuple[str, str]:
    cid = os.environ.get("ZOHO_OAUTH_CLIENT_ID", "").strip()
    secret = os.environ.get("ZOHO_OAUTH_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        raise ConnectorConfigError(
            "ZOHO_OAUTH_CLIENT_ID / SECRET required. Register at "
            "https://api-console.zoho.com/ → Add Client → Server-based Application."
        )
    return cid, secret


class ZohoConnector(BaseConnector):
    meta = META

    def _hosts(self) -> Dict[str, str]:
        region = (self.config or {}).get("region", "us").lower()
        if region not in _REGION_HOSTS:
            raise ConnectorConfigError(
                f"Unsupported Zoho region '{region}'. Choose from: {list(_REGION_HOSTS)}"
            )
        return _REGION_HOSTS[region]

    # ===================================================================== OAuth
    def build_authorize_url(self, redirect_uri: str, state_payload: Dict[str, str]) -> str:
        cid, _ = _client_credentials()
        hosts = self._hosts()
        params = {
            "client_id": cid,
            "scope": (self.config or {}).get("scopes", DEFAULT_SCOPES),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state_payload["state"],
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{hosts['accounts']}/oauth/v2/auth?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str, state_payload: Dict[str, Any]) -> Dict[str, Any]:
        cid, secret = _client_credentials()
        hosts = self._hosts()
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{hosts['accounts']}/oauth/v2/token",
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
                f"Zoho token exchange failed: {resp.status_code}",
                {"body": resp.text[:300]},
            )
        data = resp.json()
        if "error" in data:
            raise ConnectorAuthError(f"Zoho returned error: {data['error']}")

        # Zoho returns api_domain — use it for API calls (instead of fixed host).
        api_domain = data.get("api_domain") or hosts["desk"]
        save_credentials(
            self.connector_id,
            {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "api_domain": api_domain,
                "scope": data.get("scope", ""),
            },
            expires_at=datetime.now() + timedelta(seconds=int(data.get("expires_in", 3600))),
        )
        from app.repositories.connector_repository import ConnectorRepository
        new_config = {**(self.config or {}), "api_domain": api_domain}
        ConnectorRepository.update(self.connector_id, {"config": new_config, "status": "connected"})
        self.config = new_config
        return {"api_domain": api_domain}

    def refresh_token(self) -> Dict[str, Any]:
        creds = load_credentials(self.connector_id)
        cid = creds.get("client_id")
        secret = creds.get("client_secret")
        refresh = creds.get("refresh_token")
        
        if not cid or not refresh:
            raise ConnectorAuthError("Zoho credentials (client_id, refresh_token) missing.")
            
        hosts = self._hosts()
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{hosts['accounts']}/oauth/v2/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": cid,
                    "client_secret": secret,
                    "refresh_token": refresh,
                },
            )
        if resp.status_code != 200:
            raise ConnectorAuthError(f"Zoho refresh failed: {resp.status_code}")
        data = resp.json()
        if "error" in data:
            raise ConnectorAuthError(f"Zoho refresh error: {data['error']}")
            
        update_partial(
            self.connector_id,
            {"access_token": data["access_token"]},
            expires_at=datetime.now() + timedelta(seconds=int(data.get("expires_in", 3600))),
        )
        return {"refreshed_at": datetime.now().isoformat()}

    # ====================================================================== HTTP
    def http(self) -> HttpClient:
        creds = load_credentials(self.connector_id)
        # Zoho returns api_domain during token exchange. We can also derive it from region.
        api_domain = creds.get("api_domain") or self._hosts().get((self.config or {}).get("module", "desk"), "https://desk.zoho.com")
        
        return HttpClient(
            base_url=api_domain.rstrip("/"),
            default_headers={
                "Authorization": f"Zoho-oauthtoken {creds['access_token']}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            refresh_callback=self._refresh_callback,
        )

    def _refresh_callback(self) -> Dict[str, str]:
        self.refresh_token()
        creds = load_credentials(self.connector_id)
        return {"Authorization": f"Zoho-oauthtoken {creds['access_token']}"}

    # ================================================================== Webhooks
    def verify_webhook(self, raw_body: bytes, headers: Dict[str, str], url_token: Optional[str]) -> bool:
        # Zoho Desk webhooks support a custom header secret, but the cleanest
        # approach is a URL-token (like Jira). Operators configure the URL
        # `.../webhook?token={webhook_secret}` in Zoho's webhook settings.
        from app.repositories.connector_repository import ConnectorRepository
        connector = ConnectorRepository.find_by_id(self.connector_id)
        return verify_url_token(url_token or "", (connector or {}).get("webhook_secret") or "")

    def parse_webhook_event(self, body: Dict[str, Any]) -> Optional[IncidentPayload]:
        # TODO[ZH-1]: Zoho Desk webhook payload structure varies by event.
        #   For 'Ticket_Add' / 'Ticket_Update': body contains `payload` with
        #   ticketNumber, subject, description, status, priority, contact, …
        logger.info(f"[zoho] webhook stub — body keys={list(body.keys())}")
        return None

    # =================================================================== Polling
    def poll_for_changes(self, since: Optional[str] = None) -> List[IncidentPayload]:
        """Pull tickets from Zoho Desk."""
        module = (self.config or {}).get("module", "desk")
        if module != "desk":
            return [] # CRM polling not implemented yet
            
        params = {"from": 0, "limit": 50}
        # Zoho Desk modifiedTimeRange format: 2026-05-11T10:00:00.000Z
        if since:
            params["modifiedTimeRange"] = f"{since},2099-12-31T23:59:59.000Z"

        try:
            resp = self.http().get("/api/v1/tickets", params=params)
            data = resp.json()
            out: List[IncidentPayload] = []
            for ticket in data.get("data", []):
                out.append(self._parse_record(ticket))
            return out
        except Exception as e:
            logger.exception(f"[zoho] poll failed: {e}")
            return []

    def _parse_record(self, record: Dict[str, Any]) -> IncidentPayload:
        # Map Zoho status to local ENUM
        zh_status = record.get("status", "Open")
        status = "new"
        if zh_status in ("Closed", "Resolved"):
            status = "closed"
            
        # Map Zoho priority (High, Medium, Low) to P1-P4
        zh_priority = record.get("priority", "Medium")
        priority = "P3"
        severity = "medium"
        if zh_priority == "High":
            priority = "P1"
            severity = "critical"
        elif zh_priority == "Medium":
            priority = "P2"
            severity = "high"
        elif zh_priority == "Low":
            priority = "P4"
            severity = "low"

        return IncidentPayload(
            external_id=str(record["id"]),
            external_key=record.get("ticketNumber"),
            subject=record.get("subject") or "(no subject)",
            description=record.get("description") or "(no description)",
            status=status,
            priority=priority,
            severity=severity,
            category="Ticket",
            caller=record.get("contact", {}).get("lastName", "Zoho User"),
            caller_email=record.get("contact", {}).get("email"),
            tags=["zoho"],
            raw=record,
        )

    # =================================================================== Outbound
    def push_create(self, incident: Dict[str, Any]) -> Optional[str]:
        # TODO[ZH-3]: POST /api/v1/tickets
        return None

    def push_update(self, external_id: str, incident: Dict[str, Any]) -> bool:
        # TODO[ZH-4]: PATCH /api/v1/tickets/{external_id}
        return False

    def sync_inbound(self, payload: IncidentPayload) -> str:
        """Apply an inbound payload — returns local incident_id."""
        from app.connectors.zoho.sync import upsert_from_zoho_ticket
        return upsert_from_zoho_ticket(self.connector_id, payload)

    # =================================================================== Health
    def health_check(self) -> Dict[str, Any]:
        start = time.perf_counter()
        try:
            module = (self.config or {}).get("module", "desk")
            path = "/api/v1/organizations" if module == "desk" else "/crm/v6/users?type=CurrentUser"
            self.http().get(path)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:300]}
        return {
            "ok": True,
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "info": {
                "region": (self.config or {}).get("region"),
                "api_domain": (self.config or {}).get("api_domain"),
            },
        }
