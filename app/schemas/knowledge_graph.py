"""Knowledge graph schemas for the REST API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


NodeType = Literal["symptom", "cause", "resolution"]
EdgeType = Literal["caused_by", "resolved_by", "similar_to", "related_to"]
LinkRole = Literal["symptom_match", "cause_match", "resolution_applied", "taught"]


class KGNode(BaseModel):
    id: str
    node_type: NodeType = Field(..., alias="nodeType")
    label: str
    description: Optional[str] = None
    category: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None
    keywords: List[str] = Field(default_factory=list)
    occurrence_count: int = Field(0, alias="occurrenceCount")
    success_count: int = Field(0, alias="successCount")
    failure_count: int = Field(0, alias="failureCount")
    confidence: float = 0.5
    source_incident_id: Optional[str] = Field(None, alias="sourceIncidentId")
    created_by: Optional[str] = Field(None, alias="createdBy")
    is_active: bool = Field(True, alias="isActive")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

    model_config = {"populate_by_name": True, "from_attributes": True}


class KGEdge(BaseModel):
    id: str
    src_node_id: str = Field(..., alias="srcNodeId")
    dst_node_id: str = Field(..., alias="dstNodeId")
    edge_type: EdgeType = Field(..., alias="edgeType")
    weight: float
    evidence_count: int = Field(..., alias="evidenceCount")
    created_at: datetime = Field(..., alias="createdAt")
    # Joined columns (optional, present in some queries)
    dst_label: Optional[str] = Field(None, alias="dstLabel")
    dst_type: Optional[str] = Field(None, alias="dstType")
    src_label: Optional[str] = Field(None, alias="srcLabel")
    src_type: Optional[str] = Field(None, alias="srcType")

    model_config = {"populate_by_name": True, "from_attributes": True}


class KGStats(BaseModel):
    symptoms: int
    causes: int
    resolutions: int
    total_nodes: int = Field(..., alias="totalNodes")
    total_edges: int = Field(..., alias="totalEdges")
    successful_applications: int = Field(..., alias="successfulApplications")

    model_config = {"populate_by_name": True}


class KGIncidentLink(BaseModel):
    id: str
    incident_id: str = Field(..., alias="incidentId")
    node_id: str = Field(..., alias="nodeId")
    role: LinkRole
    was_successful: Optional[bool] = Field(None, alias="wasSuccessful")
    notes: Optional[str] = None
    label: Optional[str] = None
    node_type: Optional[str] = Field(None, alias="nodeType")
    confidence: Optional[float] = None
    created_at: datetime = Field(..., alias="createdAt")

    model_config = {"populate_by_name": True, "from_attributes": True}


class KGNodeDetail(BaseModel):
    """A node plus its outgoing edges + incidents that contributed to it."""

    node: KGNode
    outgoing_edges: List[KGEdge] = Field(default_factory=list, alias="outgoingEdges")
    incoming_edges: List[KGEdge] = Field(default_factory=list, alias="incomingEdges")
    related_incidents: List[Dict[str, Any]] = Field(default_factory=list, alias="relatedIncidents")

    model_config = {"populate_by_name": True}


class MistralAnalysis(BaseModel):
    id: str
    incident_id: str = Field(..., alias="incidentId")
    model: str
    root_cause: Optional[str] = Field(None, alias="rootCause")
    suggested_steps: List[str] = Field(default_factory=list, alias="suggestedSteps")
    resolution_summary: Optional[str] = Field(None, alias="resolutionSummary")
    confidence: float
    tokens_in: int = Field(0, alias="tokensIn")
    tokens_out: int = Field(0, alias="tokensOut")
    latency_ms: int = Field(0, alias="latencyMs")
    source: str
    was_applied: bool = Field(False, alias="wasApplied")
    error: Optional[str] = None
    created_at: datetime = Field(..., alias="createdAt")

    model_config = {"populate_by_name": True, "from_attributes": True}


class EmailLog(BaseModel):
    id: str
    to_address: str = Field(..., alias="toAddress")
    subject: str
    template: Optional[str] = None
    related_id: Optional[str] = Field(None, alias="relatedId")
    status: str
    error: Optional[str] = None
    retry_count: int = Field(0, alias="retryCount")
    sent_at: Optional[datetime] = Field(None, alias="sentAt")
    created_at: datetime = Field(..., alias="createdAt")

    model_config = {"populate_by_name": True, "from_attributes": True}
