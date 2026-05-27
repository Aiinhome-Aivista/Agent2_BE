"""Credential storage — encrypts payloads with the vault and persists them
in the `connector_credentials` table.

Public API:
    save_credentials(connector_id, payload, expires_at=None)
    load_credentials(connector_id) -> dict
    delete_credentials(connector_id)
    update_partial(connector_id, patch)   # decrypt → merge → re-encrypt
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from app.connectors.base.exceptions import ConnectorAuthError
from app.core.crypto import get_vault
from app.db import get_db


def save_credentials(
    connector_id: str,
    payload: Dict[str, Any],
    expires_at: Optional[datetime] = None,
) -> None:
    """Insert-or-update encrypted credentials for a connector.

    Caller is responsible for setting `connectors.status = 'connected'`
    afterwards (in a transaction with this if needed).
    """
    vault = get_vault()
    ciphertext = vault.encrypt(payload)
    cred_id = f"CRED-{uuid.uuid4().hex[:12]}"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO connector_credentials (id, connector_id, encrypted_payload, key_version, expires_at)
                VALUES (%s, %s, %s, 1, %s)
                ON DUPLICATE KEY UPDATE
                    encrypted_payload = VALUES(encrypted_payload),
                    key_version       = VALUES(key_version),
                    expires_at        = VALUES(expires_at),
                    updated_at        = CURRENT_TIMESTAMP
                """,
                (cred_id, connector_id, ciphertext, expires_at),
            )
        conn.commit()


def load_credentials(connector_id: str) -> Dict[str, Any]:
    """Decrypt and return credentials. Raises if not present."""
    vault = get_vault()
    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT encrypted_payload, expires_at FROM connector_credentials "
                "WHERE connector_id = %s LIMIT 1",
                (connector_id,),
            )
            row = cur.fetchone()
    if not row:
        raise ConnectorAuthError(
            f"No credentials stored for connector {connector_id}",
            {"connector_id": connector_id},
        )
    decoded = vault.decrypt(row["encrypted_payload"])
    if row.get("expires_at"):
        decoded["_expires_at"] = row["expires_at"]
    return decoded


def update_partial(connector_id: str, patch: Dict[str, Any], expires_at: Optional[datetime] = None) -> Dict[str, Any]:
    """Merge `patch` into the existing decrypted payload and persist.

    Used after token refresh: only `access_token` and `expires_in` change,
    `refresh_token` typically stays the same (or is rotated by the provider).
    """
    current = load_credentials(connector_id)
    current.pop("_expires_at", None)
    current.update(patch)
    save_credentials(connector_id, current, expires_at=expires_at)
    current["_expires_at"] = expires_at
    return current


def delete_credentials(connector_id: str) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM connector_credentials WHERE connector_id = %s", (connector_id,))
        conn.commit()
