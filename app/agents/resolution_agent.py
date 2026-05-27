"""Resolution Agent.

Strategy (in order):
  1. Consult the Knowledge Graph — has a near-identical incident been
     resolved before? If so, apply that recorded fix.
  2. Otherwise match a runbook (TF-IDF similarity) and execute it.
  3. If neither path produces a high-enough confidence result, escalate.

The executor is a simulator — in production it would call out to actual
remediation systems (Ansible, Lambda, k8s jobs, etc.).
"""
from __future__ import annotations

import random
from datetime import datetime
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.agents.base import BaseAgent
from app.core.config import settings
from app.core.logger import logger
from app.repositories import RunbookRepository
from app.services.knowledge_graph_service import KnowledgeGraphService


class ResolutionAgent(BaseAgent):
    name = "Resolution Agent"

    # --------------------------------------------------------------------------
    def _match_runbook(self, incident: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], float]]:
        runbooks = [r for r in RunbookRepository.list_all() if r.get("is_active")]
        if not runbooks:
            return None

        # Prefer category match — the triage agent's classification is
        # usually more reliable than free-text similarity alone.
        cat = (incident.get("category") or "").lower()
        candidates = [r for r in runbooks if r["category"].lower() == cat] or runbooks

        # TF-IDF similarity on combined text.
        corpus = [
            f"{r['name']} {r.get('description', '')} {' '.join(r.get('triggers', []))}"
            for r in candidates
        ]
        query = f"{incident.get('subject', '')} {incident.get('description', '')}"
        vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
        try:
            matrix = vectorizer.fit_transform(corpus + [query])
        except ValueError:
            return None

        sims = cosine_similarity(matrix[-1], matrix[:-1])[0]
        best = int(sims.argmax())
        score = float(sims[best])
        return candidates[best], score

    # --------------------------------------------------------------------------
    def _execute_kg_resolution(
        self,
        incident: Dict[str, Any],
        match: Dict[str, Any],
    ) -> Tuple[bool, str, float]:
        """Simulate executing a Knowledge Graph-derived resolution.

        Success probability follows the node's actual Laplace-smoothed
        confidence — a brand-new node with a 0.5 prior succeeds ~50% of
        the time, a well-proven node with 50 successes succeeds ~95%.
        We cap at 0.97 (something can always go wrong) and floor at 0.10
        (transient flakes can succeed). The detailed step list is written
        into the timeline for audit.
        """
        start = perf_counter()
        resolution = match["resolution"]
        steps = resolution.get("steps") or []

        confidence = float(resolution.get("confidence") or 0.5)
        prob = max(0.10, min(0.97, confidence))
        success = random.random() < prob

        if success:
            lines = [
                f"✓ {s.get('title', f'Step {i+1}')}" for i, s in enumerate(steps)
            ]
            output = (
                "\n".join(lines)
                + f"\n→ KG-suggested resolution applied successfully (node confidence {confidence:.0%})."
            )
        else:
            failed_at = random.randint(1, max(1, len(steps)))
            output = (
                f"✗ Step {failed_at} of KG-suggested resolution failed verification. "
                f"Node confidence was {confidence:.0%} — recording failure and escalating."
            )

        # Always record the outcome so the KG learns over time.
        try:
            KnowledgeGraphService.record_outcome(
                incident_id=incident["id"],
                resolution_node_id=resolution["id"],
                was_successful=success,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[resolution] could not record KG outcome: {e}")

        duration = perf_counter() - start + random.uniform(1.5, 6.0)
        return success, output, duration

    # --------------------------------------------------------------------------
    def _execute(self, runbook: Dict[str, Any], incident: Dict[str, Any]) -> Tuple[bool, str, float]:
        """Simulate runbook execution.

        Returns (success, output, duration_seconds). The agent's reported
        success rate biases the random outcome — well-proven runbooks
        succeed more often than untested ones.
        """
        start = perf_counter()
        # Probability of success follows the runbook's track record,
        # floored at 60% so we always have a chance.
        success_prob = max(0.6, runbook.get("success_rate") or 0.8)
        success = random.random() < success_prob

        if success:
            output_lines = [
                f"✓ {step['title']}" for step in runbook.get("steps", [])
            ]
            output = "\n".join(output_lines) + "\n→ All steps completed successfully."
        else:
            failed_at = random.randint(1, max(1, len(runbook.get("steps", []))))
            output = f"✗ Step {failed_at} failed verification check. Aborting and escalating."

        duration = perf_counter() - start + random.uniform(2.5, 8.5)
        return success, output, duration

    # --------------------------------------------------------------------------
    def run(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        # ===== Step 1: Try the Knowledge Graph first =========================
        try:
            kg_match = KnowledgeGraphService.find_resolution_for_incident(incident)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[resolution] KG lookup failed: {e}")
            kg_match = None

        if kg_match:
            resolution = kg_match["resolution"]
            cause = kg_match["cause"]
            symptom = kg_match["symptom"]
            self.record_step(
                incident_id=incident["id"],
                action=f"Knowledge Graph match (overlap {kg_match['match_overlap']} keywords)",
                output=(
                    f"Matched prior symptom: \"{symptom.get('label', '')}\"\n"
                    f"Hypothesised cause: {cause.get('label', '')}\n"
                    f"Known resolution: {resolution.get('label', '')} "
                    f"(confidence {resolution.get('confidence', 0):.0%}, "
                    f"applied successfully {resolution.get('success_count', 0)} time(s) before)"
                ),
                step_type="reason",
                metadata={
                    "symptom_id": symptom.get("id"),
                    "cause_id": cause.get("id"),
                    "resolution_id": resolution.get("id"),
                    "match_overlap": kg_match["match_overlap"],
                    "kg_confidence": resolution.get("confidence"),
                },
            )

            success, output, duration = self._execute_kg_resolution(incident, kg_match)
            self.record_step(
                incident_id=incident["id"],
                action=f"Applied KG-learned resolution in {duration:.1f}s",
                output=output,
                step_type="act",
                metadata={
                    "resolution_node_id": resolution["id"],
                    "success": success,
                    "duration_s": duration,
                    "source": "knowledge_graph",
                },
            )

            if success:
                self.record_step(
                    incident_id=incident["id"],
                    action="Verification check",
                    output="System checks pass. Knowledge graph resolution validated.",
                    step_type="evaluate",
                    metadata={"verified": True, "via": "knowledge_graph"},
                )
                return {
                    "status": "resolved",
                    "auto_resolved": True,
                    "resolved_at": datetime.now(),
                    "_runbook_used": f"KG: {resolution.get('label', '')[:60]}",
                    "_kg_resolution_id": resolution["id"],
                }
            # KG path failed → fall through to runbook
            self.record_step(
                incident_id=incident["id"],
                action="KG resolution failed — falling back to runbook search",
                output="Recorded failure for KG node; trying runbook match next.",
                step_type="reason",
            )

        # ===== Step 2: Runbook match =========================================
        match = self._match_runbook(incident)

        if not match:
            self.record_step(
                incident_id=incident["id"],
                action="Runbook search",
                output="No matching runbook found. Escalation required.",
                step_type="reason",
            )
            return {"status": "escalated", "_needs_escalation": True}

        runbook, score = match

        # If confidence is below the configured threshold, don't auto-act.
        if score < settings.agent_auto_remediation_threshold * 0.7:  # softened threshold
            self.record_step(
                incident_id=incident["id"],
                action=f"Runbook '{runbook['name']}' matched at {score:.0%}",
                output=(
                    f"Match score below auto-remediation threshold "
                    f"({settings.agent_auto_remediation_threshold * 0.7:.0%}). "
                    f"Escalating for human review."
                ),
                step_type="plan",
                metadata={"runbook_id": runbook["id"], "match_score": score},
            )
            return {"status": "escalated", "_needs_escalation": True, "_matched_runbook": runbook["name"]}

        self.record_step(
            incident_id=incident["id"],
            action=f"Selected runbook: {runbook['name']}",
            output=(
                f"Matched at {score:.0%} confidence. "
                f"Plan: execute {len(runbook.get('steps', []))} step(s)."
            ),
            step_type="plan",
            metadata={"runbook_id": runbook["id"], "match_score": score},
        )

        # Execute
        success, output, duration = self._execute(runbook, incident)
        RunbookRepository.record_execution(runbook["id"], success, duration)

        self.record_step(
            incident_id=incident["id"],
            action=f"Executed runbook in {duration:.1f}s",
            output=output,
            step_type="act",
            metadata={"runbook_id": runbook["id"], "success": success, "duration_s": duration},
        )

        if success:
            self.record_step(
                incident_id=incident["id"],
                action="Verification check",
                output="System metrics confirm resolution. Closing incident.",
                step_type="evaluate",
                metadata={"verified": True},
            )
            return {
                "status": "resolved",
                "auto_resolved": True,
                "resolved_at": datetime.now(),
                "_runbook_used": runbook["name"],
            }

        return {
            "status": "escalated",
            "_needs_escalation": True,
            "_runbook_used": runbook["name"],
            "_failure_output": output,
        }
