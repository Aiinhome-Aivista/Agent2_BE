from fastapi import APIRouter, Depends
from typing import Any, Dict, List
from app.schemas import ApiResponse
from app.api.dependencies import get_current_user
from app.services.ticket_management_service import TicketManagementService
from app.db.database import get_db

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("/team-lead", response_model=ApiResponse[Dict[str, Any]])
async def get_team_lead_dashboard(
    user: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[Dict[str, Any]]:
    # In a real app, verify user is a Team Lead
    
    engineers = TicketManagementService.get_engineer_bandwidths()
    
    # Get escalated tickets (due to declined assignments)
    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute("""
                SELECT id, subject, priority, created_at
                FROM incidents
                WHERE assignment_status = 'escalated_to_lead'
                  AND status NOT IN ('resolved', 'closed')
                ORDER BY created_at DESC
            """)
            escalated_tickets = cur.fetchall()

    return ApiResponse(data={
        "engineers": engineers,
        "escalated_tickets": escalated_tickets
    })
