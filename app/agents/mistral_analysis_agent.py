"""Mistral Analysis Agent.

Runs *between* Triage and Resolution. Asks Mistral LLM to:
  1. Hypothesise the root cause
  2. Suggest concrete resolution steps
  3. Decide if the steps are safe to run unattended

The agent records the analysis to `mistral_analyses` for audit/cache, and
adds a `reason` step to the incident timeline so the human-facing UI shows
the LLM's contribution.

If MISTRAL_API_KEY isn't configured, this agent records a single "skipped"
reason step and returns an empty patch — the orchestrator continues with
just runbooks + the knowledge graph.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict

from app.agents.base import BaseAgent
from app.core.logger import logger
from app.core.mistral_client import MistralError, get_mistral_client, is_configured
from app.db import get_db


class MistralAnalysisAgent(BaseAgent):
    name = "Mistral Analysis Agent"

    def run(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        if not is_configured():
            self.record_step(
                incident_id=incident["id"],
                action="LLM analysis skipped",
                output="MISTRAL_API_KEY not configured. Set it in .env to enable AI analysis.",
                step_type="reason",
                metadata={"skipped": True},
            )
            return {}

        client = get_mistral_client()
        if client is None:
            return {}

        # ---- Run the analysis -----------------------------------------------
        try:
            result = client.analyze_incident(incident)
        except MistralError as e:
            logger.exception(f"[mistral-agent] failed for {incident['id']}: {e}")
            self.record_step(
                incident_id=incident["id"],
                action="LLM analysis failed",
                output=f"Mistral API error: {e}. Falling back to runbook matching only.",
                step_type="reason",
                metadata={"error": str(e)[:200]},
            )
            self._persist_analysis(incident["id"], None, str(e))
            return {}

        # ---- Persist for audit + cache lookups ------------------------------
        self._persist_analysis(incident["id"], result, None)

        # ---- Add a reasoning step to the incident timeline ------------------
        steps_preview = "\n".join(
            f"{i+1}. {step}" for i, step in enumerate(result.get("suggested_steps", []))
        )
        confidence_pct = int(round(result.get("confidence", 0.0) * 100))
        timeline_output = (
            f"Hypothesised root cause: {result.get('root_cause', '(none)')}\n"
            f"Confidence: {confidence_pct}%\n"
            f"\n"
            f"Plan: {result.get('resolution_summary', '(no summary)')}\n"
            f"\n"
            f"Steps:\n{steps_preview}\n"
            f"\n"
            f"Verification: {result.get('verification', '(no verification step)')}"
        )

        self.record_step(
            incident_id=incident["id"],
            action=f"Mistral analysis ({result.get('model')}, {result.get('latency_ms')}ms)",
            output=timeline_output,
            step_type="reason",
            metadata={
                "model": result.get("model"),
                "confidence": result.get("confidence"),
                "auto_resolvable": result.get("auto_resolvable"),
                "tokens_in": result.get("tokens_in"),
                "tokens_out": result.get("tokens_out"),
            },
        )

        # ---- Return data downstream agents need -----------------------------
        # The orchestrator stashes private keys (starting with `_`) so the
        # Resolution agent can consult them without re-querying the DB.
        return {
            "_mistral_analysis": result,
            "_llm_root_cause": result.get("root_cause"),
            "_llm_confidence": result.get("confidence"),
            "_llm_auto_resolvable": result.get("auto_resolvable"),
            "_llm_suggested_steps": result.get("suggested_steps"),
            "_llm_summary": result.get("resolution_summary"),
        }

    # -------------------------------------------------------------------------
    @staticmethod
    def _persist_analysis(
        incident_id: str,
        result: Dict[str, Any] | None,
        error: str | None,
    ) -> None:
        """Insert a row into mistral_analyses for audit / cache reuse."""
        analysis_id = f"MLA-{uuid.uuid4().hex[:12]}"
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO mistral_analyses
                          (id, incident_id, model, prompt_hash, root_cause,
                           suggested_steps, resolution_summary, confidence,
                           tokens_in, tokens_out, latency_ms, source, error)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'resolution', %s)
                        """,
                        (
                            analysis_id,
                            incident_id,
                            (result or {}).get("model") or "unknown",
                            (result or {}).get("prompt_hash") or "0" * 64,
                            (result or {}).get("root_cause"),
                            json.dumps((result or {}).get("suggested_steps") or []),
                            (result or {}).get("resolution_summary"),
                            float((result or {}).get("confidence") or 0.0),
                            int((result or {}).get("tokens_in") or 0),
                            int((result or {}).get("tokens_out") or 0),
                            int((result or {}).get("latency_ms") or 0),
                            error,
                        ),
                    )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[mistral-agent] could not persist analysis: {e}")

    def extract_graph_relations(self, content: str) -> dict:
        """Use Mistral to extract entities (nodes) and relations (edges) for GraphDB."""
        if not is_configured():
            return {"nodes": [], "edges": []}

        client = get_mistral_client()
        if client is None:
            return {"nodes": [], "edges": []}

        system_prompt = (
            "You are an advanced knowledge graph extraction AI. Your task is to extract a highly interconnected graph from the given incident or runbook document.\n\n"
            "RULES:\n"
            "1. EXTRACT ENTITIES (Nodes): Identify key concepts such as servers, applications, errors, solutions, users, teams, services, and categories.\n"
            "2. DYNAMIC RELATIONSHIPS (Edges): Do not use generic or hardcoded relationship names. Infer the precise, meaningful relationship directly from the text context (e.g., 'causes', 'resolved_by', 'depends_on', 'deployed_to').\n"
            "3. NO ISOLATED NODES: Every extracted node MUST be connected to at least one other node via an edge. Discard any entity that does not have a clear relationship with another entity.\n"
            "4. CONSISTENCY: Ensure that the 'from' and 'to' fields in the edges perfectly match the 'id' of the defined nodes.\n"
            "5. LABELS: Keep node labels concise but descriptive.\n\n"
            "Respond ONLY with valid JSON matching this schema:\n"
            "{\n"
            '  "nodes": [{"id": "node_id", "label": "Node Label", "type": "NodeType"}],\n'
            '  "edges": [{"from": "node_id_1", "to": "node_id_2", "relationship": "dynamic_relationship_name"}]\n'
            "}\n"
        )
        
        user_prompt = f"DOCUMENT CONTENT:\n{content[:15000]}"

        try:
            response = client.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )
            
            content_str = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
            parsed = json.loads(content_str)
            return {
                "nodes": parsed.get("nodes", []),
                "edges": parsed.get("edges", [])
            }
        except Exception as e:
            logger.warning(f"[mistral-agent] extract_graph_relations failed: {e}")
            return {"nodes": [], "edges": []}
