"""Aggregate API router for v1.

Replaces backend/app/api/v1/router.py from the base project so that
`/connectors/*` and the connector webhook receiver are wired up.
"""
from fastapi import APIRouter

from app.api.v1.endpoints import auth, connectors, incidents, knowledge_graph, misc, webhooks

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(incidents.router)
api_router.include_router(misc.runbook_router)
api_router.include_router(misc.kb_router)
api_router.include_router(misc.escalation_router)
api_router.include_router(misc.dashboard_router)
api_router.include_router(misc.audit_router)
api_router.include_router(connectors.router)
api_router.include_router(webhooks.router)
api_router.include_router(knowledge_graph.router)
