"""Knowledge Graph repository.

Pure data-access layer for the `knowledge_graph_nodes`, `knowledge_graph_edges`,
and `kg_incident_links` tables. No business logic — that lives in
`services/knowledge_graph_service.py`.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


from app.db import get_db


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _decode_json_fields(row: Optional[Dict[str, Any]], fields: List[str]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    for f in fields:
        if isinstance(row.get(f), str):
            try:
                row[f] = json.loads(row[f])
            except json.JSONDecodeError:
                row[f] = []
    return row


class KnowledgeGraphRepository:
    # ============================================================== NODES ===

    @staticmethod
    def create_node(
        *,
        node_type: str,                       # symptom | cause | resolution
        label: str,
        keywords: List[str],
        description: Optional[str] = None,
        category: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        source_incident_id: Optional[str] = None,
        created_by: Optional[str] = None,
        confidence: float = 0.5,
    ) -> str:
        node_id = _new_id("KGN")
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO knowledge_graph_nodes
                      (id, node_type, label, description, category, steps,
                       keywords, confidence, source_incident_id, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        node_id, node_type, label[:255], description, category,
                        json.dumps(steps) if steps else None,
                        json.dumps(keywords),
                        max(0.0, min(1.0, float(confidence))),
                        source_incident_id, created_by,
                    ),
                )
            conn.commit()
        return node_id

    @staticmethod
    def find_node(node_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM knowledge_graph_nodes WHERE id = %s LIMIT 1", (node_id,))
                return _decode_json_fields(cur.fetchone(), ["steps", "keywords"])

    @staticmethod
    def find_node_by_label(
        *,
        node_type: str,
        label: str,
        category: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Look up an existing node by its normalised label.

        Uses the `label_hash` STORED column (SHA-256 of LOWER(label)) added in
        migration 004 — falls back to a LOWER(label) equality if the column
        isn't present (lets the code keep working before the migration is
        applied). Returns the highest-confidence match if multiple exist.
        """
        sql_with_hash = (
            "SELECT * FROM knowledge_graph_nodes "
            "WHERE node_type = %s AND label_hash = SHA2(LOWER(%s), 256) "
        )
        sql_fallback = (
            "SELECT * FROM knowledge_graph_nodes "
            "WHERE node_type = %s AND LOWER(label) = LOWER(%s) "
        )
        params: List[Any] = [node_type, label[:255]]
        suffix = "AND is_active = TRUE"
        if category:
            suffix += " AND category = %s"
            params.append(category)
        suffix += " ORDER BY confidence DESC, occurrence_count DESC LIMIT 1"

        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                try:
                    cur.execute(sql_with_hash + suffix, tuple(params))
                    row = cur.fetchone()
                except Exception:  # noqa: BLE001 — column missing pre-migration
                    cur.execute(sql_fallback + suffix, tuple(params))
                    row = cur.fetchone()
        return _decode_json_fields(row, ["steps", "keywords"])

    @staticmethod
    def find_node_by_keywords(
        *,
        node_type: str,
        keywords: List[str],
        category: Optional[str] = None,
        min_overlap: int = 2,
    ) -> Optional[Dict[str, Any]]:
        """Generic version of find_symptom_by_keywords for any node type.

        Used by the service layer to dedupe cause/resolution nodes that
        describe the same concept in slightly different words. Returns the
        single best-overlap match (highest overlap, then highest ratio).
        """
        candidates = KnowledgeGraphRepository.list_nodes(
            node_type=node_type, category=category, is_active=True, limit=500,
        )
        kw_set = set(k.lower() for k in keywords if k)
        if not kw_set:
            return None
        best: Optional[Dict[str, Any]] = None
        best_score: tuple[int, float] = (0, 0.0)
        for c in candidates:
            existing = set(k.lower() for k in (c.get("keywords") or []))
            overlap = len(kw_set & existing)
            if overlap < min_overlap:
                continue
            ratio = overlap / max(1, len(kw_set | existing))
            score = (overlap, ratio)
            if score > best_score:
                best = {**c, "_match_overlap": overlap, "_match_ratio": ratio}
                best_score = score
        return best

    @staticmethod
    def list_nodes(
        *,
        node_type: Optional[str] = None,
        category: Optional[str] = None,
        is_active: Optional[bool] = True,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM knowledge_graph_nodes WHERE 1=1"
        params: List[Any] = []
        if node_type:
            sql += " AND node_type = %s"
            params.append(node_type)
        if category:
            sql += " AND category = %s"
            params.append(category)
        if is_active is not None:
            sql += " AND is_active = %s"
            params.append(is_active)
        sql += " ORDER BY confidence DESC, occurrence_count DESC LIMIT %s"
        params.append(limit)
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
        return [
            r for r in (_decode_json_fields(row, ["steps", "keywords"]) for row in rows) if r is not None
        ]

    @staticmethod
    def find_symptom_by_keywords(
        keywords: List[str],
        category: Optional[str] = None,
        min_overlap: int = 2,
    ) -> List[Dict[str, Any]]:
        """Return existing 'symptom' nodes whose `keywords` overlap with the
        given list. Used to detect repeat incidents.

        We pull all active symptom nodes for the category (or all if no
        category given) and compute overlap in Python — simpler than fighting
        MySQL's JSON functions.
        """
        candidates = KnowledgeGraphRepository.list_nodes(
            node_type="symptom", category=category, is_active=True, limit=500,
        )
        kw_set = set(k.lower() for k in keywords if k)
        scored: List[Dict[str, Any]] = []
        for c in candidates:
            existing = set(k.lower() for k in (c.get("keywords") or []))
            overlap = kw_set & existing
            if len(overlap) >= min_overlap:
                c["_match_overlap"] = len(overlap)
                c["_match_ratio"] = len(overlap) / max(1, len(kw_set | existing))
                scored.append(c)
        scored.sort(key=lambda c: (c["_match_overlap"], c["_match_ratio"]), reverse=True)
        return scored

    @staticmethod
    def increment_occurrence(node_id: str) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE knowledge_graph_nodes "
                    "SET occurrence_count = occurrence_count + 1, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = %s",
                    (node_id,),
                )
            conn.commit()

    @staticmethod
    def record_outcome(node_id: str, success: bool) -> None:
        """Update success/failure counters and recompute confidence.

        Uses Laplace-smoothed success rate:
            confidence = (success_count + 1) / (success_count + failure_count + 2)

        The +1 numerator / +2 denominator are the smoothing priors — a node
        with zero history starts at 0.5, drifts toward the true success rate
        as evidence accumulates. We compute against the POST-increment counts
        in a single statement using a CASE expression so we don't have to
        worry about MySQL's left-to-right SET evaluation surprises.
        """
        with get_db() as conn:
            with conn.cursor() as cur:
                if success:
                    cur.execute(
                        """
                        UPDATE knowledge_graph_nodes
                        SET success_count = success_count + 1,
                            confidence    = LEAST(0.99,
                              (success_count + 1 + 1) /
                              (success_count + 1 + failure_count + 2)),
                            updated_at    = CURRENT_TIMESTAMP
                        WHERE id = %s
                        """,
                        (node_id,),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE knowledge_graph_nodes
                        SET failure_count = failure_count + 1,
                            confidence    = GREATEST(0.05,
                              (success_count + 1) /
                              (success_count + failure_count + 1 + 2)),
                            updated_at    = CURRENT_TIMESTAMP
                        WHERE id = %s
                        """,
                        (node_id,),
                    )
            conn.commit()

    @staticmethod
    def delete_node(node_id: str) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM knowledge_graph_nodes WHERE id = %s", (node_id,))
            conn.commit()

    # ============================================================== EDGES ===

    @staticmethod
    def upsert_edge(
        *,
        src_node_id: str,
        dst_node_id: str,
        edge_type: str,
        weight_delta: float = 0.0,
    ) -> str:
        """Create or strengthen a directed edge. weight_delta is added to the
        existing weight; evidence_count always increments."""
        edge_id = _new_id("KGE")
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO knowledge_graph_edges
                      (id, src_node_id, dst_node_id, edge_type, weight, evidence_count)
                    VALUES (%s, %s, %s, %s, %s, 1)
                    ON DUPLICATE KEY UPDATE
                      weight = weight + %s,
                      evidence_count = evidence_count + 1,
                      updated_at = CURRENT_TIMESTAMP
                    """,
                    (edge_id, src_node_id, dst_node_id, edge_type, 1.0 + weight_delta, weight_delta),
                )
            conn.commit()
        return edge_id

    @staticmethod
    def edges_from(node_id: str, edge_type: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = (
            "SELECT e.*, n.label AS dst_label, n.node_type AS dst_type, n.category AS dst_category "
            "FROM knowledge_graph_edges e "
            "JOIN knowledge_graph_nodes n ON n.id = e.dst_node_id "
            "WHERE e.src_node_id = %s"
        )
        params: List[Any] = [node_id]
        if edge_type:
            sql += " AND e.edge_type = %s"
            params.append(edge_type)
        sql += " ORDER BY e.weight DESC"
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(sql, tuple(params))
                return cur.fetchall()

    @staticmethod
    def edges_to(node_id: str, edge_type: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = (
            "SELECT e.*, n.label AS src_label, n.node_type AS src_type, n.category AS src_category "
            "FROM knowledge_graph_edges e "
            "JOIN knowledge_graph_nodes n ON n.id = e.src_node_id "
            "WHERE e.dst_node_id = %s"
        )
        params: List[Any] = [node_id]
        if edge_type:
            sql += " AND e.edge_type = %s"
            params.append(edge_type)
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(sql, tuple(params))
                return cur.fetchall()

    # ====================================================== INCIDENT LINKS ===

    @staticmethod
    def link_incident(
        *,
        incident_id: str,
        node_id: str,
        role: str,                   # symptom_match | cause_match | resolution_applied | taught
        was_successful: Optional[bool] = None,
        notes: Optional[str] = None,
    ) -> str:
        link_id = _new_id("KGL")
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO kg_incident_links
                      (id, incident_id, node_id, role, was_successful, notes)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      was_successful = COALESCE(VALUES(was_successful), was_successful),
                      notes = COALESCE(VALUES(notes), notes)
                    """,
                    (link_id, incident_id, node_id, role, was_successful, notes),
                )
            conn.commit()
        return link_id

    @staticmethod
    def list_links_for_incident(incident_id: str) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT l.*, n.label, n.node_type, n.confidence
                    FROM kg_incident_links l
                    JOIN knowledge_graph_nodes n ON n.id = l.node_id
                    WHERE l.incident_id = %s
                    ORDER BY l.created_at DESC
                    """,
                    (incident_id,),
                )
                return cur.fetchall()

    @staticmethod
    def list_incidents_for_node(node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT l.*, i.subject, i.status, i.priority, i.created_at AS incident_created
                    FROM kg_incident_links l
                    JOIN incidents i ON i.id = l.incident_id
                    WHERE l.node_id = %s
                    ORDER BY l.created_at DESC
                    LIMIT %s
                    """,
                    (node_id, limit),
                )
                return cur.fetchall()

    # ============================================================== STATS ===

    @staticmethod
    def stats() -> Dict[str, int]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT
                      SUM(CASE WHEN node_type='symptom' THEN 1 ELSE 0 END)    AS symptoms,
                      SUM(CASE WHEN node_type='cause' THEN 1 ELSE 0 END)      AS causes,
                      SUM(CASE WHEN node_type='resolution' THEN 1 ELSE 0 END) AS resolutions,
                      COUNT(*) AS total_nodes
                    FROM knowledge_graph_nodes
                    WHERE is_active = TRUE
                    """
                )
                node_stats = cur.fetchone() or {}
                cur.execute("SELECT COUNT(*) AS n FROM knowledge_graph_edges")
                edge_stats = cur.fetchone() or {}
                cur.execute(
                    "SELECT COUNT(*) AS n FROM kg_incident_links "
                    "WHERE role = 'resolution_applied' AND was_successful = TRUE"
                )
                applied = cur.fetchone() or {}
        return {
            "symptoms": int(node_stats.get("symptoms") or 0),
            "causes": int(node_stats.get("causes") or 0),
            "resolutions": int(node_stats.get("resolutions") or 0),
            "total_nodes": int(node_stats.get("total_nodes") or 0),
            "total_edges": int(edge_stats.get("n") or 0),
            "successful_applications": int(applied.get("n") or 0),
        }
