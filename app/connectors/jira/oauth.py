"""Jira Cloud OAuth (3LO) flow.

Atlassian uses 3-legged OAuth via auth.atlassian.com:

    Authorize:  https://auth.atlassian.com/authorize
    Token:      https://auth.atlassian.com/oauth/token
    Resources:  https://api.atlassian.com/oauth/token/accessible-resources
    API base:   https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3

Required scopes for our use case:
    read:jira-work, write:jira-work    — issue CRUD and comments
    manage:jira-webhook                 — register dynamic webhooks
    offline_access                      — get a refresh token

Reference: https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict
from urllib.parse import urlencode

import httpx

from app.connectors.base.exceptions import ConnectorAuthError, ConnectorConfigError
from app.core.logger import logger


JIRA_AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
JIRA_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
JIRA_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"

DEFAULT_SCOPES = [
    "read:jira-work",
    "write:jira-work",
    "manage:jira-project",
    "manage:jira-webhook",
    "manage:jira-configuration",
    "offline_access",
    "read:jira-user",
    "read:me"
]


def _client_credentials() -> tuple[str, str]:
    """Read OAuth app credentials from env. Set these to the values from
    https://developer.atlassian.com/console/myapps/ → your app → Settings."""
    client_id = os.environ.get("JIRA_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("JIRA_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise ConnectorConfigError(
            "JIRA_OAUTH_CLIENT_ID and JIRA_OAUTH_CLIENT_SECRET env vars are required. "
            "Create an OAuth 2.0 (3LO) app at https://developer.atlassian.com/console/myapps/"
        )
    return client_id, client_secret


def build_authorize_url(state: str, redirect_uri: str, audience: str = "api.atlassian.com") -> str:
    """Construct the URL the browser should be redirected to for consent."""
    client_id, _ = _client_credentials()
    params = {
        "audience": audience,
        "client_id": client_id,
        "scope": " ".join(DEFAULT_SCOPES),
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    return f"{JIRA_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str, redirect_uri: str) -> Dict[str, Any]:
    """POST /oauth/token with grant_type=authorization_code.

    Returns the full token payload plus a computed `expires_at`.
    """
    client_id, client_secret = _client_credentials()
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    logger.info("[jira] exchanging authorization code for tokens")
    with httpx.Client(timeout=15) as client:
        resp = client.post(JIRA_TOKEN_URL, json=payload, headers={"Accept": "application/json"})
    if resp.status_code != 200:
        logger.error(f"[jira] token exchange failed: {resp.status_code} {resp.text[:300]}")
        raise ConnectorAuthError(
            f"Token exchange failed: {resp.status_code}",
            {"body": resp.text[:500]},
        )
    data = resp.json()
    expires_in = int(data.get("expires_in", 3600))
    data["expires_at"] = (datetime.now() + timedelta(seconds=expires_in)).isoformat()
    return data


def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """POST /oauth/token with grant_type=refresh_token.

    Atlassian issues a *new* refresh_token on every refresh (rotating refresh
    tokens). Always replace both tokens, not just the access one.
    """
    client_id, client_secret = _client_credentials()
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(JIRA_TOKEN_URL, json=payload, headers={"Accept": "application/json"})
    if resp.status_code != 200:
        logger.error(f"[jira] refresh failed: {resp.status_code} {resp.text[:300]}")
        raise ConnectorAuthError(
            f"Token refresh failed: {resp.status_code}",
            {"body": resp.text[:300]},
        )
    data = resp.json()
    expires_in = int(data.get("expires_in", 3600))
    data["expires_at"] = (datetime.now() + timedelta(seconds=expires_in)).isoformat()
    return data


def list_accessible_resources(access_token: str) -> list[Dict[str, Any]]:
    """Return the list of Atlassian sites the token has access to.

    The user typically has one — that becomes our `cloud_id`.
    Response items look like:
        {"id": "...", "url": "https://acme.atlassian.net", "name": "Acme",
         "scopes": [...], "avatarUrl": "..."}
    """
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            JIRA_RESOURCES_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
    if resp.status_code != 200:
        raise ConnectorAuthError(
            f"Could not list accessible resources: {resp.status_code}",
            {"body": resp.text[:300]},
        )
    return resp.json()
