"""ServiceNow connector — SCAFFOLD.

╔══════════════════════════════════════════════════════════════════════════╗
║  STATUS: Auth flow + base API client are wired and tested.              ║
║          Business logic methods (sync, push_create, parse_webhook) are   ║
║          stubbed with TODOs marking exactly what to fill in.             ║
║                                                                          ║
║  WHAT WORKS:                                                             ║
║    ✅ OAuth 2.0 password grant (set INSTANCE_URL + creds)                 ║
║    ✅ HTTP client with auto-refresh + retry/backoff                       ║
║    ✅ health_check() against /api/now/table/sys_user/me                   ║
║                                                                          ║
║  WHAT'S TODO (each marked with TODO[SN-N]):                              ║
║    ❌ parse_webhook_event — ServiceNow Business Rule outbound REST       ║
║    ❌ push_create / push_update — incident table CRUD                     ║
║    ❌ poll_for_changes — sys_updated_on cursor                           ║
║    ❌ field mappings — caller_id, urgency, impact, assignment_group       ║
║                                                                          ║
║  REFERENCES:                                                             ║
║    https://docs.servicenow.com/bundle/utah-application-development/     ║
║       page/integrate/inbound-rest/concept/c_RESTAPI.html                 ║
║    https://docs.servicenow.com/bundle/utah-application-development/     ║
║       page/integrate/outbound-rest/concept/c_OutboundRESTWebService.html ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

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
    provider="servicenow",
    display_name="ServiceNow",
    description="ServiceNow ITSM — incident table sync.",
    auth_type="basic",
    docs_url="https://docs.servicenow.com/bundle/utah-application-development/page/integrate/inbound-rest/concept/c_RESTAPI.html",
    icon="server",
    capabilities=["inbound", "outbound", "polling"],
    required_config=["instance", "incident_table"],
)


class ServiceNowConnector(BaseConnector):
    meta = META

    # ====================================================================== HTTP
    def _get_base_url(self) -> str:
        instance = self.config.get("instance")
        if not instance:
            raise ConnectorConfigError("ServiceNow 'instance' name is missing in config.")
        if instance.startswith("http"):
            return instance.rstrip("/")
        return f"https://{instance}.service-now.com"

    def http(self) -> HttpClient:
        creds = load_credentials(self.connector_id)
        username = creds.get("username")
        password = creds.get("password")
        
        if not username or not password:
            raise ConnectorAuthError(f"Basic Auth credentials missing for {self.connector_id}")

        return HttpClient(
            base_url=self._get_base_url(),
            default_headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            auth=httpx.BasicAuth(username, password),
        )

    # ================================================================== Webhooks
    def verify_webhook(self, raw_body: bytes, headers: Dict[str, str], url_token: Optional[str]) -> bool:
        """ServiceNow doesn't sign outbound REST messages by default. We verify
        the URL token like Jira; alternative is to use a Basic-auth header
        on the Outbound REST Message in ServiceNow."""
        from app.repositories.connector_repository import ConnectorRepository
        connector = ConnectorRepository.find_by_id(self.connector_id)
        if not connector:
            return False
        return verify_url_token(url_token or "", connector.get("webhook_secret") or "")

    def _parse_record(self, record: Dict[str, Any]) -> IncidentPayload:
        """Helper to convert a ServiceNow table API row to IncidentPayload."""
        # state mapping (ServiceNow defaults: 1=New, 2=In Progress, 3=On Hold, 6=Resolved, 7=Closed, 8=Canceled)
        state = str(record.get("state", "1"))
        status = "new"
        if state in ("6", "7"):
            status = "resolved"
        elif state == "8":
            status = "closed"

        # urgency/impact mapping to priority
        raw_priority = str(record.get("priority", "3"))
        if raw_priority not in ("1", "2", "3", "4"):
            raw_priority = "3"
        priority = f"P{raw_priority}"
        
        severity_map = {"P1": "critical", "P2": "high", "P3": "medium", "P4": "low"}
        severity = severity_map.get(priority, "medium")

        return IncidentPayload(
            external_id=record["sys_id"],
            external_key=record.get("number"),
            subject=record.get("short_description", ""),
            description=record.get("description") or record.get("short_description", ""),
            status=status,
            priority=priority,
            severity=severity,
            category=record.get("category") or "inquiry",
            caller=record.get("caller_id", {}).get("display_value") if isinstance(record.get("caller_id"), dict) else str(record.get("caller_id", "Unknown")),
            caller_email=None,  # Need extra query or display value if included
            raw=record,
        )

    def parse_webhook_event(self, body: Dict[str, Any]) -> Optional[IncidentPayload]:
        # If the body is a direct record or wrapped in 'result'
        record = body.get("result", body)
        if "sys_id" not in record:
            logger.warning(f"[servicenow] webhook body missing sys_id: {list(record.keys())}")
            return None
        return self._parse_record(record)

    # =================================================================== Polling
    def poll_for_changes(self, since: Optional[str] = None) -> List[IncidentPayload]:
        table = self.config.get("incident_table", "incident")
        params: Dict[str, Any] = {
            "sysparm_limit": 50,
            "sysparm_display_value": "true", # To get display names for reference fields
        }
        
        if since:
            # ServiceNow sys_updated_on is in format 'YYYY-MM-DD HH:MM:SS' (UTC)
            params["sysparm_query"] = f"sys_updated_on>{since}^ORDERBYsys_updated_on"
        else:
            params["sysparm_query"] = "^ORDERBYDESCsys_updated_on"

        try:
            resp = self.http().get(f"/api/now/table/{table}", params=params)
            results = resp.json().get("result", [])
            return [self._parse_record(r) for r in results]
        except Exception as e:
            logger.exception(f"[servicenow] poll failed: {e}")
            raise

    # =================================================================== Outbound
    def push_create(self, incident: Dict[str, Any]) -> Optional[str]:
        # TODO[SN-3]: POST /api/now/table/incident with mapped fields:
        #   {"short_description": ..., "description": ..., "urgency": ..., ...}
        #   Return the new sys_id from response['result']['sys_id'].
        return None

    def push_update(self, external_id: str, incident: Dict[str, Any]) -> bool:
        # TODO[SN-4]: PATCH /api/now/table/incident/{external_id}
        return False

    def push_comment(self, external_id: str, comment: str) -> bool:
        # TODO[SN-5]: POST /api/now/table/incident/{external_id} with
        #   `{"comments": comment}` (comments field is appended on update).
        return False

    def sync_inbound(self, payload: IncidentPayload) -> str:
        """Apply an inbound payload — returns local incident_id."""
        from app.connectors.servicenow.sync import upsert_from_servicenow_incident
        return upsert_from_servicenow_incident(self.connector_id, payload)

    # =================================================================== Health
    def health_check(self) -> Dict[str, Any]:
        start = time.perf_counter()
        try:
            resp = self.http().get("/api/now/table/sys_user", params={"sysparm_limit": 1})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:300]}
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "info": {
                "instance_url": (self.config or {}).get("instance_url"),
                "sample_records": resp.json().get("result", [])[:1],
            },
        }
