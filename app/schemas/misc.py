"""KB articles, escalations, dashboard metrics, audit entries."""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

# ===== Knowledge Base =====


class KBArticleBase(BaseModel):
    title: str = Field(..., min_length=5)
    content: str = Field(..., min_length=20)
    summary: str
    tags: List[str] = Field(default_factory=list)
    category: str
    is_published: bool = Field(True, alias="isPublished")

    model_config = {"populate_by_name": True}


class KBArticle(KBArticleBase):
    id: str
    author: str
    views: int = 0
    helpful: int = 0
    not_helpful: int = Field(0, alias="notHelpful")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

class ArticleUpload(BaseModel):
    id: int
    name: str
    files: str
    files_type: str = Field(..., alias="filesType")
    content: Optional[Dict[str, Any]] = None
    summary: Optional[Dict[str, Any]] = None
    author: Optional[str] = None
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

    model_config = {"populate_by_name": True}    


# ===== Escalations =====


class EscalationStatus(BaseModel):
    pass  # placeholder


class Escalation(BaseModel):
    id: str
    incident_id: str = Field(..., alias="incidentId")
    reason: str
    diagnostic: str
    attempted_actions: List[str] = Field(default_factory=list, alias="attemptedActions")
    assigned_engineer: Optional[str] = Field(None, alias="assignedEngineer")
    priority: Literal["P1", "P2", "P3", "P4"]
    status: Literal["pending", "acknowledged", "in_progress", "resolved"]
    created_at: datetime = Field(..., alias="createdAt")
    resolved_at: Optional[datetime] = Field(None, alias="resolvedAt")

    model_config = {"populate_by_name": True}


class EscalationAssign(BaseModel):
    engineer_id: str = Field(..., alias="engineerId")
    model_config = {"populate_by_name": True}


class EscalationResolve(BaseModel):
    notes: str


# ===== Dashboard =====


class DashboardMetric(BaseModel):
    label: str
    value: Union[float, int, str]
    trend: float = 0.0
    suffix: Optional[str] = None
    format: Optional[Literal["number", "percent", "duration", "currency"]] = None


class DashboardMetrics(BaseModel):
    mttr: DashboardMetric
    sla_compliance: DashboardMetric = Field(..., alias="slaCompliance")
    deflection_rate: DashboardMetric = Field(..., alias="deflectionRate")
    total_incidents: DashboardMetric = Field(..., alias="totalIncidents")
    open_incidents: DashboardMetric = Field(..., alias="openIncidents")
    escalation_rate: DashboardMetric = Field(..., alias="escalationRate")

    model_config = {"populate_by_name": True}


class TimeseriesPoint(BaseModel):
    timestamp: str
    value: float
    label: Optional[str] = None


# ===== Audit =====


class AuditLogEntry(BaseModel):
    id: str
    actor: str
    action: str
    target: str
    target_type: Literal["incident", "runbook", "kb", "escalation", "user"] = Field(
        ..., alias="targetType"
    )
    metadata: Optional[Dict[str, Any]] = None
    timestamp: datetime

    model_config = {"populate_by_name": True}
