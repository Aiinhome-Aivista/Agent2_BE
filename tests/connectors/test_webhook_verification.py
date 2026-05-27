"""Webhook signature/token verification tests — no network."""
import base64
import hashlib
import hmac

from app.connectors.base.webhooks import (
    verify_hmac_sha256_base64,
    verify_hmac_sha256_hex,
    verify_url_token,
    generate_webhook_secret,
)


SECRET = "test-secret-12345"


def test_verify_hmac_sha256_hex_valid():
    body = b'{"event":"updated"}'
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_hmac_sha256_hex(body, sig, SECRET) is True


def test_verify_hmac_sha256_hex_invalid():
    body = b'{"event":"updated"}'
    assert verify_hmac_sha256_hex(body, "deadbeef" * 8, SECRET) is False


def test_verify_hmac_sha256_hex_with_prefix():
    body = b'data'
    sig_raw = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_hmac_sha256_hex(body, f"sha256={sig_raw}", SECRET, prefix="sha256=") is True


def test_verify_hmac_sha256_base64_valid():
    body = b'{"hub":"spot"}'
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    sig = base64.b64encode(digest).decode()
    assert verify_hmac_sha256_base64(body, sig, SECRET) is True


def test_verify_hmac_sha256_base64_missing_header():
    assert verify_hmac_sha256_base64(b'body', None, SECRET) is False


def test_verify_url_token_constant_time():
    secret = generate_webhook_secret()
    assert verify_url_token(secret, secret) is True
    assert verify_url_token(secret + "x", secret) is False
    assert verify_url_token("", secret) is False
    assert verify_url_token(secret, "") is False


def test_generate_webhook_secret_unique():
    a = generate_webhook_secret()
    b = generate_webhook_secret()
    assert a != b
    assert len(a) >= 32
