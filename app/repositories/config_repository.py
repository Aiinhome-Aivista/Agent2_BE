from app.db import get_db


class ConfigRepository:

    @staticmethod
    def get(key: str, default=None):
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT config_value
                    FROM system_config
                    WHERE config_key = %s
                    LIMIT 1
                    """,
                    (key,),
                )

                row = cur.fetchone()

                if not row:
                    return default

                return row["config_value"]