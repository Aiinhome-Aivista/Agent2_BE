from fastapi import APIRouter, Depends, Query
from typing import Any, Dict, List
from app.db import get_db, fastapi_db_dependency
from app.repositories.incident_repository import IncidentRepository
from mysql.connector.connection import MySQLConnection
import json

router = APIRouter()

@router.get("/journey")
def get_assignment_journey(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    engineer_id: str | None = Query(None),
    db: MySQLConnection = Depends(fastapi_db_dependency)
) -> Dict[str, Any]:
    # Get all active engineers
    with db.cursor(dictionary=True) as cur:
        cur.execute("""
            SELECT u.id, u.full_name, u.email, u.role, COUNT(i.id) AS active_tickets
            FROM users u
            LEFT JOIN incidents i ON u.id IN (i.assigned_to, i.proposed_to) AND i.status NOT IN ('resolved', 'closed')
            WHERE u.role = 'engineer' AND u.is_active = 1
            GROUP BY u.id, u.full_name, u.email, u.role
            ORDER BY active_tickets DESC
        """)
        engineers = cur.fetchall()

    # Filter by engineer if provided
    # The IncidentRepository doesn't natively support filtering by proposed_to/assigned_to in list() easily without modification,
    # but we can filter the list if engineer_id is provided.
    incidents, total = IncidentRepository.list(page=1, page_size=1000, sort_by="created_at", sort_order="desc")
    
    if engineer_id:
        incidents = [i for i in incidents if i.get("assigned_to") == engineer_id or i.get("proposed_to") == engineer_id]
        total = len(incidents)
        # Handle pagination in memory for this specific filter
        start = (page - 1) * page_size
        incidents = incidents[start:start + page_size]
    else:
        # If no filter, just use the repository's native pagination
        incidents, total = IncidentRepository.list(page=page, page_size=page_size, sort_by="created_at", sort_order="desc")
    
    # Process incidents to only include assignment journey steps
    processed_incidents = []
    for inc in incidents:
        journey_steps = []
        for step in inc.get("steps", []):
            action = step.get("action", "")
            if action in ["Assignment Proposed", "Assignment Accepted", "Assignment Declined & Reassigned", "Assignment Declined by All"]:
                journey_steps.append(step)
        
        processed_incidents.append({
            "id": inc["id"],
            "subject": inc["subject"],
            "status": inc["status"],
            "assignment_status": inc["assignment_status"],
            "assigned_to": inc["assigned_to"],
            "proposed_to": inc["proposed_to"],
            "created_at": inc["created_at"],
            "journey": journey_steps
        })

    return {
        "engineers": engineers,
        "incidents": processed_incidents,
        "total": total,
        "page": page,
        "page_size": page_size
    }
