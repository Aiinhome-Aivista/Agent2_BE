"""Triage agent classifier — exercised without DB by skipping record_step."""
from unittest.mock import patch

from app.agents.triage_agent import TriageAgent


@patch("app.agents.base.IncidentRepository.add_step")
@patch("app.agents.base.AuditRepository.log")
def test_database_incident_is_classified_as_database(_mock_log, _mock_step):
    agent = TriageAgent()
    incident = {
        "id": "INC-TEST-001",
        "subject": "Cannot connect to RDS database",
        "description": "Production MySQL on RDS is refusing connections from app servers.",
    }
    patch_result = agent.run(incident)
    assert patch_result["category"] == "Database"
    assert patch_result["priority"] in {"P1", "P2"}
    assert 0 < patch_result["confidence"] <= 1


@patch("app.agents.base.IncidentRepository.add_step")
@patch("app.agents.base.AuditRepository.log")
def test_p1_when_production_keyword_present(_mock_log, _mock_step):
    agent = TriageAgent()
    incident = {
        "id": "INC-TEST-002",
        "subject": "PRODUCTION OUTAGE — all users affected",
        "description": "Critical outage in production. Company-wide impact.",
    }
    patch_result = agent.run(incident)
    assert patch_result["priority"] == "P1"
