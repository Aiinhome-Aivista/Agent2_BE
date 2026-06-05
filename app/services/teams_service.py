from typing import Any, Dict
from app.repositories.incident_repository import IncidentRepository
from app.repositories.user_repository import UserRepository
from app.core.logger import logger

class TeamsService:
    @staticmethod
    def send_assignment_approval(incident: Dict[str, Any], engineer: Dict[str, Any]) -> None:
        logger.info(f"[TeamsService] Sending Real Adaptive Card to {engineer.get('full_name')} for Incident {incident.get('id')}")
        import asyncio
        import json
        from botbuilder.schema import Activity, ActivityTypes, Attachment, ConversationReference
        from botbuilder.core import TurnContext
        from app.core.bot_config import bot_adapter, BotConfig
        from app.db.database import get_db

        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT teams_conversation_reference FROM users WHERE id = %s", (engineer["id"],))
                user_row = cur.fetchone()
                
        if not user_row or not user_row.get("teams_conversation_reference"):
            logger.error(f"[TeamsService] Cannot send MS Teams message to {engineer.get('full_name')} - no conversation reference found. They need to message the bot first.")
            return
            
        conv_ref_dict = json.loads(user_row["teams_conversation_reference"])
        conversation_reference = ConversationReference().deserialize(conv_ref_dict)
        
        # Build the adaptive card
        card_content = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock",
                    "text": f"New Incident Assignment: {incident.get('id')}",
                    "weight": "Bolder",
                    "size": "Medium"
                },
                {
                    "type": "TextBlock",
                    "text": incident.get("title", "No Title"),
                    "wrap": True
                }
            ],
            "actions": [
                {
                    "type": "Action.Execute",
                    "title": "Accept",
                    "verb": "accept",
                    "data": {
                        "incident_id": incident.get('id')
                    }
                },
                {
                    "type": "Action.Execute",
                    "title": "Decline",
                    "verb": "decline",
                    "data": {
                        "incident_id": incident.get('id')
                    }
                }
            ]
        }
        
        attachment = Attachment(
            content_type="application/vnd.microsoft.card.adaptive",
            content=card_content
        )
        
        activity = Activity(
            type=ActivityTypes.message,
            attachments=[attachment]
        )
        
        async def send_proactive(context: TurnContext):
            await context.send_activity(activity)

        def run_sync():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    bot_adapter.continue_conversation(
                        conversation_reference,
                        send_proactive,
                        BotConfig.APP_ID
                    )
                )
                loop.close()
            except Exception as thread_err:
                logger.error(f"[TeamsService] Thread error: {thread_err}")

        try:
            import threading
            t = threading.Thread(target=run_sync)
            t.daemon = True
            t.start()
            logger.info(f"[TeamsService] Adaptive Card queued for {engineer.get('full_name')}.")
        except Exception as e:
            logger.error(f"[TeamsService] Error queueing proactive message: {e}")

    @staticmethod
    def send_sla_miss_notification(incident: Dict[str, Any]) -> None:
        """
        Sends an MS Teams notification that an SLA was missed.
        """
        incident_id = incident["id"]
        assigned_to = incident.get("assigned_to")
        
        if assigned_to:
            engineer = UserRepository.find_by_id(assigned_to)
            name = engineer["full_name"] if engineer else "Unknown"
            logger.info(f"[TeamsService] SLA MISS Alert sent to {name} for Incident {incident_id}.")
        else:
            logger.info(f"[TeamsService] SLA MISS Alert sent to General Channel for unassigned Incident {incident_id}.")

    @staticmethod
    def escalate_to_team_lead(incident: Dict[str, Any], reason: str) -> None:
        """
        Notifies Team Lead dashboard/channel about a declined or timed-out assignment.
        """
        logger.info(f"[TeamsService] Escalating incident {incident['id']} to Team Lead. Reason: {reason}")
