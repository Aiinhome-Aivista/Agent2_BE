from typing import Any, Dict, List, Optional
from app.db import get_db
from app.repositories.incident_repository import IncidentRepository
from app.services.teams_service import TeamsService
from app.core.logger import logger

class TicketManagementService:
    @staticmethod
    def get_engineer_bandwidths() -> List[Dict[str, Any]]:
        """
        Calculates bandwidth for engineers.
        Bandwidth definition: lower open ticket count = higher bandwidth.
        """
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT u.id, u.full_name, u.email, u.teams_user_id, u.job_title,
                           COUNT(i.id) AS active_tickets,
                           u.teams_conversation_reference IS NOT NULL AS has_teams
                    FROM users u
                    INNER JOIN team_config tc ON u.id = tc.user_id
                    LEFT JOIN incidents i ON u.id IN (i.assigned_to, i.proposed_to) AND i.status NOT IN ('resolved', 'closed')
                    WHERE u.is_active = 1
                    GROUP BY u.id, u.full_name, u.email, u.teams_user_id, u.job_title, has_teams
                    ORDER BY active_tickets ASC, has_teams DESC, u.created_at ASC
                    """
                )
                return cur.fetchall()

    @staticmethod
    def assign_ticket_based_on_bandwidth(incident_id: str) -> None:
        """
        Finds the engineer with the matching role and lowest active ticket count 
        and triggers an MS Teams assignment request.
        """
        incident = IncidentRepository.find_by_id(incident_id)
        if not incident:
            logger.error(f"[TicketManagementService] Incident {incident_id} not found.")
            return

        engineers = TicketManagementService.get_engineer_bandwidths()
        if not engineers:
            logger.warning(f"[TicketManagementService] No active engineers available to assign {incident_id}.")
            return

        target_role = TicketManagementService._determine_engineer_role_via_llm(incident)
        
        matching_engineers = [e for e in engineers if str(e.get("job_title", "")).lower() == target_role.lower()]
        
        if not matching_engineers:
            logger.warning(f"[TicketManagementService] No engineer found with role '{target_role}' for incident {incident_id}.")
            IncidentRepository.update(incident_id, {
                "assignment_status": "escalated_to_lead",
                "status": "escalated"
            })
            IncidentRepository.add_step(incident_id, {
                "agent": "System",
                "action": "Escalated due to missing skill",
                "output": f"Ticket requires '{target_role}' but no such engineer is available. Escalated to Team Lead.",
                "type": "act",
                "metadata": {"required_role": target_role}
            })
            return

        # Sort matching engineers by active tickets (ascending)
        matching_engineers.sort(key=lambda x: x["active_tickets"])
        best_engineer = matching_engineers[0]

        logger.info(f"[TicketManagementService] Proposing incident {incident_id} to {best_engineer['full_name']} (Role: {target_role}, Tickets: {best_engineer['active_tickets']})")
        
        # Update incident status to pending assignment
        IncidentRepository.update(incident_id, {
            "proposed_to": best_engineer["id"],
            "assignment_status": "pending_approval",
            "status": "assigned"
        })

        # Add timeline step
        IncidentRepository.add_step(incident_id, {
            "agent": "System",
            "action": "Assignment Proposed",
            "output": f"Ticket matched to role '{target_role}' and proposed to {best_engineer['full_name']}.",
            "type": "act",
            "metadata": {"proposed_to": best_engineer["id"], "matched_role": target_role}
        })

        # Ask via MS Teams Bot
        TeamsService.send_assignment_approval(incident, best_engineer)

    @staticmethod
    def _determine_engineer_role_via_llm(incident_row: Dict[str, Any]) -> str:
        """Use LLM to determine which engineer role should handle this ticket."""
        from app.core.mistral_client import get_mistral_client, is_configured
        
        if not is_configured():
            return ""
            
        client = get_mistral_client()
        if not client:
            return ""
            
        # Determine target role based on incident details
        system_prompt = (
            "You are a technical dispatcher. Based on the incident subject and description, "
            "determine which engineer role is most suitable to handle it. "
            "Respond ONLY with the exact role name from this list: 'ML Engineer', 'Cloud Engineer', 'Data Engineer', 'Software Engineer'. "
            "If none fit perfectly, pick the closest match. Output only the role name as plain text."
        )
        user_prompt = f"Subject: {incident_row.get('subject')}\nDescription: {incident_row.get('description')}"
        
        try:
            resp = client.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], temperature=0.1, max_tokens=10)
            
            content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
            return content.strip("'\"")
        except Exception as e:
            logger.warning(f"[TicketManagementService] LLM categorization failed: {e}")
            return ""

    @staticmethod
    def assign_unassigned_tickets_equally() -> None:
        """
        Finds all unassigned, new incidents and distributes them to engineers
        based on their role (via LLM) and lowest active ticket count.
        """
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT id, subject, description FROM incidents
                    WHERE assigned_to IS NULL AND assignment_status = 'unassigned' AND status = 'new'
                    """
                )
                unassigned_incidents = cur.fetchall()

        if not unassigned_incidents:
            return

        engineers = TicketManagementService.get_engineer_bandwidths()

        if not engineers:
            logger.warning("[TicketManagementService] No active engineers available for auto-assignment.")
            return

        logger.info(f"[TicketManagementService] Found {len(unassigned_incidents)} unassigned incidents. Distributing among {len(engineers)} engineers.")

        for incident_row in unassigned_incidents:
            incident_id = incident_row["id"]
            
            target_role = TicketManagementService._determine_engineer_role_via_llm(incident_row)
            
            # Filter engineers by role
            matching_engineers = [e for e in engineers if str(e.get("job_title", "")).lower() == target_role.lower()]
            
            if not matching_engineers:
                logger.warning(f"[TicketManagementService] No engineer found with role '{target_role}' for incident {incident_id}. Escalating to Team Lead.")
                IncidentRepository.update(incident_id, {
                    "assignment_status": "escalated_to_lead",
                    "status": "escalated"
                })
                IncidentRepository.add_step(incident_id, {
                    "agent": "System",
                    "action": "Escalated due to missing skill",
                    "output": f"Ticket requires '{target_role}' but no such engineer is available. Escalated to Team Lead.",
                    "type": "act",
                    "metadata": {"required_role": target_role}
                })
                continue
            
            # Sort matching engineers by active_tickets (ascending) to get the one with least tickets
            matching_engineers.sort(key=lambda x: x["active_tickets"])
            assigned_engineer = matching_engineers[0]
            
            IncidentRepository.update(incident_id, {
                "proposed_to": assigned_engineer["id"],
                "assignment_status": "pending_approval",
                "status": "assigned"
            })
            
            IncidentRepository.add_step(incident_id, {
                "agent": "System",
                "action": "Assignment Proposed",
                "output": f"Ticket matched to role '{target_role}' and proposed to {assigned_engineer['full_name']}.",
                "type": "act",
                "metadata": {"proposed_to": assigned_engineer["id"], "matched_role": target_role}
            })
            
            # Increment the active_tickets in memory so the next ticket goes to someone else if balanced
            assigned_engineer["active_tickets"] += 1
            
            logger.info(f"[TicketManagementService] Auto-assigned {incident_id} to {assigned_engineer['full_name']} (Role: {target_role}, Pending Approval)")

    @staticmethod
    def handle_assignment_response(incident_id: str, accepted: bool) -> None:
        """
        Callback handler when an engineer replies 'Yes' or 'No' to the assignment request (via UI or Teams).
        """
        incident = IncidentRepository.find_by_id(incident_id)
        if not incident:
            return

        if accepted:
            logger.info(f"[TicketManagementService] Assignment accepted for {incident_id}.")
            IncidentRepository.update(incident_id, {
                "assignment_status": "assigned",
                "assigned_to": incident.get("proposed_to"),
                "status": "accepted"
            })
            # Add timeline step
            IncidentRepository.add_step(incident_id, {
                "agent": "System",
                "action": "Assignment Accepted",
                "output": f"Engineer accepted the assignment.",
                "type": "act",
                "metadata": {"accepted_by": incident.get("proposed_to")}
            })
        else:
            declining_engineer_id = incident.get("proposed_to")
            logger.info(f"[TicketManagementService] Assignment declined for {incident_id}. Reassigning.")
            
            import json
            declined_by = incident.get("declined_by")
            if isinstance(declined_by, str):
                try:
                    declined_by = json.loads(declined_by)
                except Exception:
                    declined_by = []
            elif not declined_by:
                declined_by = []
                
            if declining_engineer_id and declining_engineer_id not in declined_by:
                declined_by.append(declining_engineer_id)
                
            IncidentRepository.update(incident_id, {
                "declined_by": json.dumps(declined_by)
            })
            
            engineers = TicketManagementService.get_engineer_bandwidths()
            # Filter out all declining engineers
            available_engineers = [e for e in engineers if e["id"] not in declined_by]
            
            if available_engineers:
                next_engineer = available_engineers[0]
                IncidentRepository.update(incident_id, {
                    "proposed_to": next_engineer["id"],
                    "assignment_status": "pending_approval"
                })
                IncidentRepository.add_step(incident_id, {
                    "agent": "System",
                    "action": "Assignment Declined & Reassigned",
                    "output": f"Engineer declined assignment. Reassigned to {next_engineer['full_name']}.",
                    "type": "act",
                    "metadata": {
                        "declined_by": declining_engineer_id,
                        "proposed_to": next_engineer["id"]
                    }
                })
                logger.info(f"[TicketManagementService] Reassigned {incident_id} to {next_engineer['full_name']}")
            else:
                IncidentRepository.update(incident_id, {
                    "assignment_status": "escalated_to_lead",
                    "proposed_to": None,
                    "status": "escalated"
                })
                IncidentRepository.add_step(incident_id, {
                    "agent": "System",
                    "action": "Assignment Declined by All",
                    "output": f"All available engineers declined assignment. Ticket escalated to Team Lead.",
                    "type": "act",
                    "metadata": {"declined_by": declining_engineer_id}
                })
                logger.warning(f"[TicketManagementService] {incident_id} declined by all engineers. Escalated to lead.")
