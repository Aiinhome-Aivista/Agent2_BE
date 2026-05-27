"""Jira field-mapping tests — pure unit tests, no DB or network."""
from app.connectors.jira.mappings import (
    PRIORITY_FROM_JIRA,
    PRIORITY_TO_JIRA,
    STATUS_FROM_JIRA,
    get_path,
    jira_to_local,
    local_to_jira_fields,
)


SAMPLE_JIRA_ISSUE = {
    "id": "10001",
    "key": "OPS-42",
    "fields": {
        "summary": "Database connection pool exhausted",
        "description": {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Pool size is 20, traffic spiked at 14:00 UTC."}]}
            ],
        },
        "status": {"name": "In Progress"},
        "priority": {"name": "Highest"},
        "issuetype": {"name": "Incident"},
        "labels": ["prod", "database"],
        "reporter": {
            "displayName": "Aria Patel",
            "emailAddress": "aria@example.com",
        },
    },
}


def test_get_path_basic():
    assert get_path(SAMPLE_JIRA_ISSUE, "fields.summary") == "Database connection pool exhausted"
    assert get_path(SAMPLE_JIRA_ISSUE, "fields.priority.name") == "Highest"
    assert get_path(SAMPLE_JIRA_ISSUE, "fields.does.not.exist", default="x") == "x"


def test_jira_to_local_priority_mapping():
    out = jira_to_local(SAMPLE_JIRA_ISSUE)
    assert out["priority"] == "P1"
    assert PRIORITY_TO_JIRA["P1"] == "Highest"
    assert PRIORITY_FROM_JIRA["Highest"] == "P1"


def test_jira_to_local_status_lowercased_lookup():
    out = jira_to_local(SAMPLE_JIRA_ISSUE)
    assert out["status"] == "analyzing"
    assert STATUS_FROM_JIRA["in progress"] == "analyzing"


def test_jira_to_local_extracts_full_issue():
    out = jira_to_local(SAMPLE_JIRA_ISSUE)
    assert out["external_id"] == "10001"
    assert out["external_key"] == "OPS-42"
    assert out["subject"] == "Database connection pool exhausted"
    assert "Pool size is 20" in out["description"]
    assert out["category"] == "Incident"
    assert out["tags"] == ["prod", "database"]
    assert out["caller"] == "Aria Patel"
    assert out["caller_email"] == "aria@example.com"


def test_local_to_jira_fields_priority_invert():
    fields = local_to_jira_fields({"subject": "x", "priority": "P2", "tags": ["a"]})
    assert fields["summary"] == "x"
    assert fields["priority"] == {"name": "High"}
    assert fields["labels"] == ["a"]


def test_jira_to_local_handles_missing_fields():
    minimal = {"id": "1", "key": "OPS-1", "fields": {}}
    out = jira_to_local(minimal)
    assert out["subject"] == ""
    assert out["priority"] == "P3"  # default
    assert out["status"] == "new"   # default


def test_custom_inbound_value_map_overrides_default():
    custom = [{
        "local_field": "priority",
        "remote_field": "priority.name",
        "direction": "inbound",
        "transform": {"value_map": {"Highest": "P0"}},  # custom escalation level
    }]
    out = jira_to_local(SAMPLE_JIRA_ISSUE, custom_mappings=custom)
    assert out["priority"] == "P0"
