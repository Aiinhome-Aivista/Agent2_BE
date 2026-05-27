"""ConnectorService — orchestrates connector lifecycle.

Sits between the REST endpoints and the connector framework. Handles:
    create / list / get / delete                     (CRUD on connectors)
    start_oauth / handle_oauth_callback              (OAuth dance)
    health_check / sync_now                          (operations)
    register_webhook / list_webhook_events           (webhooks)
    list_field_mappings / replace_field_mappings     (mappings)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.connectors import get_meta, instantiate, list_providers
from app.connectors.base import (
    ConnectorAuthError,
    ConnectorConfigError,
    consume_state,
    delete_credentials,
    save_credentials,
    generate_webhook_secret,
    start_flow,
)
from app.core.logger import logger
from app.repositories.connector_repository import ConnectorRepository


def _redirect_uri() -> str:
    """The OAuth callback URL configured on the provider's app settings."""
    base = os.environ.get("APP_PUBLIC_BASE_URL", "https://122.163.121.176:3019").rstrip("/")
    return f"{base}/api/v1/connectors/oauth/callback"


def _webhook_url(connector_id: str, webhook_secret: str) -> str:
    """The URL we register with providers for inbound webhooks."""
    base = os.environ.get("WEBHOOK_PUBLIC_BASE_URL", os.environ.get("APP_PUBLIC_BASE_URL", "https://122.163.121.176:3019")).rstrip("/")
    return f"{base}/api/v1/connectors/{connector_id}/webhook?token={webhook_secret}"


class ConnectorService:
    # ============================================================== Catalog
    @staticmethod
    def list_providers() -> List[Dict[str, Any]]:
        return list_providers()

    # ================================================================== CRUD
    @staticmethod
    def create(provider: str, name: str, config: Dict[str, Any], user_id: Optional[str]) -> Dict[str, Any]:
        # Validate provider exists in registry
        meta = get_meta(provider)
        webhook_secret = generate_webhook_secret() if "webhooks" in meta.capabilities else None
        return ConnectorRepository.create(
            provider=provider,
            name=name,
            config=config,
            created_by=user_id,
            webhook_secret=webhook_secret,
        )

    @staticmethod
    def list(provider: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return ConnectorRepository.list(provider=provider, status=status)

    @staticmethod
    def get(connector_id: str) -> Optional[Dict[str, Any]]:
        return ConnectorRepository.find_by_id(connector_id)

    @staticmethod
    def update(connector_id: str, patch: Dict[str, Any]) -> None:
        ConnectorRepository.update(connector_id, patch)

    @staticmethod
    def delete(connector_id: str) -> None:
        # Best-effort: try to clean up creds (cascade also handles it).
        try:
            delete_credentials(connector_id)
        except Exception:  # noqa: BLE001
            pass
        ConnectorRepository.delete(connector_id)

    # ================================================================== OAuth
    @staticmethod
    def start_oauth(connector_id: str, user_id: Optional[str]) -> Dict[str, str]:
        """Generate state + return the authorize URL for the browser to redirect to."""
        connector_row = ConnectorRepository.find_by_id(connector_id)
        if not connector_row:
            raise ConnectorConfigError(f"Connector {connector_id} not found")
        provider = connector_row["provider"]

        # Generate state + PKCE in DB
        flow = start_flow(
            provider=provider,
            user_id=user_id,
            payload={"connector_id": connector_id},
            use_pkce=(provider in {"salesforce"}),  # PKCE required for SF; optional elsewhere
        )

        connector = instantiate(provider, connector_id, connector_row.get("config") or {})
        authorize_url = connector.build_authorize_url(_redirect_uri(), flow)
        ConnectorRepository.update(connector_id, {"status": "connecting"})
        return {"authorize_url": authorize_url, "state": flow["state"]}

    @staticmethod
    def handle_oauth_callback(state: str, code: str) -> Dict[str, Any]:
        """Look up state, exchange code, persist credentials. Returns connector_id."""
        state_row = consume_state(state)
        provider = state_row["provider"]
        payload = state_row.get("payload") or {}
        connector_id = payload.get("connector_id")
        if not connector_id:
            raise ConnectorAuthError("OAuth state had no connector_id payload")

        connector_row = ConnectorRepository.find_by_id(connector_id)
        if not connector_row:
            raise ConnectorAuthError(f"Connector {connector_id} no longer exists")

        connector = instantiate(provider, connector_id, connector_row.get("config") or {})
        try:
            connector.exchange_code(
                code=code,
                redirect_uri=_redirect_uri(),
                state_payload={
                    "code_verifier": state_row.get("code_verifier") or "",
                },
            )
        except Exception as e:
            ConnectorRepository.update(connector_id, {"status": "error", "last_error": str(e)[:500]})
            raise

        return {"connector_id": connector_id, "provider": provider}

    @staticmethod
    def connect_basic(connector_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Verify basic auth credentials and persist them."""
        connector_row = ConnectorRepository.find_by_id(connector_id)
        if not connector_row:
            raise ConnectorConfigError(f"Connector {connector_id} not found")

        provider = connector_row["provider"]

        # Save frontend-provided credentials encrypted
        save_credentials(connector_id, payload)

        # Save non-secret config for UI/use
        config_patch = {}

        if payload.get("site_url"):
            config_patch["site_url"] = payload.get("site_url")

        if payload.get("project_key"):
            config_patch["project_key"] = payload.get("project_key")

        if config_patch:
            old_config = connector_row.get("config") or {}
            ConnectorRepository.update(
                connector_id,
                {"config": {**old_config, **config_patch}}
            )
            connector_row["config"] = {**old_config, **config_patch}

        connector = instantiate(provider, connector_id, connector_row.get("config") or {})

        try:
            result = connector.health_check()
            if result.get("ok"):
                ConnectorRepository.update(connector_id, {"status": "connected", "last_error": None})
                return {"ok": True}
            else:
                error_msg = result.get("error", "Health check failed")
                ConnectorRepository.update(connector_id, {"status": "error", "last_error": error_msg})
                return {"ok": False, "error": error_msg}
        except Exception as e:
            error_msg = str(e)
            ConnectorRepository.update(connector_id, {"status": "error", "last_error": error_msg})
            return {"ok": False, "error": error_msg}

    # =============================================================== Health
    @staticmethod
    def health_check(connector_id: str) -> Dict[str, Any]:
        connector_row = ConnectorRepository.find_by_id(connector_id)
        if not connector_row:
            return {"ok": False, "error": "connector not found"}
        connector = instantiate(connector_row["provider"], connector_id, connector_row.get("config") or {})
        result = connector.health_check()
        if not result.get("ok"):
            ConnectorRepository.update(connector_id, {"status": "error", "last_error": result.get("error", "")[:500]})
        else:
            ConnectorRepository.update(connector_id, {"status": "connected", "last_error": None})
        return result

    # ============================================================== Sync
    @staticmethod
    def sync_now(connector_id: str, since: Optional[str] = None) -> Dict[str, Any]:
        """Run one polling cycle synchronously. Returns a summary."""
        connector_row = ConnectorRepository.find_by_id(connector_id)
        if not connector_row:
            return {"ok": False, "error": "connector not found"}
        connector = instantiate(connector_row["provider"], connector_id, connector_row.get("config") or {})
        try:
            payloads = connector.poll_for_changes(since=since)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[connector-service] poll failed for {connector_id}: {e}")
            ConnectorRepository.touch_sync(connector_id, error=str(e)[:300])
            return {"ok": False, "error": str(e)[:300]}

        applied = 0
        errors: List[str] = []
        for p in payloads:
            try:
                if connector_row["provider"] in ("jira", "servicenow", "salesforce"):
                    # These connectors have sync_inbound helpers
                    connector.sync_inbound(p)  # type: ignore[attr-defined]
                applied += 1
            except Exception as e:  # noqa: BLE001
                errors.append(str(e)[:200])
        ConnectorRepository.touch_sync(connector_id, error=("; ".join(errors)[:500] if errors else None))
        return {"ok": True, "fetched": len(payloads), "applied": applied, "errors": errors}

    # ========================================================== Webhooks
    @staticmethod
    def register_webhook(connector_id: str) -> Dict[str, Any]:
        connector_row = ConnectorRepository.find_by_id(connector_id)
        if not connector_row:
            raise ConnectorConfigError(f"Connector {connector_id} not found")
        if not connector_row.get("webhook_secret"):
            secret = generate_webhook_secret()
            ConnectorRepository.update(connector_id, {"webhook_secret": secret})
            connector_row["webhook_secret"] = secret

        url = _webhook_url(connector_id, connector_row["webhook_secret"])
        connector = instantiate(connector_row["provider"], connector_id, connector_row.get("config") or {})

        if connector_row["provider"] == "jira":
            api = connector.api()  # type: ignore[attr-defined]
            return api.register_webhook(callback_url=url)
        return {"ok": False, "error": f"register_webhook not implemented for {connector_row['provider']}",
                "url": url}

    @staticmethod
    def list_webhook_events(connector_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        return ConnectorRepository.list_webhook_events(connector_id, limit)

    # ===================================================== Field mappings
    @staticmethod
    def list_field_mappings(connector_id: str) -> List[Dict[str, Any]]:
        return ConnectorRepository.list_mappings(connector_id)

    @staticmethod
    def replace_field_mappings(connector_id: str, mappings: List[Dict[str, Any]]) -> None:
        ConnectorRepository.replace_mappings(connector_id, mappings)
