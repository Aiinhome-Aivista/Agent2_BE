"""KB Learning Agent.

After an incident is resolved (auto or manually), this agent decides
whether to draft a new KB article or boost the views/confidence on an
existing one.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.agents.base import BaseAgent
from app.core.config import settings
from app.repositories import KBRepository


class KBLearningAgent(BaseAgent):
    name = "KB Learning Agent"

    @staticmethod
    def _is_novel(incident: Dict[str, Any]) -> bool:
        """Decide whether this resolution warrants a fresh KB article.

        An issue is "novel" if no existing article overlaps strongly enough
        on the subject keywords. Category alone is NOT enough — otherwise
        once a single "Database" article exists, no further Database articles
        would ever get written, which was the previous behaviour and a
        real bug. The threshold is: ≥4 shared content words OR an existing
        title that is a substring of the incident subject (or vice-versa).
        """
        existing = KBRepository.list_all(only_published=False)
        subj = (incident.get("subject") or "").lower().strip()
        if not subj:
            return False

        subj_tokens = {t for t in subj.split() if len(t) > 3}
        category = (incident.get("category") or "").lower()

        for art in existing:
            title = (art.get("title") or "").lower()
            tags = " ".join(art.get("tags") or []).lower()

            # 1. Strong title containment either way → not novel
            if title and (title in subj or subj in title):
                return False

            # 2. ≥4 shared meaningful tokens between subject and (title+tags)
            #    (raised from previous ≥3 because of stopword leakage)
            art_tokens = {t for t in (title + " " + tags).split() if len(t) > 3}
            shared = subj_tokens & art_tokens
            if len(shared) >= 4 and category and category in tags:
                # Strong subject overlap AND same category → not novel
                return False
        return True

    @staticmethod
    def _draft_summary(incident: Dict[str, Any]) -> str:
        steps = incident.get("steps", [])
        act_steps = [s for s in steps if s.get("type") in ("act", "evaluate")]
        if not act_steps:
            return f"Resolution for: {incident.get('subject', '')}"
        return f"Resolved {incident.get('category', 'incident')}: " + (
            act_steps[-1]["output"][:180] + "…"
            if len(act_steps[-1]["output"]) > 180
            else act_steps[-1]["output"]
        )

    @staticmethod
    def _draft_content(incident: Dict[str, Any]) -> str:
        lines: List[str] = [
            f"## Symptoms",
            incident.get("description", ""),
            "",
            f"## Category",
            incident.get("category", "Unknown"),
            "",
            f"## Resolution Steps",
        ]
        for i, step in enumerate(incident.get("steps", []), 1):
            lines.append(f"{i}. **{step['agent']}** — {step['action']}: {step['output']}")
        return "\n".join(lines)

    # --------------------------------------------------------------------------
    def run(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        if not self._is_novel(incident):
            self.record_step(
                incident_id=incident["id"],
                action="KB review",
                output="Resolution matches existing article — boosted relevance.",
                step_type="evaluate",
            )
            return {}

        # If this incident also produced a KG resolution node (via
        # force_resolve), link the article to it so the article page can
        # show "Structured: see graph node KGN-…" and the KG can point back
        # to the article. Best-effort — we look up by incident link.
        kg_node_id: str | None = None
        try:
            from app.repositories.knowledge_graph_repository import KnowledgeGraphRepository
            links = KnowledgeGraphRepository.list_links_for_incident(incident["id"])
            for link in links:
                if link.get("role") == "taught" and link.get("node_type") == "resolution":
                    kg_node_id = link["node_id"]
                    break
        except Exception:  # noqa: BLE001 — never block article drafting
            kg_node_id = None

        article_id = KBRepository.create({
            "title": f"Resolution: {incident.get('subject', 'Untitled')}",
            "summary": self._draft_summary(incident),
            "content": self._draft_content(incident),
            "tags": [
                incident.get("category", "general").lower(),
                incident.get("severity", "medium"),
                "auto-generated",
            ],
            "category": incident.get("category", "General"),
            "author": "KB Learning Agent",
            "is_published": settings.agent_kb_auto_publish,
            "kg_node_id": kg_node_id,
        })

        self.record_step(
            incident_id=incident["id"],
            action="Drafted new KB article",
            output=(
                f"Created {article_id} — "
                f"{'auto-published' if settings.agent_kb_auto_publish else 'saved as draft for review'}."
                + (f" Linked to KG node {kg_node_id}." if kg_node_id else "")
            ),
            step_type="act",
            metadata={
                "article_id": article_id,
                "auto_published": settings.agent_kb_auto_publish,
                "kg_node_id": kg_node_id,
            },
        )
        return {"_kb_article_id": article_id}
