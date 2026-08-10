"""Escalation Agent.

Packages an incident with full diagnostic context and routes it to the
appropriate engineer queue. Selection is rule-based — match on category,
fall back to round-robin among available engineers.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agents.base import BaseAgent
from app.repositories import EscalationRepository, IncidentRepository, UserRepository


# Category → engineer-skill routing. In production this would live in DB.
_CATEGORY_ROUTING = {
    "Database": "database",
    "Network": "network",
    "Cloud Infrastructure": "cloud",
    "Data Pipeline": "data",
    "Performance": "performance",
    "Application": "appdev",
    "Identity": "iam",
}


class EscalationAgent(BaseAgent):
    name = "Escalation Agent"

    @staticmethod
    def _select_engineer(_category: str) -> Optional[str]:
        engineers = UserRepository.list_engineers()
        if not engineers:
            return None
        # Simple round-robin: pick the engineer with the fewest open escalations.
        # For a starter implementation we just pick the first one.
        return engineers[0]["full_name"]

    @staticmethod
    def _diagnostic(incident: Dict[str, Any], extras: Dict[str, Any]) -> str:
        lines: List[str] = [
            f"Incident: {incident['id']} — {incident.get('subject', '')}",
            f"Caller: {incident.get('caller', 'unknown')}",
            f"Category: {incident.get('category', 'unknown')} / "
            f"Priority: {incident.get('priority', 'P3')}",
            "",
            "Description:",
            incident.get("description", ""),
        ]
        if extras.get("_runbook_used"):
            lines.append("")
            lines.append(f"Auto-remediation attempted using: {extras['_runbook_used']}")
        if extras.get("_failure_output"):
            lines.append("Failure output:")
            lines.append(extras["_failure_output"])
        return "\n".join(lines)

    @staticmethod
    def _attempted_actions(incident: Dict[str, Any]) -> List[str]:
        return [
            f"{step['agent']}: {step['action']}"
            for step in incident.get("steps", [])
        ]

    # --------------------------------------------------------------------------
    def run(self, incident: Dict[str, Any], extras: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        extras = extras or {}
        engineer = self._select_engineer(incident.get("category", ""))
        diagnostic = self._diagnostic(incident, extras)
        attempted = self._attempted_actions(incident)
        # Re-fetch to ensure attempted actions list is fresh
        fresh = IncidentRepository.find_by_id(incident["id"])
        if fresh:
            attempted = [f"{s['agent']}: {s['action']}" for s in fresh.get("steps", [])]

        esc_id = EscalationRepository.create({
            "incident_id": incident["id"],
            "reason": extras.get("reason") or "Auto-escalated: complexity exceeds agent capability",
            "diagnostic": diagnostic,
            "attempted_actions": attempted,
            "assigned_engineer": None,  # Will be updated by TicketManagementService if assigned via MS Teams logic
            "priority": incident.get("priority", "P3"),
            "status": "pending",
        })

        # Do bandwidth-based assignment (which triggers MS Teams card)
        from app.services.ticket_management_service import TicketManagementService
        TicketManagementService.assign_ticket_based_on_bandwidth(incident["id"])

        # Fetch assigned engineer to log properly
        updated_incident = IncidentRepository.find_by_id(incident["id"])
        assigned_engineer_id = updated_incident.get("assigned_to")
        engineer = None
        if assigned_engineer_id:
            eng_data = UserRepository.find_by_id(assigned_engineer_id)
            if eng_data:
                engineer = eng_data["full_name"]

        self.record_step(
            incident_id=incident["id"],
            action="Escalated to engineering",
            output=(
                f"Created escalation {esc_id}, routed to {engineer or 'unassigned queue'}. "
                f"Bundled {len(attempted)} prior actions as context."
            ),
            step_type="act",
            metadata={"escalation_id": esc_id, "assigned_engineer": engineer},
        )

        return {"_escalation_id": esc_id}


