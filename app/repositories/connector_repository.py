"""Repository for the `connectors`, `connector_field_mappings`, `webhook_events`
and `connector_sync_state` tables.

Pure data access — no business logic, no encryption (that's `credentials.py`).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db import get_db


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _row_decode(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """JSON-decode the `config` column on a row read."""
    if row is None:
        return None
    if isinstance(row.get("config"), str):
        try:
            row["config"] = json.loads(row["config"])
        except json.JSONDecodeError:
            row["config"] = {}
    return row


class ConnectorRepository:
    # --------------------------------------------------------------- connectors
    @staticmethod
    def create(
        provider: str,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        created_by: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        cid = _new_id("CONN")
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO connectors
                      (id, provider, name, status, config, webhook_secret, created_by)
                    VALUES (%s, %s, %s, 'disconnected', %s, %s, %s)
                    """,
                    (cid, provider, name, json.dumps(config or {}), webhook_secret, created_by),
                )
            conn.commit()
        result = ConnectorRepository.find_by_id(cid)
        if result is None:
            raise RuntimeError(f"Failed to read back connector {cid} after insert")
        return result

    @staticmethod
    def find_by_id(connector_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM connectors WHERE id = %s LIMIT 1", (connector_id,))
                return _row_decode(cur.fetchone())

    @staticmethod
    def list(*, provider: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM connectors WHERE 1=1"
        params: List[Any] = []
        if provider:
            sql += " AND provider = %s"
            params.append(provider)
        if status:
            sql += " AND status = %s"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
        return [r for r in (_row_decode(row) for row in rows) if r is not None]

    @staticmethod
    def update(connector_id: str, patch: Dict[str, Any]) -> None:
        if not patch:
            return
        cols: List[str] = []
        vals: List[Any] = []
        for k, v in patch.items():
            cols.append(f"{k} = %s")
            vals.append(json.dumps(v) if k == "config" and not isinstance(v, str) else v)
        vals.append(connector_id)
        sql = f"UPDATE connectors SET {', '.join(cols)}, updated_at = CURRENT_TIMESTAMP WHERE id = %s"
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(vals))
            conn.commit()

    @staticmethod
    def delete(connector_id: str) -> None:
        # ON DELETE CASCADE handles credentials/sync_state/mappings/webhook_events.
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM connectors WHERE id = %s", (connector_id,))
            conn.commit()

    @staticmethod
    def touch_sync(connector_id: str, error: Optional[str] = None) -> None:
        status_set = ", status = 'error'" if error else ""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE connectors SET last_synced_at = CURRENT_TIMESTAMP, last_error = %s{status_set} WHERE id = %s",
                    (error, connector_id),
                )
            conn.commit()

    # ----------------------------------------------------------- field mappings
    @staticmethod
    def list_mappings(connector_id: str) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM connector_field_mappings WHERE connector_id = %s ORDER BY local_field",
                    (connector_id,),
                )
                rows = cur.fetchall()
        for r in rows:
            if isinstance(r.get("transform"), str):
                try:
                    r["transform"] = json.loads(r["transform"])
                except json.JSONDecodeError:
                    r["transform"] = {}
        return rows

    @staticmethod
    def replace_mappings(connector_id: str, mappings: List[Dict[str, Any]]) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM connector_field_mappings WHERE connector_id = %s",
                    (connector_id,),
                )
                for m in mappings:
                    mid = _new_id("MAP")
                    cur.execute(
                        """
                        INSERT INTO connector_field_mappings
                          (id, connector_id, local_field, remote_field, direction, transform, is_required)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            mid,
                            connector_id,
                            m["local_field"],
                            m["remote_field"],
                            m.get("direction", "both"),
                            json.dumps(m.get("transform") or {}),
                            bool(m.get("is_required", False)),
                        ),
                    )
            conn.commit()

    # ----------------------------------------------------------- webhook events
    @staticmethod
    def record_webhook(
        connector_id: str,
        external_event_id: Optional[str],
        event_type: str,
        payload: Dict[str, Any],
        signature_valid: bool,
    ) -> Dict[str, Any]:
        """Insert or report duplicate. Returns {id, duplicate: bool}."""
        wid = _new_id("WH")
        with get_db() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO webhook_events
                          (id, connector_id, external_event_id, event_type, payload, signature_valid)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (wid, connector_id, external_event_id, event_type, json.dumps(payload), signature_valid),
                    )
                    conn.commit()
                    return {"id": wid, "duplicate": False}
                except Exception as e:  # noqa: BLE001
                    # Duplicate key on (connector_id, external_event_id)
                    if "Duplicate" in str(e) or "1062" in str(e):
                        conn.rollback()
                        return {"id": None, "duplicate": True}
                    raise

    @staticmethod
    def mark_webhook_processed(
        webhook_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE webhook_events
                    SET process_status = %s, processed_at = CURRENT_TIMESTAMP, error = %s
                    WHERE id = %s
                    """,
                    (status, error, webhook_id),
                )
            conn.commit()

    @staticmethod
    def list_webhook_events(connector_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT id, external_event_id, event_type, signature_valid,
                           received_at, processed_at, process_status, error
                    FROM webhook_events
                    WHERE connector_id = %s
                    ORDER BY received_at DESC
                    LIMIT %s
                    """,
                    (connector_id, limit),
                )
                return cur.fetchall()
