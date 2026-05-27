"""Connector registry — single source of truth mapping `provider` strings
to their concrete `BaseConnector` subclass.

Adding a new provider is a 3-line edit here plus the connector module itself.
"""
from __future__ import annotations

from typing import Any, Dict, List, Type

from app.connectors.base.connector import BaseConnector, ConnectorMeta
from app.connectors.hubspot import HubSpotConnector
from app.connectors.hubspot import META as HUBSPOT_META
from app.connectors.jira import JiraConnector
from app.connectors.jira import META as JIRA_META
from app.connectors.salesforce import SalesforceConnector
from app.connectors.salesforce import META as SALESFORCE_META
from app.connectors.servicenow import ServiceNowConnector
from app.connectors.servicenow import META as SERVICENOW_META
from app.connectors.zoho import META as ZOHO_META
from app.connectors.zoho import ZohoConnector


_REGISTRY: Dict[str, Type[BaseConnector]] = {
    "jira": JiraConnector,
    "servicenow": ServiceNowConnector,
    "salesforce": SalesforceConnector,
    "hubspot": HubSpotConnector,
    "zoho": ZohoConnector,
}

_META_REGISTRY: Dict[str, ConnectorMeta] = {
    "jira": JIRA_META,
    "servicenow": SERVICENOW_META,
    "salesforce": SALESFORCE_META,
    "hubspot": HUBSPOT_META,
    "zoho": ZOHO_META,
}

# Maturity tags shown in the UI so users know what they're getting.
_MATURITY: Dict[str, str] = {
    "jira": "production",
    "servicenow": "scaffold",
    "salesforce": "scaffold",
    "hubspot": "scaffold",
    "zoho": "scaffold",
}


def get_connector_class(provider: str) -> Type[BaseConnector]:
    """Look up the connector class for a provider string."""
    if provider not in _REGISTRY:
        raise KeyError(f"Unknown connector provider: {provider}")
    return _REGISTRY[provider]


def instantiate(provider: str, connector_id: str, config: Dict[str, Any]) -> BaseConnector:
    """Build a connector instance bound to a connector_id + config."""
    klass = get_connector_class(provider)
    return klass(connector_id=connector_id, config=config or {})


def list_providers() -> List[Dict[str, Any]]:
    """All available providers + metadata for the UI catalog."""
    out: List[Dict[str, Any]] = []
    for provider, meta in _META_REGISTRY.items():
        out.append({
            "provider": meta.provider,
            "display_name": meta.display_name,
            "description": meta.description,
            "auth_type": meta.auth_type,
            "docs_url": meta.docs_url,
            "icon": meta.icon,
            "capabilities": meta.capabilities,
            "required_config": meta.required_config,
            "maturity": _MATURITY.get(provider, "scaffold"),
        })
    return out


def get_meta(provider: str) -> ConnectorMeta:
    if provider not in _META_REGISTRY:
        raise KeyError(f"Unknown connector provider: {provider}")
    return _META_REGISTRY[provider]
