from datetime import datetime, timedelta

from app.core.email_service import send_high_priority_escalation
from app.core.logger import logger
from app.repositories import IncidentRepository, UserRepository
from app.repositories.config_repository import ConfigRepository
from app.repositories.escalation_tracker_repository import (
    EscalationTrackerRepository,
)


def process_pending_escalations():

    trackers = EscalationTrackerRepository.get_active_trackers()

    if not trackers:
        return

    engineers = UserRepository.list_engineers()

    if not engineers:
        return

    interval = int(
        ConfigRepository.get(
            "escalation_email_interval_minutes",
            3,
        )
    )

    logger.info(
        f"[escalation-email] checking pending escalations "
        f"with interval={interval} minutes"
    )

    for tracker in trackers:

        incident = IncidentRepository.find_by_id(
            tracker["incident_id"]
        )

        if not incident:
            continue

        # stop if resolved
        if incident["status"] == "resolved":

            EscalationTrackerRepository.update(
                tracker["id"],
                {
                    "completed": 1,
                },
            )

            logger.info(
                f"[escalation-email] stopped for "
                f"{incident['id']} because resolved"
            )

            continue

        last_sent = tracker["last_email_sent_at"]

        # mysql can sometimes return string timestamps
        if isinstance(last_sent, str):

            last_sent = datetime.strptime(
                last_sent,
                "%Y-%m-%d %H:%M:%S",
            )

        next_time = last_sent + timedelta(
            minutes=interval
        )

        # not time yet
        if datetime.now() < next_time:
            continue

        next_index = tracker["current_engineer_index"] + 1

        # all engineers completed
        if next_index >= len(engineers):

            EscalationTrackerRepository.update(
                tracker["id"],
                {
                    "completed": 1,
                },
            )

            logger.info(
                f"[escalation-email] completed all engineers "
                f"for incident {incident['id']}"
            )

            continue

        previous_engineer = engineers[
            tracker["current_engineer_index"]
        ]

        next_engineer = engineers[next_index]

        extra_message = (
            f"Email has already been sent to "
            f"{previous_engineer['full_name']} "
            f"but the issue is still not resolved."
        )

        ok = send_high_priority_escalation(
            incident=incident,
            engineer_email=next_engineer["email"],
            engineer_name=next_engineer["full_name"],
            extra_message=extra_message,
        )

        if ok:
            logger.info(
                f"[escalation-email] sent next escalation "
                f"for {incident['id']} to "
                f"{next_engineer['full_name']}"
            )

            EscalationTrackerRepository.update(
                tracker["id"],
                {
                    "current_engineer_index": next_index,
                    "last_email_sent_at": datetime.now(),
                },
            )