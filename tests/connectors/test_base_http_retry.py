"""Test the HTTP client's retry behaviour without hitting the network."""
from unittest.mock import patch

import httpx
import pytest

from app.connectors.base.exceptions import ConnectorError, ConnectorRateLimitError
from app.connectors.base.http_client import HttpClient


def _make_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, headers=headers or {}, request=httpx.Request("GET", "https://x"))


def test_retries_on_503_then_succeeds():
    client = HttpClient("https://example.com", max_retries=3, backoff_base=0.001)
    responses = [_make_response(503), _make_response(503), _make_response(200)]
    calls = {"n": 0}

    def fake_request(*_args, **_kwargs):
        i = calls["n"]
        calls["n"] += 1
        return responses[i]

    with patch.object(client._client, "request", side_effect=fake_request):
        resp = client.get("/foo")
    assert resp.status_code == 200
    assert calls["n"] == 3


def test_429_with_retry_after_honored():
    client = HttpClient("https://example.com", max_retries=2, backoff_base=0.001, backoff_cap=0.05)

    # All 429s — should raise ConnectorRateLimitError on final attempt.
    def fake_request(*_args, **_kwargs):
        return _make_response(429, headers={"Retry-After": "0"})

    with patch.object(client._client, "request", side_effect=fake_request):
        with pytest.raises(ConnectorRateLimitError) as ei:
            client.get("/foo")
    assert ei.value.retry_after == 0


def test_500_exhausted_raises_connector_error():
    client = HttpClient("https://example.com", max_retries=1, backoff_base=0.001)
    with patch.object(client._client, "request", return_value=_make_response(500)):
        with pytest.raises(ConnectorError):
            client.get("/foo")


def test_401_triggers_refresh_callback_then_retries():
    refreshed = {"called": False}

    def refresh():
        refreshed["called"] = True
        return {"Authorization": "Bearer NEW"}

    client = HttpClient(
        "https://example.com",
        max_retries=2,
        backoff_base=0.001,
        default_headers={"Authorization": "Bearer OLD"},
        refresh_callback=refresh,
    )

    seq = [_make_response(401), _make_response(200)]
    calls = {"n": 0}

    def fake_request(*_args, **_kwargs):
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    with patch.object(client._client, "request", side_effect=fake_request):
        resp = client.get("/foo")
    assert resp.status_code == 200
    assert refreshed["called"] is True
