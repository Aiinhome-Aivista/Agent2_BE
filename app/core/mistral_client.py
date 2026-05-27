"""Mistral LLM client.

A thin wrapper around the Mistral AI HTTP API. We use plain `httpx` rather
than the official `mistralai` SDK so we don't pin a major version dependency.

The client:
  * Reads MISTRAL_API_KEY from settings/env at first use
  * Retries on 429 / 5xx with exponential backoff
  * Returns structured JSON when `response_format={"type":"json_object"}`
    is requested (we ask the model to emit JSON we can parse)
  * Falls back gracefully if the API key is missing — agents that depend
    on it should call `is_configured()` first and skip work when False
    rather than crashing the orchestrator.

Models we use:
    mistral-small-latest    — analysis + classification (cheap, fast)
    mistral-large-latest    — opt-in for harder cases (set MISTRAL_MODEL)
"""
from __future__ import annotations

import json
import random
import time
from hashlib import sha256
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.core.logger import logger


_API_BASE = "https://api.mistral.ai/v1"


class MistralError(Exception):
    """Generic Mistral error — caller decides whether to fall back."""


class MistralClient:
    """Singleton-ish client. Instantiate via `get_mistral_client()`."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = "https://api.mistral.ai/v1",
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        if not api_key:
            raise MistralError("MISTRAL_API_KEY is not configured")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries

    # -------------------------------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Call /chat/completions. Returns the full response JSON.

        Always sets a low temperature for incident analysis — we want
        deterministic, reproducible diagnoses, not creativity.
        """
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            body["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            except httpx.HTTPError as e:
                last_error = e
                if attempt >= self.max_retries:
                    raise MistralError(f"Mistral HTTP error: {e}") from e
                time.sleep(_backoff(attempt))
                continue

            if resp.status_code == 200:
                return resp.json()

            # 429 or 5xx — retry
            if resp.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                wait = float(resp.headers.get("Retry-After") or _backoff(attempt))
                logger.warning(f"[mistral] {resp.status_code} — retrying in {wait:.2f}s")
                time.sleep(min(wait, 8.0))
                continue

            # Permanent failure
            preview = resp.text[:300]
            raise MistralError(f"Mistral API error {resp.status_code}: {preview}")

        # Should never reach here
        raise MistralError(f"Mistral exhausted retries: {last_error}")

    # -------------------------------------------------------------------------
    def analyze_incident(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        """Ask Mistral for a structured analysis of an incident.

        Returns a dict like:
            {
              "root_cause": "...",
              "confidence": 0.0-1.0,
              "resolution_summary": "one paragraph plan",
              "suggested_steps": ["step 1", "step 2", ...],
              "auto_resolvable": bool,
              "tokens_in": int, "tokens_out": int,
              "model": "mistral-small-latest"
            }

        On API errors raises MistralError — caller decides whether the
        orchestrator should continue without an analysis.
        """
        subj = (incident.get("subject") or "").strip()
        desc = (incident.get("description") or "").strip()
        category = incident.get("category") or "unknown"
        priority = incident.get("priority") or "P3"
        tags = ", ".join(incident.get("tags") or [])

        # Build the system + user messages. We ask for JSON output specifically
        # so we can parse it programmatically; the model is reliable at this.
        system_prompt = (
            "You are an experienced site reliability engineer triaging a production "
            "incident. Given the incident summary, propose the most likely root cause "
            "and a concrete resolution plan. Be precise and avoid speculation. "
            "Respond ONLY with valid JSON matching this schema:\n"
            "{\n"
            '  "root_cause": "single-sentence hypothesis",\n'
            '  "confidence": 0.0-1.0 (your confidence in the root cause),\n'
            '  "resolution_summary": "1-2 sentences describing the fix",\n'
            '  "suggested_steps": ["step 1 (imperative voice)", "step 2", ...],\n'
            '  "auto_resolvable": true|false (true only if steps are safe to run unattended),\n'
            '  "verification": "how to confirm the fix worked"\n'
            "}\n"
            "Keep suggested_steps to 3-7 items. Use imperative verbs ('Restart …', 'Verify …')."
        )

        user_prompt = (
            f"INCIDENT\n"
            f"Subject: {subj}\n"
            f"Category: {category}\n"
            f"Priority: {priority}\n"
            f"Tags: {tags}\n"
            f"\n"
            f"DESCRIPTION:\n{desc}\n"
        )

        start = time.perf_counter()
        response = self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.15,
            max_tokens=900,
            response_format={"type": "json_object"},
        )
        latency_ms = int((time.perf_counter() - start) * 1000)

        # Parse the assistant's content as JSON.
        content = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"[mistral] could not parse JSON response: {e}; raw={content[:200]}")
            # Best-effort fallback: return a minimal stub the caller can still use.
            return {
                "root_cause": "Mistral returned non-JSON output",
                "confidence": 0.0,
                "resolution_summary": content[:400] or "(empty response)",
                "suggested_steps": [],
                "auto_resolvable": False,
                "verification": "",
                "tokens_in": (response.get("usage") or {}).get("prompt_tokens", 0),
                "tokens_out": (response.get("usage") or {}).get("completion_tokens", 0),
                "latency_ms": latency_ms,
                "model": self.model,
                "prompt_hash": _hash_prompt(system_prompt, user_prompt),
            }

        # Coerce types / clamp values defensively (LLMs return surprising shapes).
        confidence = parsed.get("confidence")
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.0

        steps = parsed.get("suggested_steps") or []
        if not isinstance(steps, list):
            steps = [str(steps)]
        steps = [str(s).strip() for s in steps if str(s).strip()][:10]

        usage = response.get("usage") or {}
        return {
            "root_cause": str(parsed.get("root_cause") or "")[:500],
            "confidence": confidence,
            "resolution_summary": str(parsed.get("resolution_summary") or "")[:1000],
            "suggested_steps": steps,
            "auto_resolvable": bool(parsed.get("auto_resolvable", False)),
            "verification": str(parsed.get("verification") or "")[:500],
            "tokens_in": int(usage.get("prompt_tokens") or 0),
            "tokens_out": int(usage.get("completion_tokens") or 0),
            "latency_ms": latency_ms,
            "model": self.model,
            "prompt_hash": _hash_prompt(system_prompt, user_prompt),
        }

    # -------------------------------------------------------------------------
    def summarize_for_engineer(self, incident: Dict[str, Any], context: str) -> str:
        """Produce a one-paragraph human-friendly escalation summary for engineers.

        Used in escalation emails so on-call engineers get a quick overview
        instead of raw JSON.
        """
        system_prompt = (
            "You are an incident commander writing a brief handover note. "
            "Produce ONE paragraph (≤120 words) summarising the incident for "
            "the on-call engineer: what's broken, business impact, what's been "
            "tried, and what to do next. Be concrete. No bullet points."
        )
        user_prompt = (
            f"INCIDENT:\n"
            f"Subject: {incident.get('subject', '')}\n"
            f"Priority: {incident.get('priority', '')}\n"
            f"Category: {incident.get('category', '')}\n"
            f"Description: {incident.get('description', '')}\n"
            f"\n"
            f"CONTEXT (what the agent already did):\n{context}\n"
        )
        try:
            resp = self.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=300,
            )
            return ((resp.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
        except MistralError as e:
            logger.warning(f"[mistral] summarize failed, returning fallback: {e}")
            return f"High-priority {incident.get('category', '')} incident: {incident.get('subject', '')}"


# ===========================================================================
# Module-level helpers
# ===========================================================================
_client_cache: Optional[MistralClient] = None


def is_configured() -> bool:
    """True if MISTRAL_API_KEY is set. Agents check this to skip cleanly."""
    return bool((settings.mistral_api_key or "").strip())


def get_mistral_client() -> Optional[MistralClient]:
    """Return a cached client or None if no API key is set."""
    global _client_cache
    if _client_cache is not None:
        return _client_cache
    if not is_configured():
        return None
        
    mode = (settings.mistral_mode or "Cloud").lower()
    if mode == "local":
        base_url = settings.mistral_local_url or "http://122.163.121.176:3038"
        if not base_url.endswith("/v1"):
            base_url = f"{base_url.rstrip('/')}/v1"
        model = settings.mistral_local_model or "mistral:latest"
    else:
        base_url = "https://api.mistral.ai/v1"
        model = settings.model_name or "mistral-small-latest"
        
    _client_cache = MistralClient(
        api_key=settings.mistral_api_key,
        model=model,
        base_url=base_url,
    )
    return _client_cache


def reset_client_cache() -> None:
    """Forces the next get_mistral_client() to re-read settings. Used in tests."""
    global _client_cache
    _client_cache = None


# ===========================================================================
# Internals
# ===========================================================================
def _hash_prompt(system: str, user: str) -> str:
    h = sha256()
    h.update(system.encode("utf-8"))
    h.update(b"\x00")
    h.update(user.encode("utf-8"))
    return h.hexdigest()


def _backoff(attempt: int) -> float:
    """Full-jitter exponential backoff."""
    return random.uniform(0.0, min(8.0, 0.5 * (2 ** attempt)))
