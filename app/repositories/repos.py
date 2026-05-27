"""Repositories for runbooks, KB articles, escalations, audit log."""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db import get_db


# ============================================================================
# Runbook
# ============================================================================
class RunbookRepository:
    @staticmethod
    def list_all() -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM runbook_upload ORDER BY name ASC")
                rows = cur.fetchall()
        return [_hydrate_runbook(r) for r in rows]

    @staticmethod
    def find_by_id(rb_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM runbook_upload WHERE id = %s", (rb_id,))
                row = cur.fetchone()
        return _hydrate_runbook(row) if row else None

    @staticmethod
    def find_by_category(category: str) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM runbook_upload WHERE category = %s AND is_active = TRUE",
                    (category,),
                )
                rows = cur.fetchall()
        return [_hydrate_runbook(r) for r in rows]


    @staticmethod
    def record_execution(rb_id: str, success: bool, duration_seconds: float) -> None:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM runbook_upload WHERE id = %s", (rb_id,))
                rb = cur.fetchone()
                if not rb:
                    return
                count = (rb["execution_count"] or 0) + 1
                successes = (rb["successful_executions"] or 0) + (1 if success else 0)
                # rolling average
                prev_avg = rb["average_duration_seconds"] or 0
                new_avg = (prev_avg * (count - 1) + duration_seconds) / count
                rate = successes / count if count else 0
                cur.execute(
                    "UPDATE runbook_upload SET execution_count = %s, successful_executions = %s, "
                    "success_rate = %s, average_duration_seconds = %s, last_updated = %s "
                    "WHERE id = %s",
                    (count, successes, rate, new_avg, datetime.now(), rb_id),
                )
            conn.commit()


class RunbookUploadRepository:
    @staticmethod
    def create(payload: Dict[str, Any]) -> int:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO runbook_upload 
                    (name, files, files_type, content, summary, execution_steps) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        payload["name"],
                        payload["files"],
                        payload["files_type"],
                        json.dumps(payload.get("content", {})),
                        json.dumps(payload.get("summary", {})),
                        json.dumps(payload.get("execution_steps", [])),
                    ),
                )
                new_id = cur.lastrowid
            conn.commit()
        return new_id
    
    @staticmethod
    def update_ai_fields(upload_id: int, payload: Dict[str, Any]) -> None:

        with get_db() as conn:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    UPDATE runbook_upload
                    SET
                        summary = %s,
                        execution_steps = %s,
                        content = %s
                    WHERE id = %s
                    """,
                    (
                        json.dumps(payload.get("summary", {})),
                        json.dumps(payload.get("execution_steps", [])),
                        json.dumps(payload.get("content", {})),
                        upload_id,
                    ),
                )

            conn.commit()

    @staticmethod
    def find_by_id(upload_id: int) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM runbook_upload WHERE id = %s", (upload_id,))
                row = cur.fetchone()
        return _hydrate_upload(row) if row else None

    @staticmethod
    def list_all() -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("""
                    SELECT
                        id,
                        name,
                        files,
                        files_type,
                        summary,
                        execution_steps,
                        created_at,
                        updated_at
                    FROM runbook_upload
                    ORDER BY created_at DESC
                """)
                rows = cur.fetchall()
        return [_hydrate_upload(r) for r in rows]

    @staticmethod
    def delete(upload_id: int) -> bool:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                # First check runbook exists and get file path
                cur.execute(
                    "SELECT files FROM runbook_upload WHERE id = %s",
                    (upload_id,),
                )
                row = cur.fetchone()

                if not row:
                    return False

                # Delete from MySQL
                cur.execute(
                    "DELETE FROM runbook_upload WHERE id = %s",
                    (upload_id,),
                )

            conn.commit()

        # Delete uploaded file from uploads folder
        try:
            import os
            file_path = row.get("files")
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print("RUNBOOK FILE DELETE ERROR:", str(e))

        return True

def _hydrate_upload(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {}
    for col in ("content", "summary", "execution_steps"):
        val = row.get(col)
        if isinstance(val, str):
            try:
                row[col] = json.loads(val)
            except (TypeError, json.JSONDecodeError):
                row[col] = {}
        elif val is None:
            row[col] = {}
    return row

def _hydrate_article_upload(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {}
    for col in ("content", "summary"):
        val = row.get(col)
        if isinstance(val, str):
            try:
                row[col] = json.loads(val)
            except (TypeError, json.JSONDecodeError):
                row[col] = {}
        elif val is None:
            row[col] = {}
    return row


class ArticleUploadRepository:
    @staticmethod
    def create(payload: Dict[str, Any]) -> int:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO articles_upload 
                    (name, files, files_type, content, summary, author) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        payload["name"],
                        payload["files"],
                        payload["files_type"],
                        json.dumps(payload.get("content", {})),
                        json.dumps(payload.get("summary", {})),
                        payload.get("author"),
                    ),
                )
                new_id = cur.lastrowid
            conn.commit()
        return new_id

    @staticmethod
    def find_by_id(upload_id: int) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM articles_upload WHERE id = %s", (upload_id,))
                row = cur.fetchone()
        return _hydrate_article_upload(row) if row else None

    @staticmethod
    def list_all() -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("""
                    SELECT
                        id,
                        name,
                        files,
                        files_type,
                        summary,
                        author,
                        created_at,
                        updated_at
                    FROM articles_upload
                    ORDER BY id DESC
                """)
                rows = cur.fetchall()
        return [_hydrate_article_upload(r) for r in rows]
    
    @staticmethod
    def update_ai_fields(upload_id: int, payload: Dict[str, Any]) -> None:

        with get_db() as conn:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    UPDATE articles_upload
                    SET
                        summary = %s,
                        content = %s,
                        author = %s
                    WHERE id = %s
                    """,
                    (
                        json.dumps(payload.get("summary", {})),
                        json.dumps(payload.get("content", {})),
                        payload.get("author"),
                        upload_id,
                    ),
                )

            conn.commit()

    @staticmethod
    def delete(upload_id: int) -> bool:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:

                cur.execute(
                    "SELECT files FROM articles_upload WHERE id = %s",
                    (upload_id,)
                )

                row = cur.fetchone()

                if not row:
                    return False

                cur.execute(
                    "DELETE FROM articles_upload WHERE id = %s",
                    (upload_id,)
                )

            conn.commit()

        return True

def _hydrate_runbook(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {}

    # Parse JSON columns
    for col in ("content", "summary", "execution_steps"):

        val = row.get(col)

        if isinstance(val, str):
            try:
                row[col] = json.loads(val)
            except (TypeError, json.JSONDecodeError):

                if col == "execution_steps":
                    row[col] = []
                else:
                    row[col] = {}

        elif val is None:

            if col == "execution_steps":
                row[col] = []
            else:
                row[col] = {}

    return row

# ============================================================================
# Knowledge Base
# ============================================================================
class KBRepository:
    @staticmethod
    def list_all(only_published: bool = True) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM knowledge_articles"
        if only_published:
            sql += " WHERE is_published = TRUE"
        sql += " ORDER BY updated_at DESC"
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        return [_hydrate_kb(r) for r in rows]

    @staticmethod
    def find_by_id(article_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM knowledge_articles WHERE id = %s", (article_id,))
                row = cur.fetchone()
        return _hydrate_kb(row) if row else None

    @staticmethod
    def search_text(q: str) -> List[Dict[str, Any]]:
        like = f"%{q}%"
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM knowledge_articles "
                    "WHERE is_published = TRUE AND "
                    "(title LIKE %s OR content LIKE %s OR summary LIKE %s OR tags LIKE %s) "
                    "ORDER BY views DESC LIMIT 50",
                    (like, like, like, like),
                )
                rows = cur.fetchall()
        return [_hydrate_kb(r) for r in rows]

    @staticmethod
    def create(payload: Dict[str, Any]) -> str:
        new_id = payload.get("id") or f"KB-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now()
        kg_node_id = payload.get("kg_node_id")
        with get_db() as conn:
            with conn.cursor() as cur:
                # We try the schema with kg_node_id first (post-migration-004)
                # and fall back to the legacy column set if the column is
                # missing — keeps things working on a freshly-migrated DB
                # before 004 has been applied.
                try:
                    cur.execute(
                        "INSERT INTO knowledge_articles "
                        "(id, title, content, summary, tags, category, author, kg_node_id, "
                        "views, helpful, not_helpful, is_published, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0, %s, %s, %s)",
                        (
                            new_id,
                            payload["title"],
                            payload["content"],
                            payload.get("summary", payload["content"][:200]),
                            json.dumps(payload.get("tags", [])),
                            payload.get("category", "General"),
                            payload.get("author", "system"),
                            kg_node_id,
                            bool(payload.get("is_published", True)),
                            now,
                            now,
                        ),
                    )
                except Exception:  # noqa: BLE001 — column missing pre-migration
                    cur.execute(
                        "INSERT INTO knowledge_articles "
                        "(id, title, content, summary, tags, category, author, "
                        "views, helpful, not_helpful, is_published, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 0, 0, %s, %s, %s)",
                        (
                            new_id,
                            payload["title"],
                            payload["content"],
                            payload.get("summary", payload["content"][:200]),
                            json.dumps(payload.get("tags", [])),
                            payload.get("category", "General"),
                            payload.get("author", "system"),
                            bool(payload.get("is_published", True)),
                            now,
                            now,
                        ),
                    )
            conn.commit()
        return new_id

    @staticmethod
    def increment_views(article_id: str) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE knowledge_articles SET views = views + 1 WHERE id = %s",
                    (article_id,),
                )
            conn.commit()
    


def _hydrate_kb(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {}
    val = row.get("tags")
    if isinstance(val, str):
        try:
            row["tags"] = json.loads(val)
        except (TypeError, json.JSONDecodeError):
            row["tags"] = []
    elif val is None:
        row["tags"] = []
    return row


# ============================================================================
# Escalations
# ============================================================================
class EscalationRepository:
    @staticmethod
    def list_active() -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM escalations WHERE status != 'resolved' "
                    "ORDER BY priority ASC, created_at DESC"
                )
                rows = cur.fetchall()
        return [_hydrate_escalation(r) for r in rows]

    @staticmethod
    def find_by_id(esc_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM escalations WHERE id = %s", (esc_id,))
                row = cur.fetchone()
        return _hydrate_escalation(row) if row else None

    @staticmethod
    def create(payload: Dict[str, Any]) -> str:
        new_id = payload.get("id") or f"ESC-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now()
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO escalations "
                    "(id, incident_id, reason, diagnostic, attempted_actions, "
                    "assigned_engineer, priority, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        new_id,
                        payload["incident_id"],
                        payload["reason"],
                        payload.get("diagnostic", ""),
                        json.dumps(payload.get("attempted_actions", [])),
                        payload.get("assigned_engineer"),
                        payload.get("priority", "P3"),
                        payload.get("status", "pending"),
                        now,
                    ),
                )
            conn.commit()
        return new_id

    @staticmethod
    def update(esc_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        if "attempted_actions" in fields and isinstance(fields["attempted_actions"], list):
            fields["attempted_actions"] = json.dumps(fields["attempted_actions"])
        cols = ", ".join(f"{k} = %s" for k in fields.keys())
        values = list(fields.values()) + [esc_id]
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE escalations SET {cols} WHERE id = %s", tuple(values))
            conn.commit()


def _hydrate_escalation(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {}
    val = row.get("attempted_actions")
    if isinstance(val, str):
        try:
            row["attempted_actions"] = json.loads(val)
        except (TypeError, json.JSONDecodeError):
            row["attempted_actions"] = []
    elif val is None:
        row["attempted_actions"] = []
    return row


# ============================================================================
# Audit log
# ============================================================================
class AuditRepository:
    @staticmethod
    def log(
        actor: str,
        action: str,
        target: str,
        target_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        log_id = f"LOG-{uuid.uuid4().hex[:10]}"
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO audit_logs "
                    "(id, actor, action, target, target_type, metadata, timestamp) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        log_id,
                        actor,
                        action,
                        target,
                        target_type,
                        json.dumps(metadata or {}),
                        datetime.now(),
                    ),
                )
            conn.commit()
        return log_id

    @staticmethod
    def list_recent(limit: int = 100) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT %s",
                    (limit,),
                )
                rows = cur.fetchall()
        for r in rows:
            val = r.get("metadata")
            if isinstance(val, str):
                try:
                    r["metadata"] = json.loads(val)
                except (TypeError, json.JSONDecodeError):
                    r["metadata"] = {}
            elif val is None:
                r["metadata"] = {}
        return rows
