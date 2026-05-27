"""Salesforce connector — SCAFFOLD.

Auth + HTTP wired. CRUD + parse_webhook stubbed with TODO[SF-N].
Reference: https://help.salesforce.com/s/articleView?id=sf.remoteaccess_oauth_web_server_flow.htm
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
    provider="salesforce",
    display_name="Salesforce",
    description="Salesforce CRM — Case object sync.",
    auth_type="basic",
    docs_url="https://help.salesforce.com/s/articleView?id=sf.remoteaccess_oauth_web_server_flow.htm",
    icon="cloud",
    capabilities=["inbound", "outbound", "polling"],
    required_config=["sandbox"],   # bool — sandbox vs production login URL
)


class SalesforceConnector(BaseConnector):
    meta = META

    # ====================================================================== HTTP
    def _get_sf_client(self):
        """Internal helper to get a simple-salesforce client."""
        from simple_salesforce import Salesforce
        creds = load_credentials(self.connector_id)
        username = creds.get("username")
        password = creds.get("password")
        token = creds.get("security_token")
        domain = "test" if bool(self.config.get("sandbox", False)) else "login"
        
        if not username or not password:
            raise ConnectorAuthError(f"Salesforce credentials missing for {self.connector_id}")

        return Salesforce(
            username=username,
            password=password,
            security_token=token,
            domain=domain
        )

    def http(self) -> HttpClient:
        """Returns an HttpClient wrapped around the SF session."""
        sf = self._get_sf_client()
        return HttpClient(
            base_url=sf.base_url,
            default_headers={
                "Authorization": f"Bearer {sf.session_id}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    # ================================================================== Webhooks
    def verify_webhook(self, raw_body: bytes, headers: Dict[str, str], url_token: Optional[str]) -> bool:
        # Salesforce Outbound Messages don't include HMAC. Two valid options:
        # (1) URL token (like Jira), or (2) verify the SOAP organizationId
        #     against your known org ID.  Default: URL token.
        from app.connectors.base import verify_url_token
        from app.repositories.connector_repository import ConnectorRepository
        connector = ConnectorRepository.find_by_id(self.connector_id)
        return verify_url_token(url_token or "", (connector or {}).get("webhook_secret") or "")

    def parse_webhook_event(self, body: Dict[str, Any]) -> Optional[IncidentPayload]:
        # TODO[SF-1]: Implement parsing for either:
        #   (a) Outbound Message SOAP envelope (XML, parse via xml.etree)
        #   (b) Custom Apex trigger posting JSON via HttpCallout.
        # Most teams use (b). Recommended payload from Apex:
        #   {"Id": ..., "CaseNumber": ..., "Subject": ..., "Description": ...,
        #    "Status": ..., "Priority": ..., "ContactEmail": ...}
        logger.info(f"[salesforce] webhook stub — body keys={list(body.keys())}")
        return None

    # =================================================================== Polling
    def poll_for_changes(self, since: Optional[str] = None) -> List[IncidentPayload]:
        """Pull Cases from Salesforce."""
        sf = self._get_sf_client()
        query = "SELECT Id, CaseNumber, Subject, Description, Status, Priority, CreatedDate FROM Case"
        if since:
            # Salesforce SOQL uses YYYY-MM-DDTHH:MM:SSZ
            query += f" WHERE LastModifiedDate >= {since}"
        query += " ORDER BY LastModifiedDate DESC LIMIT 50"
        
        try:
            results = sf.query(query)
            out: List[IncidentPayload] = []
            for record in results.get("records", []):
                out.append(self._parse_record(record))
            return out
        except Exception as e:
            logger.exception(f"[salesforce] poll failed: {e}")
            return []

    def _parse_record(self, record: Dict[str, Any]) -> IncidentPayload:
        # Map SF Status to local ENUM
        sf_status = record.get("Status", "New")
        status = "new"
        if sf_status in ("Closed", "Resolved"):
            status = "closed"
        
        # Map SF Priority (High, Medium, Low) to P1-P4
        sf_priority = record.get("Priority", "Medium")
        priority = "P3"
        severity = "medium"
        if sf_priority == "High":
            priority = "P1"
            severity = "critical"
        elif sf_priority == "Medium":
            priority = "P2"
            severity = "high"
        elif sf_priority == "Low":
            priority = "P4"
            severity = "low"

        return IncidentPayload(
            external_id=record["Id"],
            external_key=record.get("CaseNumber"),
            subject=record.get("Subject") or "(no subject)",
            description=record.get("Description") or "(no description)",
            status=status,
            priority=priority,
            severity=severity,
            category="Case",
            caller="Salesforce",
            caller_email=None,
            tags=["salesforce"],
            raw=record,
        )

    # =================================================================== Outbound
    def push_create(self, incident: Dict[str, Any]) -> Optional[str]:
        sf = self._get_sf_client()
        try:
            result = sf.Case.create({
                "Subject": incident.get("subject"),
                "Description": incident.get("description"),
                "Status": "New",
                "Origin": "Web",
                "Priority": "Medium" if incident.get("priority") == "P3" else "High"
            })
            return result.get("id")
        except Exception as e:
            logger.exception(f"[salesforce] push_create failed: {e}")
            return None

    def push_update(self, external_id: str, incident: Dict[str, Any]) -> bool:
        sf = self._get_sf_client()
        try:
            sf.Case.update(external_id, {
                "Subject": incident.get("subject"),
                "Description": incident.get("description"),
            })
            return True
        except Exception as e:
            logger.exception(f"[salesforce] push_update failed: {e}")
            return False

    def sync_inbound(self, payload: IncidentPayload) -> str:
        """Apply an inbound payload — returns local incident_id."""
        from app.connectors.salesforce.sync import upsert_from_salesforce_case
        return upsert_from_salesforce_case(self.connector_id, payload)

    # =================================================================== Health
    def health_check(self) -> Dict[str, Any]:
        start = time.perf_counter()
        try:
            sf = self._get_sf_client()
            # A simple describe call to verify connectivity
            sf.Case.describe()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:300]}
        
        return {
            "ok": True,
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "info": {
                "instance_url": sf.base_url,
            },
        }
