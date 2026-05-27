"""Credential vault using Fernet symmetric encryption.

Why Fernet?
    - Authenticated encryption (AES-128-CBC + HMAC-SHA256) — tampering detected.
    - URL-safe base64 ciphertext, easy to store in BLOB columns.
    - Key rotation supported via `MultiFernet` (we use `key_version` column).

Key management:
    For single-tenant self-hosted (current mode), the master key is read from
    `CREDENTIAL_MASTER_KEY` env var. Generate one with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    For multi-tenant SaaS later, swap `_get_fernet()` for a per-tenant key
    loader backed by AWS KMS / Vault / similar. The public API of this
    module does not change.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Dict

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core import settings
from app.core.exceptions import AppException


class CredentialDecryptError(AppException):
    status_code = 500
    code = "CREDENTIAL_DECRYPT_ERROR"


class CredentialVault:
    """Encrypts and decrypts JSON-serialisable credential payloads."""

    def __init__(self, fernet: MultiFernet) -> None:
        self._fernet = fernet

    def encrypt(self, payload: Dict[str, Any]) -> bytes:
        plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self._fernet.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> Dict[str, Any]:
        try:
            plaintext = self._fernet.decrypt(ciphertext)
        except InvalidToken as e:
            raise CredentialDecryptError(
                "Failed to decrypt credentials — wrong master key or tampered ciphertext"
            ) from e
        return json.loads(plaintext.decode("utf-8"))

    def rotate(self, ciphertext: bytes) -> bytes:
        """Re-encrypt with the current primary key (after key rotation)."""
        return self._fernet.rotate(ciphertext)


@lru_cache(maxsize=1)
def get_vault() -> CredentialVault:
    """Get the singleton vault instance.

    Reads `CREDENTIAL_MASTER_KEY` (primary) and optionally
    `CREDENTIAL_PREVIOUS_KEYS` (comma-separated, oldest-last) for rotation.
    """
    primary = settings.credential_master_key.strip()
    if not primary:
        raise RuntimeError(
            "CREDENTIAL_MASTER_KEY env var is required. "
            "Generate one with: python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'"
        )

    keys = [Fernet(primary.encode())]
    previous = os.environ.get("CREDENTIAL_PREVIOUS_KEYS", "").strip()
    if previous:
        for k in previous.split(","):
            k = k.strip()
            if k:
                keys.append(Fernet(k.encode()))

    return CredentialVault(MultiFernet(keys))


def generate_master_key() -> str:
    """Helper for first-time setup. Generates a fresh Fernet key."""
    return Fernet.generate_key().decode()
