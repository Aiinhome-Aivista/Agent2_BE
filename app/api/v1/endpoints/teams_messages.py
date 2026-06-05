import json
from fastapi import APIRouter, Request, Response
from botbuilder.schema import Activity, ActivityTypes
from botbuilder.core import TurnContext
from app.core.bot_config import bot_adapter
from app.core.logger import logger
from app.db import get_db
from app.services.ticket_management_service import TicketManagementService

router = APIRouter()

async def process_message_activity(turn_context: TurnContext):
    """
    Handle incoming messages from the user.
    """
    activity = turn_context.activity
    text = activity.text.strip().lower() if activity.text else ""
    
    user_id = activity.from_property.id
    user_name = activity.from_property.name
    
    # Save the conversation reference to database so we can message them later proactively
    conversation_reference = TurnContext.get_conversation_reference(activity)
    
    # In a real scenario, you'd link their Teams account with their internal ID via OAuth or matching.
    # For this demo, we will try to match by name, but if that fails, we will link it to 'Sara Engineer' (USR-ENG-001)
    # so that the person testing (you) will receive the ticket assignments.
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET teams_conversation_reference = %s, teams_user_id = %s WHERE full_name = %s OR id = 'USR-ENG-001'",
                (json.dumps(conversation_reference.serialize()), user_id, user_name)
            )
            conn.commit()

    if "hi" in text or "hello" in text:
        await turn_context.send_activity(f"Hello {user_name}! I have linked your MS Teams account to Intelligent Incident Agent. You will now receive ticket assignments here.")
    else:
        await turn_context.send_activity("I am the Intelligent Incident Agent bot. I will notify you when you are assigned tickets.")

async def process_invoke_activity(turn_context: TurnContext):
    """
    Handle Adaptive Card action submissions (Accept/Decline).
    """
    activity = turn_context.activity
    if activity.name == "adaptiveCard/action":
        action_data = activity.value.get("action", {})
        verb = action_data.get("verb")
        incident_id = action_data.get("data", {}).get("incident_id")
        
        if verb == "accept":
            TicketManagementService.handle_assignment_response(incident_id, accepted=True)
            await turn_context.send_activity(f"Thank you, you have accepted incident {incident_id}.")
        elif verb == "decline":
            TicketManagementService.handle_assignment_response(incident_id, accepted=False)
            await turn_context.send_activity(f"You have declined incident {incident_id}. It will be escalated to the Team Lead.")
        
        # Return a 200 OK for the invoke activity
        return activity.create_reply()
    
async def activity_handler(turn_context: TurnContext):
    """
    Main entry point for all Bot activities.
    """
    activity = turn_context.activity
    
    if activity.type == ActivityTypes.message:
        await process_message_activity(turn_context)
    elif activity.type == ActivityTypes.invoke:
        # For adaptive card submits, the response must be returned to the adapter
        result = await process_invoke_activity(turn_context)
        return result
    elif activity.type == ActivityTypes.conversation_update:
        if activity.members_added:
            for member in activity.members_added:
                if member.id != activity.recipient.id:
                    await turn_context.send_activity("Welcome to Intelligent Incident Agent! Send 'Hi' to link your account.")
                    

@router.post("/messages")
async def messages(request: Request) -> Response:
    """
    MS Teams Bot Webhook Endpoint.
    """
    if "application/json" in request.headers.get("content-type", ""):
        body = await request.json()
    else:
        return Response(status_code=415)

    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")
    
    try:
        invoke_response = await bot_adapter.process_activity(activity, auth_header, activity_handler)
        
        if invoke_response:
            return Response(
                content=json.dumps(invoke_response.body) if invoke_response.body else "",
                status_code=invoke_response.status,
                media_type="application/json"
            )
        return Response(status_code=201)
    except Exception as e:
        logger.exception(f"[Bot Webhook] Error: {e}")
        return Response(status_code=500)
