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
