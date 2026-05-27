"""Runbook schemas."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RunbookStep(BaseModel):
    order: int
    title: str
    command: Optional[str] = None
    expected_output: Optional[str] = Field(None, alias="expectedOutput")
    rollback: Optional[str] = None

    model_config = {"populate_by_name": True}


class RunbookBase(BaseModel):
    name: str
    description: str
    category: str
    steps: List[RunbookStep] = Field(default_factory=list)
    triggers: List[str] = Field(default_factory=list)
    is_active: bool = Field(True, alias="isActive")

    model_config = {"populate_by_name": True}


class Runbook(RunbookBase):
    id: str
    last_updated: datetime = Field(..., alias="lastUpdated")
    success_rate: float = Field(..., alias="successRate")
    execution_count: int = Field(0, alias="executionCount")
    average_duration_seconds: float = Field(0.0, alias="averageDurationSeconds")
    created_by: str = Field(..., alias="createdBy")


class RunbookExecuteRequest(BaseModel):
    incident_id: str = Field(..., alias="incidentId")

    model_config = {"populate_by_name": True}



class RunbookExecuteResult(BaseModel):
    success: bool
    output: str
    duration_seconds: float = Field(0.0, alias="durationSeconds")

    model_config = {"populate_by_name": True}


class RunbookUpload(BaseModel):
    id: int
    name: str
    files: str
    files_type: str = Field(..., alias="filesType")
    content: Optional[Dict[str, Any]] = None
    summary: Optional[Dict[str, Any]] = None
    execution_steps: Optional[List[Dict[str, Any]]] = None
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

    model_config = {"populate_by_name": True}
