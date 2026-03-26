"""
Execution run API endpoints.

Provides a minimal control-plane API for recording and querying runtime attempts.
"""

from fastapi import APIRouter, HTTPException

from ..models.api_contracts import (
    CreateExecutionRunRequest,
    ExecutionRunListResponse,
    ExecutionRunResponse,
    UpdateExecutionRunRequest,
    validate_response,
)
from ..services.projects.execution_run_service import ExecutionRunService

router = APIRouter(prefix="/api/execution-runs", tags=["execution-runs"])


@router.get("", response_model=ExecutionRunListResponse)
async def list_execution_runs(
    task_id: str | None = None,
    project_id: str | None = None,
    bootstrap_plan_id: str | None = None,
    status: str | None = None,
    stage: str | None = None,
    limit: int = 50,
):
    """List execution runs with optional filtering."""
    service = ExecutionRunService()
    success, result = service.list_runs(
        task_id=task_id,
        project_id=project_id,
        bootstrap_plan_id=bootstrap_plan_id,
        status=status,
        stage=stage,
        limit=limit,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result, ExecutionRunListResponse)


@router.get("/{run_id}", response_model=ExecutionRunResponse)
async def get_execution_run(run_id: str):
    """Get a single execution run by ID."""
    service = ExecutionRunService()
    success, result = service.get_run(run_id)
    if not success:
        raise HTTPException(status_code=404, detail=result)
    return validate_response(result["run"], ExecutionRunResponse)


@router.post("", response_model=ExecutionRunResponse)
async def create_execution_run(body: CreateExecutionRunRequest):
    """Create a new execution run."""
    service = ExecutionRunService()
    success, result = await service.create_run(**body.model_dump())
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result["run"], ExecutionRunResponse)


@router.patch("/{run_id}", response_model=ExecutionRunResponse)
async def update_execution_run(run_id: str, body: UpdateExecutionRunRequest):
    """Update an execution run."""
    service = ExecutionRunService()
    success, result = await service.update_run(run_id, body.model_dump(exclude_none=True))
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result["run"], ExecutionRunResponse)
