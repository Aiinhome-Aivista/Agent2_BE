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
            "assigned_engineer": engineer,
            "priority": incident.get("priority", "P3"),
            "status": "pending",
        })

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

        # ---- Notify on-call engineer via SMTP for HIGH-priority incidents ---
        priority = (incident.get("priority") or "P3").upper()
        if priority in ("P1", "P2"):

            self._send_priority_email(
                incident,
                esc_id,
                engineer,
                extras,
            )

            # create escalation tracker
            try:
                import uuid

                from app.repositories.escalation_tracker_repository import (
                    EscalationTrackerRepository,
                )

                existing = EscalationTrackerRepository.get_active_trackers()

                already_exists = any(
                    t["incident_id"] == incident["id"]
                    for t in existing
                )

                if not already_exists:
                    EscalationTrackerRepository.create({
                        "id": f"TRK-{uuid.uuid4().hex[:12]}",
                        "incident_id": incident["id"],
                        "current_engineer_index": 0,
                    })

            except Exception as e:
                self.record_step(
                    incident_id=incident["id"],
                    action="Escalation tracker creation failed",
                    output=str(e),
                    step_type="reason",
                )

        return {"status": "escalated", "_escalation_id": esc_id}

    # --------------------------------------------------------------------------
    def _send_priority_email(
        self,
        incident: Dict[str, Any],
        escalation_id: str,
        engineer_name: Optional[str],
        extras: Dict[str, Any],
    ) -> None:
        """Send a SMTP notification for P1/P2 incidents to the assigned engineer.

        Best-effort: failure logs a step but doesn't disrupt the workflow.
        The actual SMTP send is wrapped in try/except inside the email service
        and persisted in `email_logs` for audit.
        """
        from app.core.email_service import is_configured, send_high_priority_escalation
        from app.core.mistral_client import get_mistral_client

        # Find an email address for the engineer
        recipient = self._engineer_email(engineer_name)
        if not recipient:
            self.record_step(
                incident_id=incident["id"],
                action="Email notification skipped",
                output="No engineer email found in users table — falling back to in-app queue only.",
                step_type="reason",
                metadata={"escalation_id": escalation_id},
            )
            return

        # Optionally have Mistral write the engineer summary paragraph
        llm_summary = None
        client = get_mistral_client()
        if client is not None:
            try:
                context = "\n".join(self._attempted_actions(incident)[-10:])
                llm_summary = client.summarize_for_engineer(incident, context)
            except Exception:  # noqa: BLE001
                llm_summary = None

        ok = send_high_priority_escalation(
            incident=incident,
            engineer_email=recipient,
            engineer_name=engineer_name,
            llm_summary=llm_summary,
            runbook_attempted=extras.get("_runbook_used"),
            failure_output=extras.get("_failure_output"),
        )

        # Add an audit step to the timeline so operators see what happened.
        self.record_step(
            incident_id=incident["id"],
            action=(
                "P1/P2 email sent" if ok
                else "P1/P2 email skipped" if not is_configured()
                else "P1/P2 email FAILED — see email_logs"
            ),
            output=(
                f"Recipient: {recipient}"
                + (f"\nSummary: {llm_summary}" if llm_summary else "")
            ),
            step_type="act",
            metadata={
                "escalation_id": escalation_id,
                "email_to": recipient,
                "smtp_configured": is_configured(),
                "delivered": ok,
            },
        )

    @staticmethod
    def _engineer_email(engineer_name: Optional[str]) -> Optional[str]:
        """Look up an engineer email by full_name. Falls back to the first
        engineer in the users table, then to settings.smtp_escalation_cc."""
        from app.core.config import settings as _settings
        if engineer_name:
            from app.db import get_db
            with get_db() as conn:
                with conn.cursor(dictionary=True) as cur:
                    cur.execute(
                        "SELECT email FROM users WHERE full_name = %s LIMIT 1",
                        (engineer_name,),
                    )
                    row = cur.fetchone()
                    if row and row.get("email"):
                        return row["email"]
        engineers = UserRepository.list_engineers()
        if engineers:
            return engineers[0].get("email")
        if _settings.smtp_escalation_cc:
            return _settings.smtp_escalation_cc
        return None
