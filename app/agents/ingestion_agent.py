"""Ingestion Agent — normalises incoming data from any source channel."""
from typing import Any, Dict

from app.agents.base import BaseAgent


class IngestionAgent(BaseAgent):
    """Parses incoming data from ITSM systems, monitoring alerts, user chat,
    email and webhooks into a unified incident schema.
    """

    name = "Ingestion Agent"

    # Heuristic mapping — production deployments would replace this with
    # source-specific adapters/parsers.
    SOURCE_KEYWORDS = {
        "monitoring": ["alert", "alarm", "threshold", "datadog", "splunk", "metric"],
        "itsm": ["servicenow", "jira", "ticket", "request"],
        "email": ["@", "from:", "subject:"],
    }

    def run(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        # If source already specified by caller, just observe it.
        text = f"{incident.get('subject', '')} {incident.get('description', '')}".lower()
        inferred_source = incident.get("source") or "user_chat"

        if not incident.get("source"):
            for src, kws in self.SOURCE_KEYWORDS.items():
                if any(kw in text for kw in kws):
                    inferred_source = src
                    break

        self.record_step(
            incident_id=incident["id"],
            action="Normalised incoming payload",
            output=(
                f"Identified source as '{inferred_source}'. "
                f"Caller: {incident.get('caller', 'unknown')}. "
                f"Description length: {len(incident.get('description', ''))} chars."
            ),
            step_type="observe",
            metadata={"detected_source": inferred_source},
        )

        return {"source": inferred_source}
