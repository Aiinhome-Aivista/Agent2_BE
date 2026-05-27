"""Knowledge Graph REST API.

  GET    /kg/stats                       — aggregate counts
  GET    /kg/nodes                       — list nodes (filterable)
  GET    /kg/nodes/{id}                  — full detail incl. edges + incidents
  DELETE /kg/nodes/{id}                  — admin only
  POST   /kg/match                       — submit incident-like body, return KG match
  GET    /kg/incidents/{id}/analyses     — Mistral analyses for an incident
  GET    /kg/emails                      — recent email log (admin)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app.api.dependencies import get_current_user, require_admin
from app.core.email_service import list_recent_emails
from app.db import get_db
from app.repositories.knowledge_graph_repository import KnowledgeGraphRepository
from app.schemas import (
    ApiResponse,
    EmailLog,
    KGEdge,
    KGNode,
    KGNodeDetail,
    KGStats,
    MistralAnalysis,
)
from app.services.knowledge_graph_service import KnowledgeGraphService


router = APIRouter(prefix="/kg", tags=["Knowledge Graph"])


# ----------------------------------------------------------------- Stats ----
@router.get("/stats", response_model=ApiResponse[KGStats])
def stats(_user=Depends(get_current_user)) -> ApiResponse[KGStats]:
    data = KnowledgeGraphRepository.stats()
    return ApiResponse(data=KGStats.model_validate(data))


# ----------------------------------------------------------------- Nodes ----
@router.get("/nodes", response_model=ApiResponse[List[KGNode]])
def list_nodes(
    node_type: Optional[str] = Query(None, description="symptom | cause | resolution"),
    category: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    _user=Depends(get_current_user),
) -> ApiResponse[List[KGNode]]:
    rows = KnowledgeGraphRepository.list_nodes(
        node_type=node_type, category=category, limit=limit,
    )
    return ApiResponse(data=[KGNode.model_validate(r) for r in rows])


@router.get("/nodes/{node_id}", response_model=ApiResponse[KGNodeDetail])
def get_node(node_id: str, _user=Depends(get_current_user)) -> ApiResponse[KGNodeDetail]:
    node = KnowledgeGraphRepository.find_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    outgoing = KnowledgeGraphRepository.edges_from(node_id)
    incoming = KnowledgeGraphRepository.edges_to(node_id)
    related = KnowledgeGraphRepository.list_incidents_for_node(node_id)
    detail = KGNodeDetail(
        node=KGNode.model_validate(node),
        outgoing_edges=[KGEdge.model_validate(e) for e in outgoing],
        incoming_edges=[KGEdge.model_validate(e) for e in incoming],
        related_incidents=related,
    )
    return ApiResponse(data=detail)


@router.delete("/nodes/{node_id}", status_code=204, response_class=Response, response_model=None)
def delete_node(node_id: str, _user=Depends(require_admin)):
    KnowledgeGraphRepository.delete_node(node_id)
    return None


# ------------------------------------------------------------ Match incoming -
class MatchRequest(BaseModel):
    subject: str
    description: str
    category: Optional[str] = None


@router.post("/match", response_model=ApiResponse[Optional[Dict[str, Any]]])
def match(req: MatchRequest, _user=Depends(get_current_user)) -> ApiResponse[Optional[Dict[str, Any]]]:
    """Quick KG lookup for an arbitrary incident body — useful for an
    'ask the KG' search bar in the UI without creating an incident first.
    """
    result = KnowledgeGraphService.find_resolution_for_incident({
        "subject": req.subject,
        "description": req.description,
        "category": req.category,
    })
    return ApiResponse(data=result)


# ------------------------------------------------------------ Direct teach --
class TeachStep(BaseModel):
    """A single step in a taught resolution."""
    order: int
    title: str
    detail: Optional[str] = None
    command: Optional[str] = None


class TeachTripletRequest(BaseModel):
    """Admin-supplied symptom→cause→resolution triplet for direct ingestion.

    Bypasses the resolve-an-incident path — used to seed the graph with
    expert knowledge or to bulk-load best-known fixes. Reuses existing
    nodes when an equivalent one already exists, so repeated POSTs of the
    same content strengthen rather than duplicate.
    """
    symptom_label: str
    symptom_description: Optional[str] = None
    cause_label: str
    resolution_label: str
    resolution_steps: Optional[List[TeachStep]] = None
    category: Optional[str] = None
    keywords: Optional[List[str]] = None
    initial_confidence: float = 0.7


@router.post("/teach", response_model=ApiResponse[Dict[str, Any]])
def teach(
    req: TeachTripletRequest,
    user=Depends(require_admin),
) -> ApiResponse[Dict[str, Any]]:
    """Teach the KG a triplet directly. Admin only.

    Each call is idempotent — re-teaching identical content strengthens the
    existing symptom/cause/resolution nodes rather than creating duplicates.
    """
    try:
        result = KnowledgeGraphService.teach_triplet(
            symptom_label=req.symptom_label,
            symptom_description=req.symptom_description,
            cause_label=req.cause_label,
            resolution_label=req.resolution_label,
            resolution_steps=[s.model_dump() for s in (req.resolution_steps or [])],
            category=req.category,
            keywords=req.keywords,
            taught_by_user_id=user.get("id") if isinstance(user, dict) else None,
            initial_confidence=req.initial_confidence,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ApiResponse(data=result)


# ------------------------------------------------------ Mistral analyses ----
@router.get(
    "/incidents/{incident_id}/analyses",
    response_model=ApiResponse[List[MistralAnalysis]],
)
def list_analyses(
    incident_id: str,
    _user=Depends(get_current_user),
) -> ApiResponse[List[MistralAnalysis]]:
    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                """
                SELECT id, incident_id, model, root_cause, suggested_steps,
                       resolution_summary, confidence, tokens_in, tokens_out,
                       latency_ms, source, was_applied, error, created_at
                FROM mistral_analyses
                WHERE incident_id = %s
                ORDER BY created_at DESC
                """,
                (incident_id,),
            )
            rows = cur.fetchall()
    # Parse the suggested_steps JSON
    import json as _json
    for r in rows:
        if isinstance(r.get("suggested_steps"), str):
            try:
                r["suggested_steps"] = _json.loads(r["suggested_steps"])
            except _json.JSONDecodeError:
                r["suggested_steps"] = []
    return ApiResponse(data=[MistralAnalysis.model_validate(r) for r in rows])


# ------------------------------------------------------------- Email logs ---
@router.get("/emails", response_model=ApiResponse[List[EmailLog]])
def list_emails(
    limit: int = Query(50, ge=1, le=500),
    _user=Depends(require_admin),
) -> ApiResponse[List[EmailLog]]:
    rows = list_recent_emails(limit=limit)
    return ApiResponse(data=[EmailLog.model_validate(r) for r in rows])
