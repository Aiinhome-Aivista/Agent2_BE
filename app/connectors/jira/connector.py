"""JiraConnector — full reference implementation of BaseConnector.

This class wires together oauth.py, api_client.py, sync.py, and the credential
vault into a single object the rest of the app interacts with.
"""
from __future__ import annotations

import base64
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.connectors.base import (
    BaseConnector,
    ConnectorConfigError,
    ConnectorMeta,
    HttpClient,
    IncidentPayload,
    load_credentials,
    save_credentials,
    update_partial,
    verify_url_token,
)
from app.connectors.jira import oauth as jira_oauth
from app.connectors.jira.api_client import JiraApiClient
from app.connectors.jira.sync import (
    push_incident_create,
    push_incident_update,
    upsert_from_jira_issue,
)
from app.core.config import settings
from app.core.logger import logger


META = ConnectorMeta(
    provider="jira",
    display_name="Jira Cloud",
    description="Atlassian Jira Cloud — bidirectional sync of issues and incidents.",
    auth_type="oauth2",
    docs_url="https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/",
    icon="trello",
    capabilities=["inbound", "outbound", "webhooks", "polling", "comments", "transitions"],
    required_config=["project_key"],
)


class JiraConnector(BaseConnector):
    meta = META

    # ===================================================== OAuth lifecycle
    def build_authorize_url(self, redirect_uri: str, state_payload: Dict[str, str]) -> str:
        return jira_oauth.build_authorize_url(state_payload["state"], redirect_uri)

    def exchange_code(self, code: str, redirect_uri: str, state_payload: Dict[str, Any]) -> Dict[str, Any]:
        token_data = jira_oauth.exchange_code_for_token(code, redirect_uri)
        access_token = token_data["access_token"]

        # Pull accessible resources to find the cloud_id.
        resources = jira_oauth.list_accessible_resources(access_token)
        if not resources:
            raise ConnectorConfigError(
                "Token has no accessible Atlassian sites. "
                "Did you grant the OAuth app access during consent?"
            )
        # If multiple, pick the first; UI can offer a picker later.
        site = resources[0]
        cloud_id = site["id"]

        # Persist credentials
        expires_at = self._parse_iso(token_data.get("expires_at"))
        save_credentials(
            self.connector_id,
            {
                "access_token": access_token,
                "refresh_token": token_data.get("refresh_token", ""),
                "token_type": token_data.get("token_type", "Bearer"),
                "scope": token_data.get("scope", ""),
                "cloud_id": cloud_id,
                "site_url": site.get("url"),
                "site_name": site.get("name"),
            },
            expires_at=expires_at,
        )

        # Persist site info into the public connector config for UI display.
        from app.repositories.connector_repository import ConnectorRepository
        new_config = {**(self.config or {}), "cloud_id": cloud_id,
                      "site_url": site.get("url"), "site_name": site.get("name")}
        ConnectorRepository.update(self.connector_id, {"config": new_config, "status": "connected"})
        self.config = new_config
        return {"cloud_id": cloud_id, "site_url": site.get("url")}

    def refresh_token(self) -> Dict[str, Any]:
        creds = load_credentials(self.connector_id)
        refresh = creds.get("refresh_token")
        if not refresh:
            raise ConnectorConfigError(
                "No refresh token stored. The user must re-authenticate this connector."
            )
        new_data = jira_oauth.refresh_access_token(refresh)
        expires_at = self._parse_iso(new_data.get("expires_at"))
        update_partial(
            self.connector_id,
            {
                "access_token": new_data["access_token"],
                "refresh_token": new_data.get("refresh_token", refresh),
                "scope": new_data.get("scope", creds.get("scope", "")),
            },
            expires_at=expires_at,
        )
        logger.info(f"[jira] refreshed token for connector {self.connector_id}")
        return {"refreshed_at": datetime.now().isoformat()}

    # ============================================================ HTTP
    def http(self) -> HttpClient:
        try:
            creds = load_credentials(self.connector_id)
        except Exception:
            creds = {}

        # OAuth2 mode
        if "access_token" in creds:
            return HttpClient(
                base_url="https://api.atlassian.com",
                default_headers={
                    "Authorization": f"Bearer {creds['access_token']}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                refresh_callback=self._refresh_callback,
            )

        # Basic Auth mode from frontend credentials
        jira_email = (
            creds.get("email")
            or creds.get("username")
            or settings.jira_email
        )

        jira_token = (
            creds.get("api_token")
            or creds.get("password")
            or settings.jira_api_token
        )

        jira_url = (
            creds.get("site_url")
            or (self.config or {}).get("site_url")
            or settings.jira_url
        )

        if jira_email and jira_token and jira_url:
            auth_str = f"{jira_email}:{jira_token}"
            encoded = base64.b64encode(auth_str.encode()).decode()

            return HttpClient(
                base_url=jira_url.rstrip("/"),
                default_headers={
                    "Authorization": f"Basic {encoded}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )

        raise ConnectorConfigError(
            "Connector is not authenticated. Send Jira site_url, email and api_token from frontend."
    )

    def _refresh_callback(self) -> Dict[str, str]:
        """Called by HttpClient on 401. Returns the new Authorization header."""
        self.refresh_token()
        creds = load_credentials(self.connector_id)
        return {"Authorization": f"Bearer {creds['access_token']}"}

    def api(self) -> JiraApiClient:
        http_client = self.http()
        if "atlassian.com/ex/jira" in http_client.base_url or "api.atlassian.com" in http_client.base_url:
            # OAuth2 needs cloud_id in the URL
            cloud_id = (self.config or {}).get("cloud_id")
            if not cloud_id:
                try:
                    creds = load_credentials(self.connector_id)
                    cloud_id = creds.get("cloud_id")
                except Exception:
                    cloud_id = None
            if not cloud_id:
                raise ConnectorConfigError("cloud_id is missing from connector config (required for OAuth).")
            return JiraApiClient(http_client, cloud_id)
        
        # Basic Auth uses the site URL directly, no cloud_id needed in path
        return JiraApiClient(http_client, "")

    # ============================================================ Webhooks
    def verify_webhook(self, raw_body: bytes, headers: Dict[str, str], url_token: Optional[str]) -> bool:
        """Atlassian dynamic webhooks have no native HMAC; we authenticate via
        a per-connector secret embedded in the URL path."""
        from app.repositories.connector_repository import ConnectorRepository
        connector = ConnectorRepository.find_by_id(self.connector_id)
        if not connector:
            return False
        return verify_url_token(url_token or "", connector.get("webhook_secret") or "")

    def parse_webhook_event(self, body: Dict[str, Any]) -> Optional[IncidentPayload]:
        """Translate a Jira webhook body into our IncidentPayload.

        Body shape (issue events):
            {
              "webhookEvent": "jira:issue_updated",
              "issue_event_type_name": "issue_updated",
              "issue": {...full issue...},
              "user": {...},
              ...
            }
        """
        event = body.get("webhookEvent", "")
        if not event.startswith("jira:") and event != "comment_created":
            return None
        issue = body.get("issue") or {}
        if not issue:
            return None
        from app.connectors.jira.mappings import jira_to_local
        translated = jira_to_local(issue)
        return IncidentPayload(
            external_id=str(translated["external_id"]),
            external_key=translated.get("external_key"),
            subject=translated["subject"],
            description=translated["description"],
            status=translated["status"],
            priority=translated["priority"],
            severity="medium",
            category=translated["category"],
            caller=translated.get("caller", "unknown"),
            caller_email=translated.get("caller_email"),
            tags=translated.get("tags") or [],
            raw=issue,
        )

    # ============================================================ Polling
    def poll_for_changes(self, since: Optional[str] = None) -> List[IncidentPayload]:
        """Pull issues updated in the last 24h (or since the given ISO time)."""
        api = self.api()
        # JQL: updated within the cursor (Jira's 'updated >=' is timezone aware)
        cursor = since or "-1d"
        jql = f"updated >= '{cursor}' ORDER BY updated DESC"
        try:
            results = api.search(
                jql=jql,
                fields=[
                    "summary", "description", "status", "priority", "issuetype",
                    "labels", "reporter", "updated",
                ],
                max_results=50,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[jira] poll failed: {e}")
            return []

        from app.connectors.jira.mappings import jira_to_local
        out: List[IncidentPayload] = []
        for issue in results.get("issues", []):
            t = jira_to_local(issue)
            out.append(IncidentPayload(
                external_id=str(t["external_id"]),
                external_key=t.get("external_key"),
                subject=t["subject"],
                description=t["description"],
                status=t["status"],
                priority=t["priority"],
                severity="medium",
                category=t["category"],
                caller=t.get("caller", "unknown"),
                caller_email=t.get("caller_email"),
                tags=t.get("tags") or [],
                raw=issue,
            ))
        return out

    def sync_inbound(self, payload: IncidentPayload) -> str:
        """Apply an inbound payload — returns local incident_id."""
        return upsert_from_jira_issue(self.connector_id, payload.raw or {})

    # ============================================================ Outbound
    def push_create(self, incident: Dict[str, Any]) -> Optional[str]:
        project_key = (self.config or {}).get("project_key")
        if not project_key:
            logger.warning(f"[jira] no project_key configured for {self.connector_id} — skipping push")
            return None
        issue = push_incident_create(self.api(), self.connector_id, project_key, incident)
        return issue.get("key")

    def push_update(self, _external_id: str, incident: Dict[str, Any]) -> bool:
        return push_incident_update(self.api(), self.connector_id, incident)

    def push_comment(self, external_id: str, comment: str) -> bool:
        try:
            self.api().add_comment(external_id, comment)
            return True
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[jira] add_comment failed: {e}")
            return False

    # ============================================================ Health
    def health_check(self) -> Dict[str, Any]:
        start = time.perf_counter()
        try:
            myself = self.api().myself()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:300]}
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "info": {
                "account_id": myself.get("accountId"),
                "display_name": myself.get("displayName"),
                "email": myself.get("emailAddress"),
                "site_name": (self.config or {}).get("site_name"),
                "site_url": (self.config or {}).get("site_url"),
            },
        }

    # =============================================================== Helpers
    @staticmethod
    def _parse_iso(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, AttributeError):
            return None
