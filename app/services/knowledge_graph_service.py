"""Knowledge Graph service — orchestrates the lifecycle of the KG.

Two flows:

  1. **Learn**  (called when a human resolves an incident)
     ─────────────────────────────────────────────────────
     - Extract a "symptom" node from the incident's subject + description
       (keyword-based; we don't need an LLM here, but we use Mistral when
       available to label the cause more precisely)
     - Extract a "cause" node from the human's resolution notes
     - Extract a "resolution" node from the timeline's `act`/`evaluate` steps
     - Connect: symptom --caused_by--> cause --resolved_by--> resolution
     - Link the incident to all three nodes via kg_incident_links
     - If a similar symptom node already exists, reuse it and just
       strengthen the edges.

  2. **Apply**  (called by the Resolution agent before runbook matching)
     ─────────────────────────────────────────────────────
     - Build keywords from the incoming incident
     - Find symptom nodes with sufficient keyword overlap
     - For each candidate, traverse symptom → cause → resolution
     - Return the highest-confidence resolution suggestion(s) to the agent
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import logger
from app.repositories.knowledge_graph_repository import KnowledgeGraphRepository

# Words too generic to be useful keywords. Add to this set as patterns emerge.
_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of",
    "in", "on", "at", "for", "and", "or", "but", "not", "no", "we", "it",
    "this", "that", "these", "those", "with", "as", "by", "from", "into",
    "i", "you", "he", "she", "they", "my", "our", "your", "their", "have",
    "has", "had", "do", "does", "did", "will", "would", "can", "could",
    "should", "may", "might", "shall", "must", "if", "then", "else",
    "issue", "problem", "error", "ticket", "incident", "please", "help",
    "thanks", "thank", "hi", "hello", "team", "asap", "urgent",
    "ms", "mr", "mrs", "dear", "regards", "sincerely",
})

_TOKEN_RX = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


# ============================================================================
# Public service surface
# ============================================================================
class KnowledgeGraphService:

    # ----------------------------------------------------------------- LEARN
    @classmethod
    def learn_from_resolved_incident(
        cls,
        incident: Dict[str, Any],
        resolution_notes: Optional[str] = None,
        resolved_by_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build (or strengthen) KG nodes/edges from a resolved incident.

        Returns a summary dict with the IDs of nodes created/updated.
        Idempotent across all three node types: symptoms, causes, and
        resolutions are all reused when an equivalent node already exists
        (matched by keyword overlap, then by normalised label). Without
        this dedup the graph would grow a fresh triplet per resolution and
        the matching algorithm would dilute over time.
        """
        kw = _extract_keywords(
            f"{incident.get('subject', '')} {incident.get('description', '')}"
        )
        if len(kw) < 2:
            logger.info(f"[kg] incident {incident['id']} has too few keywords — skipping")
            return {"created": [], "matched": [], "reason": "insufficient_keywords"}

        category = incident.get("category") or "Uncategorised"

        # ---- 1. Symptom node (reuse if similar) ----------------------------
        existing_symptoms = KnowledgeGraphRepository.find_symptom_by_keywords(
            keywords=kw, category=category, min_overlap=2,
        )
        if existing_symptoms:
            symptom_id = existing_symptoms[0]["id"]
            KnowledgeGraphRepository.increment_occurrence(symptom_id)
            symptom_created = False
            logger.info(f"[kg] reused symptom node {symptom_id}")
        else:
            symptom_id = KnowledgeGraphRepository.create_node(
                node_type="symptom",
                label=(incident.get("subject") or "Untitled symptom")[:200],
                description=incident.get("description"),
                category=category,
                keywords=kw,
                source_incident_id=incident["id"],
                created_by=resolved_by_user_id,
                confidence=0.5,
            )
            symptom_created = True

            # When creating a new symptom, link it to its closest peers via
            # `similar_to` edges. This is what builds the cluster structure
            # in the graph over time. We use a slightly lower threshold than
            # symptom-reuse (min_overlap=2 vs 3) since the relationship is
            # weaker than identity.
            peers = KnowledgeGraphRepository.find_symptom_by_keywords(
                keywords=kw, category=None, min_overlap=2,
            )
            for peer in peers[:5]:  # cap fanout
                if peer["id"] == symptom_id:
                    continue
                KnowledgeGraphRepository.upsert_edge(
                    src_node_id=symptom_id,
                    dst_node_id=peer["id"],
                    edge_type="similar_to",
                )
                KnowledgeGraphRepository.upsert_edge(
                    src_node_id=peer["id"],
                    dst_node_id=symptom_id,
                    edge_type="similar_to",
                )

        KnowledgeGraphRepository.link_incident(
            incident_id=incident["id"], node_id=symptom_id, role="symptom_match",
        )

        # ---- 2. Cause node (reuse if existing) -----------------------------
        cause_label, cause_keywords = _derive_cause(incident, resolution_notes)
        cause = (
            KnowledgeGraphRepository.find_node_by_label(
                node_type="cause", label=cause_label, category=category,
            )
            or KnowledgeGraphRepository.find_node_by_keywords(
                node_type="cause",
                keywords=cause_keywords,
                category=category,
                min_overlap=2,
            )
        )
        if cause:
            cause_id = cause["id"]
            KnowledgeGraphRepository.increment_occurrence(cause_id)
            cause_created = False
        else:
            cause_id = KnowledgeGraphRepository.create_node(
                node_type="cause",
                label=cause_label[:200],
                description=resolution_notes,
                category=category,
                keywords=cause_keywords,
                source_incident_id=incident["id"],
                created_by=resolved_by_user_id,
                confidence=0.6,
            )
            cause_created = True

        # ---- 3. Resolution node (reuse if existing) ------------------------
        steps = _extract_resolution_steps(incident, resolution_notes)
        resolution_label = _summarise_resolution(steps, incident)
        # Resolutions are matched by step similarity, not just label —
        # the keywords list combines the cause keywords with step titles,
        # which gives a richer signal than label alone.
        resolution_keywords = list({
            *cause_keywords,
            *[w for s in steps for w in _extract_keywords(s.get("title", ""))],
        })
        resolution = (
            KnowledgeGraphRepository.find_node_by_label(
                node_type="resolution", label=resolution_label, category=category,
            )
            or KnowledgeGraphRepository.find_node_by_keywords(
                node_type="resolution",
                keywords=resolution_keywords,
                category=category,
                min_overlap=3,
            )
        )
        if resolution:
            resolution_id = resolution["id"]
            KnowledgeGraphRepository.increment_occurrence(resolution_id)
            resolution_created = False
        else:
            resolution_id = KnowledgeGraphRepository.create_node(
                node_type="resolution",
                label=resolution_label,
                description=resolution_notes,
                category=category,
                keywords=resolution_keywords,
                steps=steps,
                source_incident_id=incident["id"],
                created_by=resolved_by_user_id,
                confidence=0.7,
            )
            resolution_created = True

        # ---- 4. Edges (idempotent — upsert) --------------------------------
        KnowledgeGraphRepository.upsert_edge(
            src_node_id=symptom_id, dst_node_id=cause_id, edge_type="caused_by",
        )
        KnowledgeGraphRepository.upsert_edge(
            src_node_id=cause_id, dst_node_id=resolution_id, edge_type="resolved_by",
        )

        # ---- 5. Incident links (idempotent — ON DUPLICATE KEY) -------------
        for nid, role in [
            (cause_id, "cause_match"),
            (resolution_id, "taught"),
        ]:
            KnowledgeGraphRepository.link_incident(
                incident_id=incident["id"], node_id=nid, role=role,
            )

        return {
            "symptom_id": symptom_id,
            "symptom_created": symptom_created,
            "cause_id": cause_id,
            "cause_created": cause_created,
            "resolution_id": resolution_id,
            "resolution_created": resolution_created,
            "keywords": kw,
        }

    # ----------------------------------------------------------------- APPLY
    @classmethod
    def find_resolution_for_incident(
        cls,
        incident: Dict[str, Any],
        min_overlap: int = 3,
        min_confidence: float = 0.55,
    ) -> Optional[Dict[str, Any]]:
        """Search the KG for the BEST matching resolution.

        Walks every symptom→cause→resolution path that clears the keyword
        overlap and confidence floors, scores each candidate, and returns
        the top one. Composite score is:

            score = match_ratio              (0..1, keyword similarity)
                  × resolution.confidence    (0..1, Laplace-smoothed)
                  × (1 + log1p(successes))   (boost track record)

        This is what makes a resolution that has worked 50 times outrank
        an untested one with the same keyword fit.

        Returns: { symptom, cause, resolution, match_overlap, match_ratio,
                   score } or None.
        """
        import math

        kw = _extract_keywords(
            f"{incident.get('subject', '')} {incident.get('description', '')}"
        )
        if len(kw) < min_overlap:
            return None

        category = incident.get("category") or None
        candidates = KnowledgeGraphRepository.find_symptom_by_keywords(
            keywords=kw, category=category, min_overlap=min_overlap,
        )
        if not candidates and category:
            # Retry without category — sometimes triage miscategorises
            candidates = KnowledgeGraphRepository.find_symptom_by_keywords(
                keywords=kw, category=None, min_overlap=min_overlap,
            )
        if not candidates:
            return None

        best: Optional[Dict[str, Any]] = None
        best_score = 0.0

        for symptom in candidates:
            cause_edges = KnowledgeGraphRepository.edges_from(symptom["id"], edge_type="caused_by")
            for ce in cause_edges:
                cause = KnowledgeGraphRepository.find_node(ce["dst_node_id"])
                if not cause:
                    continue
                resolution_edges = KnowledgeGraphRepository.edges_from(
                    cause["id"], edge_type="resolved_by",
                )
                for re_edge in resolution_edges:
                    resolution = KnowledgeGraphRepository.find_node(re_edge["dst_node_id"])
                    if not resolution:
                        continue
                    conf = float(resolution.get("confidence") or 0)
                    if conf < min_confidence:
                        continue
                    successes = int(resolution.get("success_count") or 0)
                    ratio = float(symptom.get("_match_ratio") or 0)
                    score = ratio * conf * (1.0 + math.log1p(successes))
                    if score > best_score:
                        best_score = score
                        best = {
                            "symptom": symptom,
                            "cause": cause,
                            "resolution": resolution,
                            "match_overlap": symptom.get("_match_overlap", 0),
                            "match_ratio": ratio,
                            "score": score,
                        }
        return best

    # --------------------------------------------------------- OUTCOME UPDATE
    @classmethod
    def record_outcome(
        cls,
        incident_id: str,
        resolution_node_id: str,
        was_successful: bool,
    ) -> None:
        """Called by the Resolution agent after applying a KG-suggested fix.

        Updates the resolution node's success/failure counters and the
        incident-link row so we can see which applications worked.
        """
        KnowledgeGraphRepository.record_outcome(resolution_node_id, was_successful)
        KnowledgeGraphRepository.link_incident(
            incident_id=incident_id,
            node_id=resolution_node_id,
            role="resolution_applied",
            was_successful=was_successful,
        )

    # --------------------------------------------------------- DIRECT TEACH
    @classmethod
    def teach_triplet(
        cls,
        *,
        symptom_label: str,
        symptom_description: Optional[str] = None,
        cause_label: str,
        resolution_label: str,
        resolution_steps: Optional[List[Dict[str, Any]]] = None,
        category: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        taught_by_user_id: Optional[str] = None,
        initial_confidence: float = 0.7,
    ) -> Dict[str, Any]:
        """Teach the graph a symptom→cause→resolution triplet directly,
        without going through the resolve-an-incident path.

        This is the admin "I know this fix, save it for next time" entry
        point. Reuses the same dedup logic as learn_from_resolved_incident
        so re-teaching an identical triplet strengthens the existing nodes
        rather than duplicating them.
        """
        kw = list(keywords) if keywords else _extract_keywords(
            f"{symptom_label} {symptom_description or ''}"
        )
        if len(kw) < 2:
            # Pad with the cause keywords if the symptom is too terse
            kw = list({*kw, *_extract_keywords(cause_label)})
        if len(kw) < 2:
            raise ValueError("Symptom is too vague — please add a description or more specific wording.")

        category = category or "Uncategorised"

        # ---- Symptom -------------------------------------------------------
        existing = KnowledgeGraphRepository.find_symptom_by_keywords(
            keywords=kw, category=category, min_overlap=2,
        )
        if existing:
            symptom_id = existing[0]["id"]
            symptom_created = False
            KnowledgeGraphRepository.increment_occurrence(symptom_id)
        else:
            symptom_id = KnowledgeGraphRepository.create_node(
                node_type="symptom",
                label=symptom_label[:200],
                description=symptom_description,
                category=category,
                keywords=kw,
                created_by=taught_by_user_id,
                confidence=initial_confidence,
            )
            symptom_created = True

        # ---- Cause ---------------------------------------------------------
        cause_keywords = _extract_keywords(cause_label) or kw[:5]
        cause = (
            KnowledgeGraphRepository.find_node_by_label(
                node_type="cause", label=cause_label, category=category,
            )
            or KnowledgeGraphRepository.find_node_by_keywords(
                node_type="cause", keywords=cause_keywords,
                category=category, min_overlap=2,
            )
        )
        if cause:
            cause_id = cause["id"]
            cause_created = False
            KnowledgeGraphRepository.increment_occurrence(cause_id)
        else:
            cause_id = KnowledgeGraphRepository.create_node(
                node_type="cause",
                label=cause_label[:200],
                category=category,
                keywords=cause_keywords,
                created_by=taught_by_user_id,
                confidence=initial_confidence,
            )
            cause_created = True

        # ---- Resolution ----------------------------------------------------
        steps = resolution_steps or []
        resolution_kw = list({
            *cause_keywords,
            *[w for s in steps for w in _extract_keywords(s.get("title", ""))],
        }) or cause_keywords
        resolution = (
            KnowledgeGraphRepository.find_node_by_label(
                node_type="resolution", label=resolution_label, category=category,
            )
            or KnowledgeGraphRepository.find_node_by_keywords(
                node_type="resolution", keywords=resolution_kw,
                category=category, min_overlap=3,
            )
        )
        if resolution:
            resolution_id = resolution["id"]
            resolution_created = False
            KnowledgeGraphRepository.increment_occurrence(resolution_id)
        else:
            resolution_id = KnowledgeGraphRepository.create_node(
                node_type="resolution",
                label=resolution_label[:200],
                category=category,
                keywords=resolution_kw,
                steps=steps,
                created_by=taught_by_user_id,
                confidence=initial_confidence,
            )
            resolution_created = True

        KnowledgeGraphRepository.upsert_edge(
            src_node_id=symptom_id, dst_node_id=cause_id, edge_type="caused_by",
        )
        KnowledgeGraphRepository.upsert_edge(
            src_node_id=cause_id, dst_node_id=resolution_id, edge_type="resolved_by",
        )

        return {
            "symptom_id": symptom_id,
            "symptom_created": symptom_created,
            "cause_id": cause_id,
            "cause_created": cause_created,
            "resolution_id": resolution_id,
            "resolution_created": resolution_created,
        }


# ============================================================================
# Internals
# ============================================================================
def _extract_keywords(text: str, max_n: int = 20) -> List[str]:
    """Cheap keyword extraction: lowercase tokens, drop stopwords, dedupe,
    sort by length (longer = more specific). Good enough for the current
    "match similar incidents" use case."""
    seen: Dict[str, None] = {}
    for tok in _TOKEN_RX.findall(text or ""):
        low = tok.lower()
        if low in _STOPWORDS or len(low) < 3:
            continue
        if low not in seen:
            seen[low] = None
        if len(seen) >= max_n * 3:  # over-collect so we can sort meaningfully
            break
    # Prefer longer tokens (proper nouns, identifiers) when ranking.
    tokens = sorted(seen.keys(), key=lambda s: (-len(s), s))
    return tokens[:max_n]


def _derive_cause(
    incident: Dict[str, Any],
    resolution_notes: Optional[str],
) -> Tuple[str, List[str]]:
    """Heuristic cause derivation.

    If a Mistral analysis ran for this incident, use its `root_cause` field.
    Otherwise pull the first sentence of the operator's notes, or fall back
    to a generic '{category} issue' label.
    """
    # 1. Best signal: prior Mistral analysis stored on the incident
    extras = incident.get("_mistral_analysis")
    if extras and extras.get("root_cause"):
        label = extras["root_cause"]
    elif resolution_notes:
        # First sentence of the operator's notes
        first = re.split(r"[.\n!?]", resolution_notes, maxsplit=1)[0].strip()
        label = first[:200] if first else f"{incident.get('category', 'Unknown')} issue"
    else:
        label = f"{incident.get('category', 'Unknown')} root cause"

    return label, _extract_keywords(label)


def _extract_resolution_steps(
    incident: Dict[str, Any],
    resolution_notes: Optional[str],
) -> List[Dict[str, Any]]:
    """Build an ordered list of resolution steps from:
      - timeline `act`/`evaluate` steps (auto-generated)
      - operator notes (split on newlines / bullets)
    Each step is {order, title, source, command?, expected?}.
    """
    out: List[Dict[str, Any]] = []
    order = 1

    # From the agent timeline
    for step in incident.get("steps") or []:
        if step.get("type") in ("act", "evaluate", "plan"):
            title = (step.get("action") or "").strip()
            if not title:
                continue
            out.append({
                "order": order,
                "title": title[:200],
                "detail": (step.get("output") or "")[:500],
                "source": "agent",
                "agent": step.get("agent"),
            })
            order += 1

    # From operator notes — split on newlines, then bullet markers
    if resolution_notes:
        for line in resolution_notes.splitlines():
            cleaned = re.sub(r"^[\s\-*•\d.)]+", "", line).strip()
            if len(cleaned) < 4:
                continue
            out.append({
                "order": order,
                "title": cleaned[:200],
                "source": "operator",
            })
            order += 1
    return out


def _summarise_resolution(steps: List[Dict[str, Any]], incident: Dict[str, Any]) -> str:
    """Short label for a resolution node — used in the KG browser & emails."""
    if steps:
        first = steps[0].get("title", "")
        if first:
            return first[:200]
    return f"Resolution for {incident.get('category', 'incident')}"
