from typing import Any, Dict, List, Optional

from app.db import get_db


class EscalationTrackerRepository:

    @staticmethod
    def create(data: Dict[str, Any]) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO escalation_email_tracker
                    (
                        id,
                        incident_id,
                        current_engineer_index,
                        last_email_sent_at,
                        completed
                    )
                    VALUES (%s, %s, %s, NOW(), 0)
                    """,
                    (
                        data["id"],
                        data["incident_id"],
                        data.get("current_engineer_index", 0),
                    ),
                )

            conn.commit()

    @staticmethod
    def get_active_trackers() -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM escalation_email_tracker
                    WHERE completed = 0
                    """
                )

                return cur.fetchall()

    @staticmethod
    def update(tracker_id: str, updates: Dict[str, Any]) -> None:
        cols = ", ".join(f"{k} = %s" for k in updates)

        values = list(updates.values())
        values.append(tracker_id)

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE escalation_email_tracker
                    SET {cols}
                    WHERE id = %s
                    """,
                    tuple(values),
                )

            conn.commit()