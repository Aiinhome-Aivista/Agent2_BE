"""Orchestrator — runs the agents in sequence and persists state changes.

Sequence:

    Ingestion → Triage → Mistral Analysis (LLM) → Resolution
                                                    ├── success → KB Learning
                                                    └── failure → Escalation → KB Learning

The Mistral step is OPTIONAL — if MISTRAL_API_KEY isn't set, the agent
records a "skipped" step and the orchestrator continues without LLM input.
The Resolution agent itself first tries the Knowledge Graph (built from
human-resolved incidents) and then falls back to runbook matching.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.agents.ingestion_agent import IngestionAgent
from app.agents.triage_agent import TriageAgent
from app.agents.mistral_analysis_agent import MistralAnalysisAgent
from app.agents.resolution_agent import ResolutionAgent
from app.agents.escalation_agent import EscalationAgent
from app.agents.kb_learning_agent import KBLearningAgent
from app.core.logger import logger
from app.repositories import IncidentRepository


_PRIVATE_PATCH_KEYS = {
    "_needs_escalation", "_runbook_used", "_failure_output",
    "_kb_article_id", "_escalation_id", "_matched_runbook",
    "_mistral_analysis", "_llm_root_cause", "_llm_confidence",
    "_llm_auto_resolvable", "_llm_suggested_steps", "_llm_summary",
    "_kg_resolution_id",
}


class AgentOrchestrator:
    """Singleton orchestrator — agents are stateless after init, so reuse them."""

    _instance: "AgentOrchestrator | None" = None

    def __new__(cls) -> "AgentOrchestrator":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self.ingestion = IngestionAgent()
        self.triage = TriageAgent()
        self.mistral = MistralAnalysisAgent()
        self.resolution = ResolutionAgent()
        self.escalation = EscalationAgent()
        self.kb_learning = KBLearningAgent()

    # --------------------------------------------------------------------------
    @staticmethod
    def _persist(incident_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        # Strip orchestrator-internal keys before writing to DB.
        clean = {k: v for k, v in patch.items() if k not in _PRIVATE_PATCH_KEYS}
        if clean:
            IncidentRepository.update(incident_id, clean)
        return clean

    # --------------------------------------------------------------------------
    def process_new(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        """Run the full pipeline on a freshly ingested incident."""
        try:
            patch = self.ingestion.run(incident)
            self._persist(incident["id"], patch)
            incident.update(patch)

            patch = self.triage.run(incident)
            self._persist(incident["id"], patch)
            incident.update(patch)

            # New: LLM analysis. Adds private `_llm_*` keys, no DB columns.
            patch = self.mistral.run(incident)
            incident.update(patch)

            patch = self.resolution.run(incident)
            self._persist(incident["id"], {k: v for k, v in patch.items() if k not in _PRIVATE_PATCH_KEYS})
            incident.update(patch)

            if patch.get("_needs_escalation"):
                # Pass the Mistral summary down to the escalation agent so
                # P1/P2 emails can include an engineer-friendly paragraph.
                escalation_extras = {
                    **patch,
                    "_llm_summary": incident.get("_llm_summary"),
                }
                esc_patch = self.escalation.run(incident, extras=escalation_extras)
                self._persist(incident["id"], esc_patch)
                incident.update(esc_patch)

            # KB learning runs whether resolved or escalated — both are signal.
            kb_patch = self.kb_learning.run(IncidentRepository.find_by_id(incident["id"]) or incident)
            self._persist(incident["id"], kb_patch)

        except Exception as e:  # noqa: BLE001
            logger.exception(f"Orchestrator failed on {incident.get('id')}: {e}")

        # Return the latest persisted state.
        return IncidentRepository.find_by_id(incident["id"]) or incident

    # --------------------------------------------------------------------------
    def re_triage(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        patch = self.triage.run(incident)
        self._persist(incident["id"], patch)
        return IncidentRepository.find_by_id(incident["id"]) or incident

    def force_escalate(self, incident: Dict[str, Any], reason: str) -> Dict[str, Any]:
        patch = self.escalation.run(incident, extras={"reason": reason})
        self._persist(incident["id"], patch)
        return IncidentRepository.find_by_id(incident["id"]) or incident

    def force_resolve(
        self,
        incident: Dict[str, Any],
        notes: Optional[str],
        resolved_by_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from datetime import datetime

        from app.services.knowledge_graph_service import KnowledgeGraphService

        IncidentRepository.update(incident["id"], {
            "status": "resolved",
            "resolved_at": datetime.now(),
        })
        # Add a manual evaluation step
        IncidentRepository.add_step(incident["id"], {
            "agent": "Manual Operator",
            "action": "Marked as resolved",
            "output": notes or "Resolution confirmed by operator.",
            "type": "evaluate",
            "timestamp": datetime.now(),
        })

        # *** NEW *** — feed the resolved incident into the Knowledge Graph.
        # This is the closing of the learning loop: human resolves → graph
        # gains a symptom→cause→resolution path → next similar incident
        # gets auto-resolved by the Resolution agent.
        try:
            refreshed = IncidentRepository.find_by_id(incident["id"]) or incident
            kg_summary = KnowledgeGraphService.learn_from_resolved_incident(
                incident=refreshed,
                resolution_notes=notes,
                resolved_by_user_id=resolved_by_user_id,
            )
            # Record a timeline step so the UI can show what was learned.
            IncidentRepository.add_step(refreshed["id"], {
                "agent": "Knowledge Graph",
                "action": "Captured resolution into graph",
                "output": (
                    f"Symptom node {kg_summary.get('symptom_id')} "
                    f"({'new' if kg_summary.get('symptom_created') else 'reused'}), "
                    f"cause node {kg_summary.get('cause_id')}, "
                    f"resolution node {kg_summary.get('resolution_id')}. "
                    f"Future similar incidents will be auto-resolved using this path."
                ),
                "type": "evaluate",
                "metadata": kg_summary,
                "timestamp": datetime.now(),
            })
        except Exception as e:  # noqa: BLE001
            logger.exception(f"KG learning failed on {incident['id']}: {e}")

        # Trigger KB learning (article drafting)
        self.kb_learning.run(IncidentRepository.find_by_id(incident["id"]) or incident)
        return IncidentRepository.find_by_id(incident["id"]) or incident


orchestrator = AgentOrchestrator()
