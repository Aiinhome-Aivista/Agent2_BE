from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query, File, UploadFile, Form

from app.api.dependencies import get_current_user, require_engineer
from app.repositories.repos import (
    AuditRepository,
    EscalationRepository,
    RunbookRepository,
    RunbookUploadRepository,
    ArticleUploadRepository,
)
from app.schemas import (
    ApiResponse,
    AuditLogEntry,
    DashboardMetrics,
    Escalation,
    EscalationAssign,
    EscalationResolve,
    KBArticle,
    ArticleUpload,
    Runbook,
    RunbookExecuteRequest,
    RunbookExecuteResult,
    RunbookUpload,
    TimeseriesPoint,
)
from app.services import DashboardService, EscalationService, KBService

# ============================================================================
# Runbooks
# ============================================================================
runbook_router = APIRouter(prefix="/runbooks", tags=["Runbooks"])


@runbook_router.post("/upload", response_model=ApiResponse[List[RunbookUpload]])
async def upload_runbook(
    files: List[UploadFile] = File(...),
    author: str = Form(None),
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[List[RunbookUpload]]:
    import json as _json
    import base64 as _base64
    import os as _os
    import time as _time
    
    # Ensure upload directory exists
    upload_dir = "uploads"
    if not _os.path.exists(upload_dir):
        _os.makedirs(upload_dir)
        
    results = []
    
    for file in files:
        content_raw = await file.read()
        
        # Try to decode as text, fallback to base64 for binary
        is_binary = False
        try:
            content_str = content_raw.decode("utf-8")
            try:
                content_json = _json.loads(content_str)
            except _json.JSONDecodeError:
                content_json = {"text": content_str}
        except UnicodeDecodeError:
            is_binary = True

            # Don't store full binary in DB
            content_json = {
                "uploaded": True
            }

        # Generate unique filename
        timestamp = int(_time.time())
        unique_filename = f"{timestamp}_{file.filename}"
        file_path = _os.path.join(upload_dir, unique_filename)
        
        # Save file to disk
        with open(file_path, "wb") as f:
            f.write(content_raw)
        
        file_ext = _os.path.splitext(file.filename)[1].lower().replace(".", "") or "unknown"

        upload_data = {
            "name": file.filename,
            "files": file_path,
            "files_type": file_ext,
            "content": content_json,
            "summary": {
                "size": len(content_raw),
                "is_binary": is_binary
            },
            "author": author or "system",
        }
        
        upload_id = RunbookUploadRepository.create(upload_data)

        # =========================
        # AI Processing
        # =========================
        from app.services.runbook_parser import extract_text
        from app.services.runbook_ai_service import generate_runbook_summary
        from app.services.chroma_service import store_runbook_embedding

        try:
            # Extract text from uploaded file
            extracted_text = extract_text(file_path)
            print("EXTRACTED TEXT:")
            print(extracted_text[:2000])

            # Generate AI summary + steps
            ai_result = generate_runbook_summary(extracted_text)
            print("AI RESULT:")
            print(ai_result)

            # Update DB with AI data
            RunbookUploadRepository.update_ai_fields(
                upload_id,
                {
                    "summary": {
                        "name": ai_result.get("name"),
                        "category": ai_result.get("category"),
                        "summary": ai_result.get("summary"),
                        "description": ai_result.get("description"),
                    },
                    "execution_steps": ai_result.get("steps", []),
                    "content": {
                        "text": extracted_text[:50000]
                    }
                }
            )

            # Store embedding in ChromaDB
            store_runbook_embedding(
                runbook_id=upload_id,
                text=extracted_text,
                metadata={
                    "name": ai_result.get("name"),
                    "category": ai_result.get("category"),
                    "file_name": file.filename
                }
            )

        except Exception as e:
            print("RUNBOOK AI PROCESSING ERROR:", str(e))

        record = RunbookUploadRepository.find_by_id(upload_id)
        if record:
            results.append(RunbookUpload.model_validate(record))
            
    return ApiResponse(data=results)


@runbook_router.get("", response_model=ApiResponse[List[RunbookUpload]])
async def list_runbooks(
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[List[RunbookUpload]]:

    rows = RunbookUploadRepository.list_all()

    return ApiResponse(
        data=[RunbookUpload.model_validate(r) for r in rows]
    )


@runbook_router.get("/{runbook_id}", response_model=ApiResponse[RunbookUpload])
async def get_runbook(runbook_id: str, _: Dict[str, Any] = Depends(get_current_user)) -> ApiResponse[RunbookUpload]:
    rb = RunbookRepository.find_by_id(runbook_id)
    if not rb:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(f"Runbook {runbook_id} not found")
    return ApiResponse(data=Runbook.model_validate(rb))

@runbook_router.delete("/{runbook_id}", response_model=ApiResponse[Dict[str, Any]])
async def delete_runbook(
    runbook_id: int,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[Dict[str, Any]]:

    deleted = RunbookUploadRepository.delete(runbook_id)

    if not deleted:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(f"Runbook {runbook_id} not found")

    return ApiResponse(
        data={
            "id": runbook_id,
            "deleted": True,
        },
        message="Runbook deleted successfully",
    )

@runbook_router.post("/{runbook_id}/execute", response_model=ApiResponse[RunbookExecuteResult])
async def execute_runbook(
    runbook_id: str,
    payload: RunbookExecuteRequest,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[RunbookExecuteResult]:
    """Synchronously execute a runbook — used for manual operator runs."""
    from app.agents.resolution_agent import ResolutionAgent
    from app.services import IncidentService

    rb = RunbookRepository.find_by_id(runbook_id)
    if not rb:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(f"Runbook {runbook_id} not found")

    incident = IncidentService.get(payload.incident_id)
    agent = ResolutionAgent()
    success, output, duration = agent._execute(rb, incident)  # noqa: SLF001 — internal helper
    RunbookRepository.record_execution(runbook_id, success, duration)
    return ApiResponse(
        data=RunbookExecuteResult(success=success, output=output, durationSeconds=duration)
    )


@runbook_router.get("/{runbook_id}/executions", response_model=ApiResponse[List[Dict[str, Any]]])
async def list_runbook_executions(
    runbook_id: str,
    limit: int = Query(20, ge=1, le=100),
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[List[Dict[str, Any]]]:
    """Return the most recent executions of a runbook, with the full step trace.

    Joins `incidents` ← `incident_steps` where the step metadata references
    this runbook id. Each returned execution has the incident summary and
    the ordered list of steps that ran.
    """
    import json as _json
    from app.db import get_db

    rb = RunbookRepository.find_by_id(runbook_id)
    if not rb:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(f"Runbook {runbook_id} not found")

    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            # Step 1: find the recent incidents this runbook was applied to.
            # We use JSON_EXTRACT to filter by runbook_id in step metadata.
            cur.execute(
                """
                SELECT DISTINCT i.id, i.subject, i.status, i.priority,
                       i.category, i.auto_resolved, i.created_at,
                       i.resolved_at, s_act.timestamp AS executed_at,
                       JSON_EXTRACT(s_act.metadata, '$.success') AS act_success,
                       JSON_EXTRACT(s_act.metadata, '$.duration_s') AS duration_s
                FROM incidents i
                JOIN incident_steps s_act ON s_act.incident_id = i.id
                WHERE s_act.type = 'act'
                  AND JSON_UNQUOTE(JSON_EXTRACT(s_act.metadata, '$.runbook_id')) = %s
                ORDER BY s_act.timestamp DESC
                LIMIT %s
                """,
                (runbook_id, limit),
            )
            executions = cur.fetchall()

            # Step 2: for each incident, fetch its full step trace.
            results: List[Dict[str, Any]] = []
            for ex in executions:
                cur.execute(
                    """
                    SELECT id, agent, action, output, type, metadata, timestamp
                    FROM incident_steps
                    WHERE incident_id = %s
                    ORDER BY timestamp ASC
                    """,
                    (ex["id"],),
                )
                steps_raw = cur.fetchall()
                steps: List[Dict[str, Any]] = []
                for s in steps_raw:
                    meta = s.get("metadata")
                    if isinstance(meta, str):
                        try:
                            meta = _json.loads(meta)
                        except _json.JSONDecodeError:
                            meta = {}
                    steps.append({**s, "metadata": meta})

                results.append({
                    "incident_id": ex["id"],
                    "subject": ex["subject"],
                    "status": ex["status"],
                    "priority": ex["priority"],
                    "category": ex["category"],
                    "executed_at": ex["executed_at"],
                    "duration_s": float(ex.get("duration_s") or 0),
                    "success": bool(ex.get("act_success")),
                    "auto_resolved": bool(ex.get("auto_resolved")),
                    "resolved_at": ex.get("resolved_at"),
                    "steps": steps,
                })
    return ApiResponse(data=results)


# ============================================================================
# Knowledge base
# ============================================================================
kb_router = APIRouter(prefix="/kb", tags=["Knowledge Base"])


@kb_router.get("", response_model=ApiResponse[List[ArticleUpload]])
async def list_articles(
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[List[ArticleUpload]]:

    rows = ArticleUploadRepository.list_all()

    return ApiResponse(
        data=[ArticleUpload.model_validate(r) for r in rows]
    )


@kb_router.get("/search", response_model=ApiResponse[List[KBArticle]])
async def search_articles(
    q: str = Query(..., min_length=1),
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[List[KBArticle]]:
    rows = KBService.search(q)
    return ApiResponse(data=[KBArticle.model_validate(r) for r in rows])


@kb_router.get("/{article_id}", response_model=ApiResponse[KBArticle])
async def get_article(article_id: str, _: Dict[str, Any] = Depends(get_current_user)) -> ApiResponse[KBArticle]:
    return ApiResponse(data=KBArticle.model_validate(KBService.get(article_id)))


@kb_router.post("/upload", response_model=ApiResponse[List[ArticleUpload]])
async def upload_article(
    files: List[UploadFile] = File(...),
    author: str = Form(None),
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[List[ArticleUpload]]:
    import json as _json
    import base64 as _base64
    import os as _os
    import time as _time
    
    # Ensure upload directory exists
    upload_dir = "uploads"
    if not _os.path.exists(upload_dir):
        _os.makedirs(upload_dir)
        
    results = []
    
    for file in files:
        content_raw = await file.read()
        
        # Try to decode as text, fallback to base64 for binary
        is_binary = False
        try:
            content_str = content_raw.decode("utf-8")
            try:
                content_json = _json.loads(content_str)
            except _json.JSONDecodeError:
                content_json = {"text": content_str}
        except UnicodeDecodeError:
            is_binary = True
            content_json = {
                "binary_base64": _base64.b64encode(content_raw).decode("ascii"),
                "encoding": "base64"
            }

        # Generate unique filename
        timestamp = int(_time.time())
        unique_filename = f"{timestamp}_{file.filename}"
        file_path = _os.path.join(upload_dir, unique_filename)
        
        # Save file to disk
        with open(file_path, "wb") as f:
            f.write(content_raw)
        
        file_ext = _os.path.splitext(file.filename)[1].lower().replace(".", "") or "unknown"

        upload_data = {
            "name": file.filename,
            "files": file_path,
            "files_type": file_ext,
            "content": content_json,
            "summary": {
                "size": len(content_raw),
                "is_binary": is_binary
            },
            "author": author or "system",
        }
        
        upload_id = ArticleUploadRepository.create(upload_data)

        # =========================
        # AI Processing
        # =========================
        from app.services.runbook_parser import extract_text
        from app.services.runbook_ai_service import generate_runbook_summary
        from app.services.chroma_service import store_runbook_embedding

        try:
            # Extract text
            extracted_text = extract_text(file_path)

            print("ARTICLE EXTRACTED TEXT:")
            print(extracted_text[:2000])

            # Generate AI summary
            ai_result = generate_runbook_summary(extracted_text)

            print("ARTICLE AI RESULT:")
            print(ai_result)

            # Update DB with AI fields
            ArticleUploadRepository.update_ai_fields(
                upload_id,
                {
                    "summary": {
                        "name": ai_result.get("name"),
                        "category": ai_result.get("category"),
                        "summary": ai_result.get("summary"),
                        "description": ai_result.get("description"),
                    },
                    "content": {
                        "text": extracted_text[:50000]
                    },
                    "author": author or "system",
                }
            )

            # Store embedding
            store_runbook_embedding(
                runbook_id=upload_id,
                text=extracted_text,
                metadata={
                    "name": ai_result.get("name"),
                    "category": ai_result.get("category"),
                    "file_name": file.filename
                }
            )

        except Exception as e:
            print("ARTICLE AI PROCESSING ERROR:", str(e))

        record = ArticleUploadRepository.find_by_id(upload_id)
        if record:
            results.append(ArticleUpload.model_validate(record))
            
    return ApiResponse(data=results)


@kb_router.delete("/{article_id}", response_model=ApiResponse[Dict[str, Any]])
async def delete_article(
    article_id: int,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[Dict[str, Any]]:

    deleted = ArticleUploadRepository.delete(article_id)

    if not deleted:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(f"Article {article_id} not found")

    return ApiResponse(
        data={
            "deleted": True,
            "article_id": article_id
        }
    )

# ============================================================================
# Escalations
# ============================================================================
escalation_router = APIRouter(prefix="/escalations", tags=["Escalations"])


@escalation_router.get("", response_model=ApiResponse[List[Escalation]])
async def list_escalations(_: Dict[str, Any] = Depends(get_current_user)) -> ApiResponse[List[Escalation]]:
    rows = EscalationService.list_active()
    return ApiResponse(data=[Escalation.model_validate(r) for r in rows])


@escalation_router.get("/{esc_id}", response_model=ApiResponse[Escalation])
async def get_escalation(esc_id: str, _: Dict[str, Any] = Depends(get_current_user)) -> ApiResponse[Escalation]:
    row = EscalationRepository.find_by_id(esc_id)
    if not row:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(f"Escalation {esc_id} not found")
    return ApiResponse(data=Escalation.model_validate(row))


@escalation_router.post("/{esc_id}/assign", response_model=ApiResponse[Escalation])
async def assign_escalation(
    esc_id: str,
    payload: EscalationAssign,
    _: Dict[str, Any] = Depends(require_engineer),
) -> ApiResponse[Escalation]:
    return ApiResponse(data=Escalation.model_validate(EscalationService.assign(esc_id, payload.engineer_id)))


@escalation_router.post("/{esc_id}/resolve", response_model=ApiResponse[Escalation])
async def resolve_escalation(
    esc_id: str,
    payload: EscalationResolve,
    _: Dict[str, Any] = Depends(require_engineer),
) -> ApiResponse[Escalation]:
    return ApiResponse(data=Escalation.model_validate(EscalationService.resolve(esc_id, payload.notes)))


# ============================================================================
# Dashboard
# ============================================================================
dashboard_router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@dashboard_router.get("/metrics", response_model=ApiResponse[DashboardMetrics])
async def metrics(_: Dict[str, Any] = Depends(get_current_user)) -> ApiResponse[DashboardMetrics]:
    return ApiResponse(data=DashboardMetrics.model_validate(DashboardService.metrics()))


@dashboard_router.get("/timeseries", response_model=ApiResponse[List[TimeseriesPoint]])
async def timeseries(
    metric: str = Query("incidents"),
    range: str = Query("7d"),
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[List[TimeseriesPoint]]:
    return ApiResponse(
        data=[TimeseriesPoint.model_validate(p) for p in DashboardService.timeseries(metric, range)]
    )


# ============================================================================
# Audit / actions
# ============================================================================
audit_router = APIRouter(prefix="/actions", tags=["Audit"])


@audit_router.get("", response_model=ApiResponse[List[AuditLogEntry]])
async def list_audit(
    limit: int = Query(50, ge=1, le=500),
    _: Dict[str, Any] = Depends(get_current_user),
) -> ApiResponse[List[AuditLogEntry]]:
    rows = AuditRepository.list_recent(limit)
    return ApiResponse(data=[AuditLogEntry.model_validate(r) for r in rows])
