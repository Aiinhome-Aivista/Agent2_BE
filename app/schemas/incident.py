"""Incident schemas — must mirror the frontend `common.types.ts`."""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field

IncidentStatus = Literal["new", "analyzing", "remediating", "resolved", "escalated", "closed"]
Priority = Literal["P1", "P2", "P3", "P4"]
Severity = Literal["critical", "high", "medium", "low"]
Source = Literal[
    "itsm",
    "jira",
    "servicenow",
    "salesforce",
    "zoho",
    "adf",
    "hubspot",
    "monitoring",
    "user_chat",
    "email",
    "webhook",
]
StepType = Literal["observe", "reason", "plan", "act", "evaluate"]


class AgentStep(BaseModel):
    id: str
    agent: str
    action: str
    output: str
    timestamp: datetime
    type: StepType
    metadata: Optional[Dict[str, Any]] = None

class EmailLog(BaseModel):
    id: str
    incident_id: Optional[str] = None
    to_address: str
    subject: str
    status: str
    template: Optional[str] = None
    error: Optional[str] = None
    sent_at: Optional[datetime] = None
    created_at: Optional[datetime] = None    


class IncidentBase(BaseModel):
    subject: str
    description: str
    caller: str
    caller_email: Optional[EmailStr] = Field(None, alias="callerEmail")
    source: Source = "user_chat"
    category: str = "Uncategorised"
    subcategory: Optional[str] = None
    priority: Priority = "P3"
    severity: Severity = "medium"
    tags: List[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class IncidentCreate(BaseModel):
    """Body accepted by POST /incidents and POST /incidents/ingest."""

    subject: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10, max_length=5000)
    caller: str = Field(..., min_length=2, max_length=100)
    caller_email: Optional[EmailStr] = Field(None, alias="callerEmail")
    source: Source = "user_chat"
    category: Optional[str] = None
    priority: Optional[Priority] = None

    model_config = {"populate_by_name": True}


class IncidentEscalate(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class IncidentResolve(BaseModel):
    notes: Optional[str] = None


class Incident(IncidentBase):
    id: str
    status: IncidentStatus
    assigned_to: Optional[str] = Field(None, alias="assignedTo")
    sla_deadline: Optional[datetime] = Field(None, alias="slaDeadline")
    sla_breached: bool = Field(False, alias="slaBreached")
    auto_resolved: bool = Field(False, alias="autoResolved")
    confidence: float = 0.0
    steps: List[AgentStep] = Field(default_factory=list)
    emails: List[EmailLog] = Field(default_factory=list)
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    resolved_at: Optional[datetime] = Field(None, alias="resolvedAt")
