"""Abstract base agent.

Every concrete agent (Ingestion / Triage / Resolution / Escalation /
KB-Learning) inherits from `BaseAgent` and implements `run()`. Each run
appends an `AgentStep` to the incident timeline, giving the UI a complete
audit trail of every decision the system makes.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Literal

from app.core.logger import logger
from app.repositories import IncidentRepository, AuditRepository

StepType = Literal["observe", "reason", "plan", "act", "evaluate"]


class BaseAgent(ABC):
    name: str = "BaseAgent"

    def __init__(self) -> None:
        self.logger = logger.bind(agent=self.name)

    @abstractmethod
    def run(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        """Execute this agent's logic on the given incident.

        Returns a patch dict — fields the orchestrator should merge back into
        the incident record. Returning an empty dict means no state change.
        """
        ...

    # -- helpers ----------------------------------------------------------------
    def record_step(
        self,
        incident_id: str,
        action: str,
        output: str,
        step_type: StepType,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        step = {
            "id": f"STP-{uuid.uuid4().hex[:10]}",
            "agent": self.name,
            "action": action,
            "output": output,
            "type": step_type,
            "metadata": metadata or {},
            "timestamp": datetime.now(),
        }
        IncidentRepository.add_step(incident_id, step)
        AuditRepository.log(
            actor=self.name,
            action=action,
            target=incident_id,
            target_type="incident",
            metadata={"step_type": step_type, **(metadata or {})},
        )
        self.logger.info(f"[{incident_id}] {action} → {output[:80]}")
