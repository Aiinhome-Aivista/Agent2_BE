"""Rate-limit-aware HTTP client.

Wraps `httpx` to add:
  - Per-call retries with exponential backoff + jitter on transient errors
  - Honours `Retry-After` header on 429
  - Auto-refresh hook on 401 (subclasses inject a `refresh_callback`)
  - Structured logging of every request (sanitised — no auth headers)
  - Timeout enforcement

Designed for synchronous use from Celery workers and FastAPI request handlers
(via `run_in_threadpool` if called from async context).
"""
from __future__ import annotations

import random
import time
from typing import Any, Callable, Dict, Optional

import httpx

from app.connectors.base.exceptions import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorRateLimitError,
)
from app.core.logger import logger


# A small set of statuses we consider safe to retry idempotently.
_RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}
_RETRYABLE_METHODS = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}


class HttpClient:
    """Thin httpx wrapper with retry logic and 401 refresh hook."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        default_headers: Optional[Dict[str, str]] = None,
        refresh_callback: Optional[Callable[[], Dict[str, str]]] = None,
        auth: Optional[Any] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self._default_headers = default_headers or {}
        self._refresh_callback = refresh_callback
        self._client = httpx.Client(timeout=timeout, follow_redirects=True, auth=auth)

    # ------------------------------------------------------------------ helpers
    def update_headers(self, headers: Dict[str, str]) -> None:
        """Replace or extend default headers (e.g. after token refresh)."""
        self._default_headers.update(headers)

    @staticmethod
    def _backoff_delay(attempt: int, base: float, cap: float, retry_after: Optional[float]) -> float:
        if retry_after is not None:
            return min(float(retry_after), cap)
        # Full jitter: random between 0 and min(cap, base * 2**attempt)
        upper = min(cap, base * (2 ** attempt))
        return random.uniform(0, upper)

    @staticmethod
    def _safe_log_headers(headers: Dict[str, str]) -> Dict[str, str]:
        """Strip auth headers before logging."""
        return {
            k: ("***" if k.lower() in {"authorization", "x-api-key", "cookie"} else v)
            for k, v in headers.items()
        }

    # ---------------------------------------------------------------- requests
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        absolute_url: bool = False,
    ) -> httpx.Response:
        url = path if absolute_url else f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        method = method.upper()
        merged_headers: Dict[str, str] = {**self._default_headers, **(headers or {})}

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(
                    f"[http] {method} {url} attempt={attempt + 1} "
                    f"params={params} headers={self._safe_log_headers(merged_headers)}"
                )
                response = self._client.request(
                    method, url, params=params, json=json, headers=merged_headers
                )
            except httpx.TimeoutException as e:
                last_exc = e
                if attempt >= self.max_retries or method not in _RETRYABLE_METHODS:
                    raise ConnectorError(f"Timeout calling {url}") from e
                delay = self._backoff_delay(attempt, self.backoff_base, self.backoff_cap, None)
                logger.warning(f"[http] timeout, retrying in {delay:.2f}s")
                time.sleep(delay)
                continue
            except httpx.HTTPError as e:
                last_exc = e
                if attempt >= self.max_retries:
                    raise ConnectorError(f"HTTP error calling {url}: {e}") from e
                delay = self._backoff_delay(attempt, self.backoff_base, self.backoff_cap, None)
                time.sleep(delay)
                continue

            # ---- 401: try a single refresh, then a single retry --------------
            if response.status_code == 401 and self._refresh_callback and attempt == 0:
                logger.info(f"[http] 401 from {url} — attempting token refresh")
                try:
                    new_headers = self._refresh_callback()
                    self.update_headers(new_headers)
                    merged_headers.update(new_headers)
                    continue
                except Exception as e:  # noqa: BLE001
                    raise ConnectorAuthError(f"Token refresh failed: {e}") from e

            # ---- Retryable status -------------------------------------------
            if response.status_code in _RETRYABLE_STATUSES and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After")
                try:
                    retry_after_f: Optional[float] = float(retry_after) if retry_after else None
                except (TypeError, ValueError):
                    retry_after_f = None
                delay = self._backoff_delay(attempt, self.backoff_base, self.backoff_cap, retry_after_f)
                logger.warning(
                    f"[http] {method} {url} -> {response.status_code}, retrying in {delay:.2f}s"
                )
                time.sleep(delay)
                continue

            # ---- Final outcome ----------------------------------------------
            return self._handle_response(response, url)

        if last_exc:
            raise ConnectorError(f"Exhausted retries calling {url}") from last_exc
        raise ConnectorError(f"Exhausted retries calling {url}")

    @staticmethod
    def _handle_response(response: httpx.Response, url: str) -> httpx.Response:
        if response.status_code == 401:
            raise ConnectorAuthError(f"Unauthorized calling {url}")
        if response.status_code == 404:
            raise ConnectorNotFoundError(f"Not found: {url}")
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "60"))
            raise ConnectorRateLimitError(f"Rate limited at {url}", retry_after=retry_after)
        if response.status_code >= 400:
            body_preview = response.text[:500]
            raise ConnectorError(
                f"{response.status_code} {response.reason_phrase} from {url}",
                {"body": body_preview},
            )
        return response

    # --------------------------------------------------------- convenience verbs
    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
