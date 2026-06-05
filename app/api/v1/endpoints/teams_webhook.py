from fastapi import APIRouter, Request
from typing import Any, Dict
from app.schemas import ApiResponse
from app.services.ticket_management_service import TicketManagementService

router = APIRouter(prefix="/teams", tags=["Teams Webhook"])

@router.post("/webhook", response_model=ApiResponse)
async def handle_teams_webhook(payload: Dict[str, Any]):
    """
    Mock endpoint to receive MS Teams webhook payloads.
    Since we are mocking the bot framework, we simulate receiving the Adaptive Card action response here.
    """
    # Expected payload structure for this mock:
    # { "incident_id": "INC-123", "action": "accept" | "decline", "user_id": "user-uuid" }
    
    incident_id = payload.get("incident_id")
    action = payload.get("action")
    
    if not incident_id or not action:
        return ApiResponse(message="Invalid payload, missing incident_id or action")
        
    accepted = (action.lower() == "accept")
    
    TicketManagementService.handle_assignment_response(incident_id, accepted)
    
    return ApiResponse(data={"status": "ok"}, message=f"Assignment {'accepted' if accepted else 'declined'} processed")
