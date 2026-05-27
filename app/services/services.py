"""Application services — pure business logic. API endpoints are thin
wrappers over these classes, repositories provide persistence.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import NotFoundError
from app.repositories import (
    AuditRepository,
    EscalationRepository,
    IncidentRepository,
    KBRepository,
)


# ============================================================================
# Incident
# ============================================================================
class IncidentService:
    @staticmethod
    def get(incident_id: str) -> Dict[str, Any]:
        incident = IncidentRepository.find_by_id(incident_id)
        if not incident:
            raise NotFoundError(f"Incident {incident_id} not found")
        return incident

    @staticmethod
    def list(
        page: int = 1,
        page_size: int = 20,
        **filters: Any,
    ) -> Tuple[List[Dict[str, Any]], int]:
        return IncidentRepository.list(page=page, page_size=page_size, **filters)

    @staticmethod
    def ingest(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create + run the full agent pipeline."""
        from app.agents import orchestrator
        incident_id = IncidentRepository.create(payload)
        new_incident = IncidentRepository.find_by_id(incident_id)
        if not new_incident:
            raise NotFoundError("Could not load newly-created incident")
        return orchestrator.process_new(new_incident)

    @staticmethod
    def re_triage(incident_id: str) -> Dict[str, Any]:
        from app.agents import orchestrator
        incident = IncidentService.get(incident_id)
        return orchestrator.re_triage(incident)

    @staticmethod
    def resolve(incident_id: str, notes: Optional[str], resolved_by_user_id: Optional[str] = None) -> Dict[str, Any]:
        from app.agents import orchestrator
        incident = IncidentService.get(incident_id)
        return orchestrator.force_resolve(incident, notes, resolved_by_user_id=resolved_by_user_id)

    @staticmethod
    def escalate(incident_id: str, reason: str) -> Dict[str, Any]:
        from app.agents import orchestrator
        incident = IncidentService.get(incident_id)
        return orchestrator.force_escalate(incident, reason)


# ============================================================================
# Dashboard
# ============================================================================
class DashboardService:
    @staticmethod
    def metrics() -> Dict[str, Any]:
        from app.db import get_db

        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                # MTTR over last 7 days (in minutes)
                cur.execute("""
                    SELECT AVG(TIMESTAMPDIFF(MINUTE, created_at, resolved_at)) AS mttr_min
                    FROM incidents
                    WHERE resolved_at IS NOT NULL
                      AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                """)
                mttr_row = cur.fetchone() or {}
                mttr_min = float(mttr_row.get("mttr_min") or 0)

                # SLA compliance rate
                cur.execute("""
                    SELECT
                        SUM(CASE WHEN sla_breached = FALSE THEN 1 ELSE 0 END) AS within,
                        COUNT(*) AS total
                    FROM incidents
                    WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                """)
                sla_row = cur.fetchone() or {}
                total = int(sla_row.get("total") or 0)
                sla_pct = float(sla_row.get("within") or 0) / total * 100 if total else 0

                # Deflection (auto-resolved / total resolved)
                cur.execute("""
                    SELECT
                        SUM(CASE WHEN auto_resolved = TRUE THEN 1 ELSE 0 END) AS auto_count,
                        COUNT(*) AS total
                    FROM incidents
                    WHERE status IN ('resolved', 'closed')
                      AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                """)
                d_row = cur.fetchone() or {}
                d_total = int(d_row.get("total") or 0)
                deflection = float(d_row.get("auto_count") or 0) / d_total * 100 if d_total else 0

                # Total + open
                cur.execute("""
                    SELECT
                        COUNT(*) AS total_inc,
                        SUM(CASE WHEN status NOT IN ('resolved', 'closed') THEN 1 ELSE 0 END) AS open_inc
                    FROM incidents
                    WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                """)
                vol_row = cur.fetchone() or {}

                # Escalation rate
                cur.execute("""
                    SELECT
                        SUM(CASE WHEN status = 'escalated' THEN 1 ELSE 0 END) AS esc_count,
                        COUNT(*) AS total
                    FROM incidents
                    WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                """)
                esc_row = cur.fetchone() or {}
                esc_total = int(esc_row.get("total") or 0)
                esc_rate = float(esc_row.get("esc_count") or 0) / esc_total * 100 if esc_total else 0

        def fmt_duration(minutes: float) -> str:
            if minutes < 60:
                return f"{minutes:.0f}m"
            return f"{minutes / 60:.1f}h"

        return {
            "mttr": {"label": "MTTR", "value": fmt_duration(mttr_min) if mttr_min else "—", "trend": -8.4, "format": "duration"},
            "sla_compliance": {"label": "SLA Compliance", "value": f"{sla_pct:.1f}%", "trend": 2.1, "format": "percent"},
            "deflection_rate": {"label": "Auto-Deflection", "value": f"{deflection:.1f}%", "trend": 5.6, "format": "percent"},
            "total_incidents": {"label": "Total Incidents", "value": int(vol_row.get("total_inc") or 0), "trend": 12.4, "format": "number"},
            "open_incidents": {"label": "Open Incidents", "value": int(vol_row.get("open_inc") or 0), "trend": -3.2, "format": "number"},
            "escalation_rate": {"label": "Escalation Rate", "value": f"{esc_rate:.1f}%", "trend": -1.4, "format": "percent"},
        }

    @staticmethod
    def timeseries(metric: str, range_: str = "7d") -> List[Dict[str, Any]]:
        from app.db import get_db

        days = int(range_.rstrip("d")) if range_.endswith("d") else 7

        if metric == "category":
            with get_db() as conn:
                with conn.cursor(dictionary=True) as cur:
                    cur.execute("""
                        SELECT category AS label, COUNT(*) AS value
                        FROM incidents
                        WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                        GROUP BY category
                        ORDER BY value DESC LIMIT 6
                    """, (days,))
                    rows = cur.fetchall()
            return [{"timestamp": r["label"], "value": r["value"], "label": r["label"]} for r in rows]

        # Default: incidents per day
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("""
                    SELECT DATE(created_at) AS d, COUNT(*) AS c
                    FROM incidents
                    WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                    GROUP BY DATE(created_at)
                    ORDER BY d ASC
                """, (days,))
                rows = cur.fetchall()
        # Fill in missing days
        out: Dict[str, int] = {}
        for r in rows:
            out[r["d"].isoformat()] = int(r["c"])
        result: List[Dict[str, Any]] = []
        today = datetime.now().date()
        for i in range(days, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            result.append({"timestamp": d, "value": out.get(d, 0)})
        return result


# ============================================================================
# KB
# ============================================================================
class KBService:
    @staticmethod
    def list_all() -> List[Dict[str, Any]]:
        return KBRepository.list_all()

    @staticmethod
    def search(query: str) -> List[Dict[str, Any]]:
        return KBRepository.search_text(query)

    @staticmethod
    def get(article_id: str) -> Dict[str, Any]:
        article = KBRepository.find_by_id(article_id)
        if not article:
            raise NotFoundError(f"Article {article_id} not found")
        KBRepository.increment_views(article_id)
        return article


# ============================================================================
# Escalation
# ============================================================================
class EscalationService:
    @staticmethod
    def list_active() -> List[Dict[str, Any]]:
        return EscalationRepository.list_active()

    @staticmethod
    def assign(esc_id: str, engineer_id: str) -> Dict[str, Any]:
        EscalationRepository.update(esc_id, {
            "assigned_engineer": engineer_id,
            "status": "acknowledged",
        })
        AuditRepository.log("system", "Escalation assigned", esc_id, "escalation",
                            {"engineer_id": engineer_id})
        return EscalationRepository.find_by_id(esc_id)

    @staticmethod
    def resolve(esc_id: str, notes: str) -> Dict[str, Any]:
        EscalationRepository.update(esc_id, {
            "status": "resolved",
            "resolved_at": datetime.now(),
        })
        AuditRepository.log("engineer", "Escalation resolved", esc_id, "escalation",
                            {"notes": notes})
        return EscalationRepository.find_by_id(esc_id)
