"""Jira Cloud REST API v3 client.

Wraps the most common operations our incident agent needs:

  - Search issues (JQL)
  - Get/create/update issue
  - Add comment
  - Transition issue (change status)
  - Register/list/delete dynamic webhooks

All methods raise on non-2xx; the caller decides what to do with errors.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.connectors.base.http_client import HttpClient
from app.core.logger import logger


class JiraApiClient:
    """Thin wrapper around HttpClient with Jira-specific methods."""

    def __init__(self, http: HttpClient, cloud_id: str) -> None:
        self._http = http
        self.cloud_id = cloud_id
        # OAuth2 uses /ex/jira/{cloud_id}/rest/api/3
        # Basic Auth uses /rest/api/3 directly
        if cloud_id:
            self._api_path = f"/ex/jira/{cloud_id}/rest/api/3"
        else:
            self._api_path = "/rest/api/3"

    # --------------------------------------------------------------- Self-info
    def myself(self) -> Dict[str, Any]:
        """GET /rest/api/3/myself — the cheapest authenticated request."""
        return self._http.get(f"{self._api_path}/myself").json()

    # ----------------------------------------------------------------- Issues
    def search(
        self,
        jql: str,
        *,
        fields: Optional[List[str]] = None,
        max_results: int = 50,
        next_page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /rest/api/3/search/jql (the new endpoint as of late 2024).

        Atlassian deprecated GET /rest/api/3/search and the new JQL search
        uses cursor-based pagination via `nextPageToken`.
        """
        body: Dict[str, Any] = {"jql": jql, "maxResults": max_results}
        if fields:
            body["fields"] = fields
        if next_page_token:
            body["nextPageToken"] = next_page_token
        return self._http.post(f"{self._api_path}/search/jql", json=body).json()

    def get_issue(self, key_or_id: str, *, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        params = {"fields": ",".join(fields)} if fields else None
        return self._http.get(f"{self._api_path}/issue/{key_or_id}", params=params).json()

    def create_issue(self, project_key: str, summary: str, description: str, issue_type: str = "Task",
                     priority: Optional[str] = None, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        """POST /rest/api/3/issue. Returns {id, key, self}.

        Description must be ADF (Atlassian Document Format) — we wrap a
        plain string in a minimal ADF envelope.
        """
        body: Dict[str, Any] = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "description": _plain_to_adf(description),
                "issuetype": {"name": issue_type},
            },
        }
        if priority:
            body["fields"]["priority"] = {"name": priority}
        if labels:
            body["fields"]["labels"] = labels
        return self._http.post(f"{self._api_path}/issue", json=body).json()

    def update_issue(self, key_or_id: str, fields: Dict[str, Any]) -> bool:
        """PUT /rest/api/3/issue/{key}. Returns True on 204."""
        resp = self._http.put(f"{self._api_path}/issue/{key_or_id}", json={"fields": fields})
        return resp.status_code in (200, 204)

    def add_comment(self, key_or_id: str, comment: str) -> Dict[str, Any]:
        body = {"body": _plain_to_adf(comment)}
        return self._http.post(f"{self._api_path}/issue/{key_or_id}/comment", json=body).json()

    def list_transitions(self, key_or_id: str) -> List[Dict[str, Any]]:
        return self._http.get(f"{self._api_path}/issue/{key_or_id}/transitions").json().get("transitions", [])

    def transition_issue(self, key_or_id: str, transition_id: str) -> bool:
        body = {"transition": {"id": transition_id}}
        resp = self._http.post(f"{self._api_path}/issue/{key_or_id}/transitions", json=body)
        return resp.status_code in (200, 204)

    # ---------------------------------------------------------------- Webhooks
    def register_webhook(
        self,
        callback_url: str,
        jql_filter: str = "project is not EMPTY",
        events: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """POST /rest/api/3/webhook — dynamic registration via OAuth.

        Note: dynamic webhooks created this way **expire after 30 days**
        and must be refreshed via PUT /rest/api/3/webhook/refresh.
        Our worker handles refresh on a daily schedule.
        """
        events = events or [
            "jira:issue_created",
            "jira:issue_updated",
            "jira:issue_deleted",
            "comment_created",
        ]
        body = {
            "url": callback_url,
            "webhooks": [{"events": events, "jqlFilter": jql_filter}],
        }
        return self._http.post(f"{self._api_path}/webhook", json=body).json()

    def list_webhooks(self) -> Dict[str, Any]:
        return self._http.get(f"{self._api_path}/webhook").json()

    def refresh_webhooks(self, webhook_ids: List[int]) -> Dict[str, Any]:
        """PUT /rest/api/3/webhook/refresh — extends webhook expiry by 30d."""
        return self._http.put(
            f"{self._api_path}/webhook/refresh",
            json={"webhookIds": webhook_ids},
        ).json()

    def delete_webhook(self, webhook_id: int) -> bool:
        body = {"webhookIds": [webhook_id]}
        resp = self._http.delete(f"{self._api_path}/webhook", json=body)
        return resp.status_code in (200, 202, 204)

    # ----------------------------------------------------------------- Projects
    def list_projects(self) -> List[Dict[str, Any]]:
        return self._http.get(f"{self._api_path}/project/search").json().get("values", [])


# ----------------------------------------------------------------------- Utils
def _plain_to_adf(text: str) -> Dict[str, Any]:
    """Wrap a plain string in minimal ADF (Atlassian Document Format).

    Jira API v3 requires this for `description` and `comment.body`.
    """
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        ],
    }


def adf_to_plain(adf: Any) -> str:
    """Best-effort flatten ADF → plain text. Non-recursive on the common case."""
    if not isinstance(adf, dict):
        return str(adf or "")
    out: List[str] = []
    for block in adf.get("content", []):
        for inline in block.get("content", []):
            if inline.get("type") == "text":
                out.append(inline.get("text", ""))
        out.append("\n")
    text = "".join(out).strip()
    return text or ""
