"""OAuth 2.0 helper utilities — state, PKCE, callback verification.

Every connector OAuth flow uses the same pattern:

  1. User clicks "Connect" → backend calls `start_flow(provider, ...)`
     which generates a `state` token + PKCE pair, stores them, and
     returns the authorize URL to redirect the browser to.

  2. Provider redirects back to `/api/v1/connectors/oauth/callback`
     with `?code=...&state=...`. We call `consume_state(state)` to
     atomically retrieve and delete the stored values, then exchange
     the code for tokens.

State entries auto-expire after 10 minutes — preventing replay and
unbounded growth.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from app.connectors.base.exceptions import ConnectorAuthError
from app.db import get_db


STATE_TTL_MINUTES = 10


def _generate_pkce_pair() -> Tuple[str, str]:
    """Returns (code_verifier, code_challenge). RFC 7636 §4.1–4.2.

    Verifier: 43–128 chars from `[A-Z]/[a-z]/[0-9]/-/./_/~`.
    Challenge: base64url(SHA256(verifier)) — for `S256` method.
    """
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


def start_flow(
    provider: str,
    *,
    user_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    use_pkce: bool = True,
) -> Dict[str, str]:
    """Generate state + PKCE for a new OAuth flow and persist them.

    Returns a dict with `state`, `code_challenge`, and `code_challenge_method`.
    The caller (provider-specific OAuth module) builds the authorize URL.
    """
    import json

    state = secrets.token_urlsafe(32)
    verifier, challenge = _generate_pkce_pair() if use_pkce else ("", "")
    expires_at = datetime.now() + timedelta(minutes=STATE_TTL_MINUTES)

    with get_db() as conn:
        with conn.cursor() as cur:
            # Best-effort cleanup of expired states on each new flow.
            cur.execute("DELETE FROM oauth_states WHERE expires_at < UTC_TIMESTAMP()")
            cur.execute(
                "INSERT INTO oauth_states (state, provider, code_verifier, user_id, payload, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (state, provider, verifier or None, user_id, json.dumps(payload or {}), expires_at),
            )
        conn.commit()

    return {
        "state": state,
        "code_verifier": verifier,
        "code_challenge": challenge,
        "code_challenge_method": "S256" if use_pkce else "",
    }


def consume_state(state: str) -> Dict[str, Any]:
    """Atomically read & delete a state entry. Raises if missing/expired.

    Returns the original payload (provider, code_verifier, user_id, …).
    """
    import json

    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT * FROM oauth_states WHERE state = %s AND expires_at > UTC_TIMESTAMP() LIMIT 1",
                (state,),
            )
            row = cur.fetchone()
            if not row:
                raise ConnectorAuthError("OAuth state is missing or expired", {"state": state})
            cur.execute("DELETE FROM oauth_states WHERE state = %s", (state,))
        conn.commit()

    if isinstance(row.get("payload"), str):
        try:
            row["payload"] = json.loads(row["payload"])
        except json.JSONDecodeError:
            row["payload"] = {}
    return row
