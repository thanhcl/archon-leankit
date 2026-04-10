"""
Agent Runtime API endpoints.

Provides runtime registration, heartbeat, task claiming, and querying.
Adopted from Multica daemon pattern (Workstream M-P1, M-P2).
"""

from fastapi import APIRouter, HTTPException

from ..models.api_contracts import (
    AgentRuntimeListResponse,
    AgentRuntimeResponse,
    AssignTaskRequest,
    ClaimTaskResponse,
    RegisterRuntimeRequest,
    RuntimeHeartbeatRequest,
    RuntimeProgressRequest,
    validate_response,
)
from ..services.agent_runtime_service import AgentRuntimeService

router = APIRouter(prefix="/api/runtimes", tags=["agent-runtimes"])


# ── Registration ──────────────────────────────────────────────────────────


@router.post("/register", response_model=AgentRuntimeResponse)
async def register_runtime(body: RegisterRuntimeRequest):
    """Register a new agent runtime. Runner calls this on startup."""
    service = AgentRuntimeService()
    success, result = await service.register_runtime(
        device_name=body.device_name,
        capabilities=body.capabilities,
        supported_runners=body.supported_runners,
        max_concurrent_tasks=body.max_concurrent_tasks,
        agent_id=body.agent_id,
        version=body.version,
        metadata=body.metadata,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result["runtime"], AgentRuntimeResponse)


@router.post("/{runtime_id}/unregister", response_model=AgentRuntimeResponse)
async def unregister_runtime(runtime_id: str):
    """Gracefully unregister a runtime. Runner calls this on shutdown."""
    service = AgentRuntimeService()
    success, result = await service.unregister_runtime(runtime_id)
    if not success:
        raise HTTPException(status_code=404, detail=result)
    return validate_response(result["runtime"], AgentRuntimeResponse)


# ── Heartbeat ─────────────────────────────────────────────────────────────


@router.post("/{runtime_id}/heartbeat", response_model=AgentRuntimeResponse)
async def runtime_heartbeat(runtime_id: str, body: RuntimeHeartbeatRequest | None = None):
    """Record a heartbeat from a runtime. Called every 30 seconds."""
    service = AgentRuntimeService()
    current_task_count = body.current_task_count if body else None
    success, result = await service.heartbeat(runtime_id, current_task_count)
    if not success:
        raise HTTPException(status_code=404, detail=result)
    return validate_response(result["runtime"], AgentRuntimeResponse)


@router.get("/{runtime_id}/health")
async def runtime_health(runtime_id: str):
    """Check health of a specific runtime."""
    service = AgentRuntimeService()
    success, result = service.get_runtime(runtime_id)
    if not success:
        raise HTTPException(status_code=404, detail=result)
    runtime = result["runtime"]
    return {
        "runtime_id": runtime_id,
        "status": runtime["status"],
        "last_heartbeat": runtime.get("last_heartbeat"),
        "current_task_count": runtime.get("current_task_count", 0),
        "max_concurrent_tasks": runtime.get("max_concurrent_tasks", 1),
        "healthy": runtime["status"] == "online",
    }


# ── Task Claim ────────────────────────────────────────────────────────────


@router.post("/{runtime_id}/claim", response_model=ClaimTaskResponse)
async def claim_task(runtime_id: str):
    """Claim the next available task for this runtime.

    Runner polls this endpoint to get work. Atomic claim prevents double-assignment.
    """
    service = AgentRuntimeService()
    success, result = await service.claim_task(runtime_id)
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/{runtime_id}/release/{task_id}")
async def release_task(runtime_id: str, task_id: str):
    """Release a claimed task after completion or failure."""
    service = AgentRuntimeService()
    success, result = await service.release_task(runtime_id, task_id)
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return result


# ── Task Status (for cancellation detection) ─────────────────────────────


@router.get("/tasks/{task_id}/status")
async def get_task_status(task_id: str):
    """Get task status for cancellation detection. Runner polls this every 5s."""
    from ..services.projects.task_service import TaskService

    task_service = TaskService()
    success, result = task_service.get_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail=result)
    task = result.get("task") or result
    return {
        "task_id": task_id,
        "status": task.get("status"),
        "runtime_id": task.get("runtime_id"),
    }


# ── Progress ──────────────────────────────────────────────────────────────


@router.post("/tasks/{task_id}/progress")
async def report_progress(task_id: str, body: RuntimeProgressRequest):
    """Report execution progress for a task. Runner calls this during execution."""
    service = AgentRuntimeService()
    success, result = await service.report_progress(
        task_id=task_id,
        runtime_id=body.runtime_id,
        step_count=body.step_count,
        percentage=body.percentage,
        current_action=body.current_action,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return result


# ── Query ─────────────────────────────────────────────────────────────────


@router.get("", response_model=AgentRuntimeListResponse)
async def list_runtimes(
    status: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
):
    """List registered runtimes with optional filters."""
    service = AgentRuntimeService()
    success, result = service.list_runtimes(status=status, agent_id=agent_id, limit=limit)
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result, AgentRuntimeListResponse)


@router.get("/{runtime_id}", response_model=AgentRuntimeResponse)
async def get_runtime(runtime_id: str):
    """Get a single runtime by ID."""
    service = AgentRuntimeService()
    success, result = service.get_runtime(runtime_id)
    if not success:
        raise HTTPException(status_code=404, detail=result)
    return validate_response(result["runtime"], AgentRuntimeResponse)


# ── Assignment Routing (M-P3-02) ──────────────────────────────────────────


@router.post("/tasks/{task_id}/assign")
async def assign_task(task_id: str, body: AssignTaskRequest):
    """Assign a task to a human or agent with routing logic.

    - Agent assignment → enters execution pipeline
    - Human assignment → notification only
    - Reassignment mid-execution → cancels running execution
    """
    from ..services.assignment_router import AssignmentRouter

    router_svc = AssignmentRouter()
    success, result = await router_svc.assign(
        task_id=task_id,
        assignee_type=body.assignee_type,
        assignee_id=body.assignee_id,
        reason=body.reason,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return result


# ── Stale Check ───────────────────────────────────────────────────────────


@router.post("/check-stale")
async def check_stale_runtimes():
    """Mark stale runtimes as degraded/offline. Called by engine or scheduler."""
    service = AgentRuntimeService()
    success, result = await service.check_stale_runtimes()
    if not success:
        raise HTTPException(status_code=500, detail=result)
    return result
