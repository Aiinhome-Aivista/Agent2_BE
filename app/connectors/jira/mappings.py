"""Jira ↔ local field mappings.

Each mapping declares how to translate a value from one side to the other.
Users can override these via the Field Mappings UI; the rules below are
the defaults applied if no override is present.

Two transforms are supported:

  * `value_map`   — a dict like {"P1": "Highest", "P2": "High"} applied
                    left→right inbound and right→left outbound.
  * `path`        — a JSONPath-lite (dot notation) describing where in the
                    Jira issue payload the value lives, e.g.
                    'fields.priority.name'.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---- Default value mappings ------------------------------------------------
PRIORITY_TO_JIRA = {"P1": "Highest", "P2": "High", "P3": "Medium", "P4": "Low"}
PRIORITY_FROM_JIRA = {v: k for k, v in PRIORITY_TO_JIRA.items()}
PRIORITY_FROM_JIRA["Lowest"] = "P4"  # extra alias

STATUS_TO_JIRA_TRANSITION = {
    # Local status → name of the Jira transition we should fire.
    # Specific transition IDs vary by workflow; we look up the ID at runtime.
    "new": "To Do",
    "analyzing": "In Progress",
    "remediating": "In Progress",
    "resolved": "Done",
    "escalated": "In Progress",
    "closed": "Done",
}

STATUS_FROM_JIRA = {
    # Jira status name → local status. `lower()` is applied before lookup.
    "to do": "new",
    "open": "new",
    "in progress": "analyzing",
    "blocked": "escalated",
    "done": "resolved",
    "closed": "closed",
    "resolved": "resolved",
}


# ---- Path resolver ---------------------------------------------------------
def get_path(obj: Dict[str, Any], dotted_path: str, default: Any = None) -> Any:
    """Read 'fields.priority.name' style paths. Returns default on miss."""
    cur: Any = obj
    for part in dotted_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


# ---- Translators -----------------------------------------------------------
def jira_to_local(issue: Dict[str, Any], custom_mappings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Translate a Jira issue payload to local fields.

    `custom_mappings` is an optional list of rows from connector_field_mappings;
    when present they override defaults.
    """
    from app.connectors.jira.api_client import adf_to_plain

    fields = issue.get("fields", {}) or {}
    out: Dict[str, Any] = {
        "external_id": issue.get("id"),
        "external_key": issue.get("key"),
        "subject": fields.get("summary") or "",
        "description": adf_to_plain(fields.get("description")),
        "priority": PRIORITY_FROM_JIRA.get(get_path(fields, "priority.name") or "", "P3"),
        "status": STATUS_FROM_JIRA.get(
            (get_path(fields, "status.name") or "").lower(), "new"
        ),
        "category": get_path(fields, "issuetype.name") or "Uncategorised",
        "tags": fields.get("labels") or [],
        "caller": (
            get_path(fields, "reporter.displayName")
            or get_path(fields, "reporter.emailAddress")
            or "unknown"
        ),
        "caller_email": get_path(fields, "reporter.emailAddress"),
    }

    # Apply custom mappings (overrides + extension fields).
    # Path resolution tries, in order:
    #   1. issue.{path}      — if path starts with "fields." or another top-level key like "key"
    #   2. fields.{path}     — convenience: 'priority.name' resolves under fields
    for mapping in custom_mappings or []:
        if mapping.get("direction") not in ("inbound", "both"):
            continue
        local_field = mapping["local_field"]
        remote_path = mapping["remote_field"]
        sentinel = object()
        value: Any = get_path(issue, remote_path, default=sentinel)
        if value is sentinel:
            value = get_path(fields, remote_path, default=None)
        transform = mapping.get("transform") or {}
        value_map = transform.get("value_map") if isinstance(transform, dict) else None
        if isinstance(value_map, dict) and isinstance(value, str):
            value = value_map.get(value, value)
        out[local_field] = value

    return out


def local_to_jira_fields(local: Dict[str, Any], custom_mappings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Translate a local incident dict to a Jira `fields` patch."""
    out: Dict[str, Any] = {}
    if local.get("subject"):
        out["summary"] = local["subject"]
    if local.get("description"):
        # Real Jira PUT requires ADF, but the api_client wraps when creating.
        # For updates we send raw and let the client transform if needed.
        out["description"] = local["description"]
    if local.get("priority"):
        jira_pri = PRIORITY_TO_JIRA.get(local["priority"])
        if jira_pri:
            out["priority"] = {"name": jira_pri}
    if local.get("tags"):
        out["labels"] = local["tags"]

    for mapping in custom_mappings or []:
        if mapping.get("direction") not in ("outbound", "both"):
            continue
        local_field = mapping["local_field"]
        remote_field = mapping["remote_field"]
        if local_field not in local:
            continue
        value = local[local_field]
        transform = mapping.get("transform") or {}
        value_map = transform.get("value_map") if isinstance(transform, dict) else None
        if isinstance(value_map, dict):
            # Outbound = invert the map
            inverted = {v: k for k, v in value_map.items()}
            value = inverted.get(value, value)
        # For now we only support fields under 'fields.*' (no nested writes)
        leaf = remote_field.split(".")[-1]
        out[leaf] = value

    return out
