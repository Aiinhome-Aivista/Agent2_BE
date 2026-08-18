from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from app.core import logger
from app.db.database import get_db
from app.services.connector_service import ConnectorService

from app.check_missed_slas_job import check_missed_slas
from app.core.config import settings

scheduler = BackgroundScheduler()

def auto_sync():
    """Background task to poll all enabled connectors for new data."""

    logger.info("[scheduler] checking for connectors to sync...")


    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute("""
                SELECT *
                FROM connectors
                WHERE status = 'connected'
                  AND sync_enabled = TRUE
            """)
            connectors = cur.fetchall()

    now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    connectors_to_sync = []

    for connector in connectors:
        interval_sec = connector.get("poll_interval_sec") or settings.connector_default_poll_interval_sec
        last_synced = connector.get("last_synced_at")

        if last_synced is None:
            should_sync = True
        else:
            next_sync = last_synced + timedelta(seconds=interval_sec)
            should_sync = now >= next_sync

        if should_sync:
            connectors_to_sync.append(connector)

    for connector in connectors_to_sync:
        try:
            logger.info(
                f"[scheduler] auto syncing "
                f"{connector['name']} "
                f"({connector['id']})"
            )

            # sync_now can take a long time, so we run it outside the outer DB connection
            result = ConnectorService.sync_now(connector["id"])

            if result.get("ok"):
                # Open a short-lived DB connection just for the update
                with get_db() as conn:
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
            minutes=settings.scheduler_interval_minutes,
            id="auto_sync_job",
            replace_existing=True
        )

    if not scheduler.get_job("check_missed_slas_job"):
        scheduler.add_job(
            check_missed_slas,
            trigger="interval",
            minutes=1,
            id="check_missed_slas_job",
            replace_existing=True
        )

    from app.services.ticket_management_service import TicketManagementService
    
    def auto_assign_tickets():
        try:
            logger.info("[scheduler] checking for unassigned tickets to distribute...")
            TicketManagementService.assign_unassigned_tickets_equally()
        except Exception as e:
            logger.exception(f"[scheduler] auto assign tickets failed: {e}")

    if not scheduler.get_job("auto_assign_tickets_job"):
        scheduler.add_job(
            auto_assign_tickets,
            trigger="interval",
            minutes=1,
            id="auto_assign_tickets_job",
            replace_existing=True
        )

    if not scheduler.running:
        scheduler.start()
        logger.info("[scheduler] background scheduler started (check interval: 1m)")
