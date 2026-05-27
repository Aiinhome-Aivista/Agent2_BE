"""Smoke test for security utilities — runs without DB."""
from datetime import timedelta

import pytest

from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        h = hash_password("super-secret-1234")
        assert h != "super-secret-1234"
        assert verify_password("super-secret-1234", h)
        assert not verify_password("wrong", h)

    def test_different_salts_produce_different_hashes(self):
        a = hash_password("password")
        b = hash_password("password")
        assert a != b
        assert verify_password("password", a)
        assert verify_password("password", b)


class TestJWT:
    def test_access_token_roundtrip(self):
        token = create_access_token("user-123", {"role": "admin"})
        payload = decode_token(token)
        assert payload["sub"] == "user-123"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_invalid_token_rejected(self):
        from app.core.exceptions import UnauthorizedError

        with pytest.raises(UnauthorizedError):
            decode_token("not-a-valid-token")
