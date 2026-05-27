"""Incident repository — owns all SQL touching the `incidents` and
`incident_steps` tables."""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from app.core.email_service import list_incident_emails
from app.db import get_db


class IncidentRepository:
    # ---- create ----------------------------------------------------------------
    @staticmethod
    def create(payload: Dict[str, Any]) -> str:
        new_id = payload.get("id") or f"INC-{uuid.uuid4().hex[:10].upper()}"
        now = datetime.now()
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO incidents (
                        id, subject, description, caller, caller_email, source,
                        status, priority, severity, category, subcategory,
                        assigned_to, sla_deadline, sla_breached, auto_resolved,
                        confidence, tags, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    """,
                    (
                        new_id,
                        payload["subject"],
                        payload["description"],
                        payload["caller"],
                        payload.get("caller_email"),
                        payload.get("source", "user_chat"),
                        payload.get("status", "new"),
                        payload.get("priority", "P3"),
                        payload.get("severity", "medium"),
                        payload.get("category") or "Uncategorised",
                        payload.get("subcategory"),
                        payload.get("assigned_to"),
                        payload.get("sla_deadline"),
                        payload.get("sla_breached", False),
                        payload.get("auto_resolved", False),
                        payload.get("confidence", 0.0),
                        json.dumps(payload.get("tags", [])),
                        now,
                        now,
                    ),
                )
            conn.commit()
        return new_id

    # ---- read ------------------------------------------------------------------
    @staticmethod
    def find_by_id(incident_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM incidents WHERE id = %s LIMIT 1", (incident_id,))
                row = cur.fetchone()
        return _hydrate(row) if row else None

    @staticmethod
    def list(
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> Tuple[List[Dict[str, Any]], int]:
        where: List[str] = []
        params: List[Any] = []
        if status:
            where.append("status = %s")
            params.append(status)
        if priority:
            where.append("priority = %s")
            params.append(priority)
        if category:
            where.append("category = %s")
            params.append(category)
        if search:
            where.append("(subject LIKE %s OR description LIKE %s OR caller LIKE %s OR id LIKE %s)")
            like = f"%{search}%"
            params.extend([like, like, like, like])

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        sort_col = {
            "createdAt": "created_at",
            "updatedAt": "updated_at",
            "priority": "priority",
            "status": "status",
        }.get(sort_by, sort_by if sort_by in {"created_at", "updated_at"} else "created_at")
        order = "ASC" if sort_order.lower() == "asc" else "DESC"
        offset = max(0, (page - 1) * page_size)

        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(f"SELECT COUNT(*) AS total FROM incidents {where_sql}", tuple(params))
                total = cur.fetchone()["total"]

                cur.execute(
                    f"SELECT * FROM incidents {where_sql} "
                    f"ORDER BY {sort_col} {order} LIMIT %s OFFSET %s",
                    tuple(params + [page_size, offset]),
                )
                rows = cur.fetchall()
        return [_hydrate(r) for r in rows], total

    # ---- update ----------------------------------------------------------------
    @staticmethod
    def update(incident_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        if "tags" in fields and isinstance(fields["tags"], list):
            fields["tags"] = json.dumps(fields["tags"])
        if "category" in fields and not fields["category"]:
            fields["category"] = "Uncategorised"
        fields["updated_at"] = datetime.now()

        cols = ", ".join(f"{k} = %s" for k in fields.keys())
        values = list(fields.values()) + [incident_id]
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE incidents SET {cols} WHERE id = %s", tuple(values))
            conn.commit()

    # ---- agent steps -----------------------------------------------------------
    @staticmethod
    def add_step(incident_id: str, step: Dict[str, Any]) -> str:
        step_id = step.get("id") or f"STP-{uuid.uuid4().hex[:10]}"
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO incident_steps (
                        id, incident_id, agent, action, output, type, metadata, timestamp
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        step_id,
                        incident_id,
                        step["agent"],
                        step["action"],
                        step["output"],
                        step["type"],
                        json.dumps(step.get("metadata", {})),
                        step.get("timestamp", datetime.now()),
                    ),
                )
            conn.commit()
        return step_id

    @staticmethod
    def get_steps(incident_id: str) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM incident_steps WHERE incident_id = %s "
                    "ORDER BY timestamp ASC",
                    (incident_id,),
                )
                rows = cur.fetchall()
        for r in rows:
            r["metadata"] = json.loads(r["metadata"]) if r.get("metadata") else {}
        return rows


def _hydrate(row: Dict[str, Any]) -> Dict[str, Any]:
    """Decode JSON columns and merge agent steps."""
    if "tags" in row and isinstance(row["tags"], str):
        try:
            row["tags"] = json.loads(row["tags"])
        except (TypeError, json.JSONDecodeError):
            row["tags"] = []
    if not row.get("tags"):
        row["tags"] = []
    if row.get("id"):
        row["steps"] = IncidentRepository.get_steps(row["id"])
        row["emails"] = list_incident_emails(row["id"])
    else:
        row["steps"] = []
        row["emails"] = []
    return row



