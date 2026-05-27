"""Credential vault tests — verifies encryption/decryption roundtrip and
key-rotation behaviour."""
import os

from cryptography.fernet import Fernet

# Set the master key BEFORE importing the vault (it's read in get_vault).
os.environ["CREDENTIAL_MASTER_KEY"] = Fernet.generate_key().decode()

from app.core.crypto import (  # noqa: E402
    CredentialDecryptError,
    CredentialVault,
    generate_master_key,
    get_vault,
)


def test_roundtrip_simple_payload():
    vault = get_vault()
    payload = {"access_token": "abc", "refresh_token": "xyz", "scope": "read write"}
    cipher = vault.encrypt(payload)
    assert isinstance(cipher, bytes)
    assert cipher != payload
    decoded = vault.decrypt(cipher)
    assert decoded == payload


def test_roundtrip_unicode_and_nesting():
    vault = get_vault()
    payload = {
        "user": "Aurélie 🌍",
        "nested": {"list": [1, 2, 3], "flag": True},
    }
    decoded = vault.decrypt(vault.encrypt(payload))
    assert decoded == payload


def test_decrypt_with_wrong_key_raises():
    other_key = Fernet.generate_key()
    other_vault = CredentialVault(__import__("cryptography.fernet", fromlist=["MultiFernet"]).MultiFernet([Fernet(other_key)]))
    cipher = other_vault.encrypt({"x": 1})

    import pytest
    with pytest.raises(CredentialDecryptError):
        get_vault().decrypt(cipher)


def test_generate_master_key_format():
    key = generate_master_key()
    assert isinstance(key, str)
    # Validates as a real Fernet key.
    Fernet(key.encode())
