"""Webhook signature verification.

Each provider signs webhooks differently. This module gathers the common
strategies in one place:

  - HMAC-SHA256 with hex digest          (Salesforce, GitHub-style)
  - HMAC-SHA256 with base64 digest       (HubSpot v3, Atlassian Connect)
  - Constant-time URL token              (Jira Cloud dynamic webhooks — no native
                                          HMAC, so we put a per-connector secret
                                          in the URL path and compare it with
                                          `secrets.compare_digest`)

`verify_*` functions return `True/False` — callers raise the appropriate
exception themselves so they can also log the offending payload.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Optional


# ----------------------------------------------------------------- HMAC helpers
def verify_hmac_sha256_hex(
    raw_body: bytes,
    signature_header: Optional[str],
    secret: str,
    *,
    prefix: str = "",  # e.g. "sha256=" for GitHub-style
) -> bool:
    """Verify a hex-encoded HMAC-SHA256 signature."""
    if not signature_header:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    received = signature_header
    if prefix and received.startswith(prefix):
        received = received[len(prefix):]
    return hmac.compare_digest(expected, received)


def verify_hmac_sha256_base64(
    raw_body: bytes,
    signature_header: Optional[str],
    secret: str,
) -> bool:
    """Verify a base64-encoded HMAC-SHA256 signature."""
    if not signature_header:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature_header)


# ------------------------------------------------------------- URL-token helper
def verify_url_token(provided: str, expected: str) -> bool:
    """Constant-time comparison of a per-connector URL secret token.

    Used by Jira Cloud (dynamic webhooks created via REST API don't include
    an HMAC; instead we put a secret in the webhook URL itself).
    """
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided, expected)


def generate_webhook_secret() -> str:
    """Returns a URL-safe secret suitable for embedding in a webhook URL."""
    return secrets.token_urlsafe(32)
