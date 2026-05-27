from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from app.core import logger
from app.db.database import get_db
from app.services.connector_service import ConnectorService
from app.escalation_email_scheduler import (
    process_pending_escalations,
)

scheduler = BackgroundScheduler()

def auto_sync():
    """Background task to poll all enabled connectors for new data."""

    logger.info("[scheduler] checking for connectors to sync...")

    # process escalation emails
    try:
        process_pending_escalations()
    except Exception as e:
        logger.exception(
            f"[scheduler] escalation email scheduler failed: {e}"
        )

    with get_db() as conn:

        with conn.cursor(dictionary=True) as cur:

            cur.execute("""
                SELECT *
                FROM connectors
                WHERE status = 'connected'
                  AND sync_enabled = TRUE
            """)

            connectors = cur.fetchall()

        now = datetime.now()

        for connector in connectors:

            interval_sec = connector.get("poll_interval_sec", 120)

            last_synced = connector.get("last_synced_at")

            if last_synced is None:
                should_sync = True

            else:
                # mysql-connector returns datetime objects for DATETIME columns
                next_sync = last_synced + timedelta(seconds=interval_sec)

                should_sync = now >= next_sync

            if should_sync:

                try:

                    logger.info(
                        f"[scheduler] auto syncing "
                        f"{connector['name']} "
                        f"({connector['id']})"
                    )

                    # sync_now returns a summary:
                    # {"ok": bool, "fetched": int, ...}
                    result = ConnectorService.sync_now(
                        connector["id"]
                    )

                    if result.get("ok"):

                        with conn.cursor() as cur:

                            cur.execute("""
                                UPDATE connectors
                                SET last_synced_at = %s
                                WHERE id = %s
                            """, (now, connector["id"]))

                        conn.commit()

                        logger.info(
                            f"[scheduler] sync complete for "
                            f"{connector['name']}: "
                            f"{result.get('fetched', 0)} fetched"
                        )

                    else:

                        logger.error(
                            f"[scheduler] sync failed for "
                            f"{connector['name']}: "
                            f"{result.get('error')}"
                        )

                except Exception as e:

                    logger.exception(
                        f"[scheduler] unexpected error syncing "
                        f"{connector['name']}: {e}"
                    )
def start_scheduler():
    """Start the background scheduler."""
    # Run every 1 minute to check for due syncs.
    if not scheduler.get_job("auto_sync_job"):
        scheduler.add_job(
            auto_sync,
            trigger="interval",
            minutes=1,
            id="auto_sync_job",
            replace_existing=True
        )

    if not scheduler.running:
        scheduler.start()
        logger.info("[scheduler] background scheduler started (check interval: 1m)")
