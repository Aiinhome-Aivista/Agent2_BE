from datetime import datetime
from app.core.logger import logger
from app.db.database import get_db
from app.repositories.incident_repository import IncidentRepository
from app.services.teams_service import TeamsService
from app.services.ai_troubleshooting_service import AITroubleshootingService

def check_missed_slas():
    logger.info("[scheduler] checking for missed SLAs...")
    now = datetime.now()
    try:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("""
                    SELECT id FROM incidents
                    WHERE sla_deadline < %s
                      AND sla_breached = FALSE
                      AND status NOT IN ('resolved', 'closed')
                """, (now,))
                breached = cur.fetchall()

            for row in breached:
                incident_id = row['id']
                logger.info(f"[scheduler] SLA breached for {incident_id}. Marking and notifying.")
                
                # Mark breached
                IncidentRepository.update(incident_id, {"sla_breached": True})
                
                # Fetch full incident
                incident = IncidentRepository.find_by_id(incident_id)
                if not incident:
                    continue
                
                # Notify Teams
                TeamsService.send_sla_miss_notification(incident)
                
                # Ask AI for POC and attach to timeline
                ai_result = AITroubleshootingService.generate_solution_and_poc(incident)
                if ai_result:
                    IncidentRepository.add_step(incident_id, {
                        "agent": "AI Support Bot",
                        "action": "Generated Troubleshooting POC due to SLA breach",
                        "output": f"Suggested Steps:\n" + "\n".join(ai_result.get('solution_steps', [])) + "\n\nPOC Description: " + ai_result.get('poc_description', '') + "\n\nCode/Snippet:\n```\n" + ai_result.get('poc_code', '') + "\n```",
                        "type": "reason"
                    })
    except Exception as e:
        logger.exception(f"[scheduler] check_missed_slas failed: {e}")
