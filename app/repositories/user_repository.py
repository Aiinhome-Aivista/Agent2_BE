"""User repository — raw SQL via mysql-connector."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db import get_db


class UserRepository:
    @staticmethod
    def find_by_email(email: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT id, email, full_name, role, password_hash, avatar_url, "
                    "created_at, last_login_at FROM users WHERE email = %s LIMIT 1",
                    (email,),
                )
                return cur.fetchone()

    @staticmethod
    def find_by_id(user_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT id, email, full_name, role, avatar_url, "
                    "created_at, last_login_at FROM users WHERE id = %s LIMIT 1",
                    (user_id,),
                )
                return cur.fetchone()

    @staticmethod
    def update_last_login(user_id: str) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET last_login_at = %s WHERE id = %s",
                    (datetime.now(), user_id),
                )
            conn.commit()

    @staticmethod
    def list_engineers():
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM users
                    WHERE role = 'engineer'
                    AND is_active = 1
                    ORDER BY escalation_order ASC, created_at ASC
                    """
                )

                return cur.fetchall()