"""Incident endpoints."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_user
from app.schemas import (
    AgentStep,
    ApiResponse,
    Incident,
    IncidentCreate,
    IncidentEscalate,
    IncidentResolve,
    PaginatedResponse,
)
from app.services import IncidentService
from app.core.email_service import list_incident_emails
from app.services.ai_troubleshooting_service import AITroubleshootingService
from app.repositories.incident_repository import IncidentRepository

router = APIRouter(prefix="/incidents", tags=["Incidents"])


@router.get("", response_model=ApiResponse[PaginatedResponse[Incident]])
async def list_incidents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200, alias="pageSize"),
    status: Optional[str] = None,
    priority: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = Query("created_at", alias="sortBy"),
    sort_order: str = Query("desc", alias="sortOrder"),
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[PaginatedResponse[Incident]]:
    items, total = IncidentService.list(
        page=page,
        page_size=page_size,
        status=status,
        priority=priority,
        category=category,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    has_more = page * page_size < total
    return ApiResponse(
        data=PaginatedResponse(
            items=[Incident.model_validate(i) for i in items],
            total=total,
            page=page,
            pageSize=page_size,
            hasMore=has_more,
        )
    )


from app.services.ticket_management_service import TicketManagementService
from pydantic import BaseModel

class AssignmentResponsePayload(BaseModel):
    accept: bool

@router.get("/pending-assignments", response_model=ApiResponse[List[Incident]])
async def pending_assignments(
    user: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[List[Incident]]:
    user_id = user.get("id")
    # Fetch all incidents for this user that are pending approval
    # We can use IncidentRepository directly or IncidentService
    # But list_incidents might not have assignment_status filter. Let's just query db or use list with custom args.
    # Actually, we can just do a custom query in IncidentRepository. 
    # But wait, IncidentRepository.list does not have `assignment_status` parameter. Let's do it manually via a new method or direct DB query, or just fetch all and filter for now (bad practice).
    # Let's add it to IncidentRepository or use get_db here.
    from app.db import get_db
    from app.repositories.incident_repository import _hydrate
    
    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT * FROM incidents WHERE proposed_to = %s AND assignment_status = 'pending_approval'",
                (user_id,)
            )
            rows = cur.fetchall()
            
    items = [_hydrate(r) for r in rows]
    return ApiResponse(data=[Incident.model_validate(i) for i in items])


@router.post("/{incident_id}/assignment", response_model=ApiResponse[Dict[str, Any]])
async def respond_to_assignment(
    incident_id: str,
    payload: AssignmentResponsePayload,
    user: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[Dict[str, Any]]:
    # optionally verify user is the assigned user
    incident = IncidentRepository.find_by_id(incident_id)
    if not incident or str(incident.get("proposed_to")) != str(user.get("id")):
        return ApiResponse(message="Not authorized or not found", data={"success": False})

    TicketManagementService.handle_assignment_response(incident_id, payload.accept)
    return ApiResponse(message="Response recorded", data={"success": True})


@router.get("/{incident_id}", response_model=ApiResponse[Incident])
async def get_incident(
    incident_id: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[Incident]:
    return ApiResponse(data=Incident.model_validate(IncidentService.get(incident_id)))


@router.post("", response_model=ApiResponse[Incident])
async def create_incident(
    payload: IncidentCreate,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[Incident]:
    incident = IncidentService.ingest(payload.model_dump(by_alias=False, exclude_none=True))
    return ApiResponse(data=Incident.model_validate(incident), message="Incident created")


@router.post("/ingest", response_model=ApiResponse[Incident])
async def ingest_incident(
    payload: IncidentCreate,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[Incident]:
    """Same as POST / — alternative endpoint for monitoring webhooks."""
    incident = IncidentService.ingest(payload.model_dump(by_alias=False, exclude_none=True))
    return ApiResponse(data=Incident.model_validate(incident), message="Incident ingested")


@router.post("/{incident_id}/triage", response_model=ApiResponse[Incident])
async def triage_incident(
    incident_id: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[Incident]:
    return ApiResponse(data=Incident.model_validate(IncidentService.re_triage(incident_id)))


@router.post("/{incident_id}/resolve", response_model=ApiResponse[Incident])
async def resolve_incident(
    incident_id: str,
    payload: IncidentResolve,
    user: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[Incident]:
    return ApiResponse(
        data=Incident.model_validate(
            IncidentService.resolve(incident_id, payload.notes, resolved_by_user_id=user.get("id"))
        )
    )


@router.post("/{incident_id}/escalate", response_model=ApiResponse[Incident])
async def escalate_incident(
    incident_id: str,
    payload: IncidentEscalate,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[Incident]:
    return ApiResponse(
        data=Incident.model_validate(IncidentService.escalate(incident_id, payload.reason))
    )


@router.get("/{incident_id}/timeline", response_model=ApiResponse[List[AgentStep]])
async def incident_timeline(
    incident_id: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[List[AgentStep]]:
    incident = IncidentService.get(incident_id)
    return ApiResponse(data=[AgentStep.model_validate(s) for s in incident.get("steps", [])])

@router.get("/{incident_id}/emails")
async def incident_emails(
    incident_id: str,
    _: Dict[str, Any] = Depends(get_current_user),
    ):

    emails = list_incident_emails(incident_id)

    return ApiResponse(data=emails)

@router.post("/{incident_id}/generate-solution", response_model=ApiResponse[Dict[str, Any]])
async def generate_ai_solution(
    incident_id: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[Dict[str, Any]]:
    incident = IncidentService.get(incident_id)
    ai_result = AITroubleshootingService.generate_solution_and_poc(incident)
    
    if ai_result:
        # Optionally add it to the timeline so it's persisted
        IncidentRepository.add_step(incident_id, {
            "agent": "AI Support Bot",
            "action": "Generated Troubleshooting POC (Manual Request)",
            "output": f"Suggested Steps:\n" + "\n".join(ai_result.get('solution_steps', [])) + "\n\nPOC Description: " + ai_result.get('poc_description', '') + "\n\nCode/Snippet:\n```\n" + ai_result.get('poc_code', '') + "\n```",
            "type": "reason"
        })
        return ApiResponse(data=ai_result, message="AI solution and POC generated successfully")
    return ApiResponse(message="Failed to generate AI solution", data={})
