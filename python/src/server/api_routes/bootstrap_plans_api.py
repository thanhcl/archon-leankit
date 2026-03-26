"""Bootstrap plan API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models.api_contracts import (
    BootstrapPlanListResponse,
    BootstrapPlanMaterializeResponse,
    BootstrapPlanPreviewResponse,
    BootstrapPlanResponse,
    validate_response,
)
from ..services.projects.bootstrap_architect import (
    create_bootstrap_plan_envelope,
    normalize_bootstrap_architect_provider,
)
from ..services.projects.bootstrap_plan_service import BootstrapPlanService
from ..services.projects.bootstrap_planner import (
    DEFAULT_BOOTSTRAP_POLICY,
    DEFAULT_BOOTSTRAP_TEMPLATE,
    DEFAULT_PROJECT_TYPE,
    build_bootstrap_context,
)

router = APIRouter(prefix="/api", tags=["bootstrap-plans"])


class BootstrapPlanPreviewRequest(BaseModel):
    """Request body for generating a dry-run bootstrap plan preview."""

    project_title: str
    project_description: str | None = None
    github_repo: str | None = None
    bootstrap_template: str | None = None
    project_type: str | None = None
    bootstrap_policy: str | None = None
    bootstrap_architect_provider: str | None = None
    bootstrap_architect_model: str | None = None
    source_app: str | None = None


@router.get("/projects/{project_id}/bootstrap-plans", response_model=BootstrapPlanListResponse)
async def list_project_bootstrap_plans(project_id: str, limit: int = 20):
    """List bootstrap plans for a project."""
    service = BootstrapPlanService()
    success, result = service.list_plans(project_id=project_id, limit=limit)
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result, BootstrapPlanListResponse)


@router.get("/bootstrap-plans/{plan_id}", response_model=BootstrapPlanResponse)
async def get_bootstrap_plan(plan_id: str):
    """Get a single bootstrap plan by ID."""
    service = BootstrapPlanService()
    success, result = service.get_plan(plan_id)
    if not success:
        raise HTTPException(status_code=404, detail=result)
    return validate_response(result["plan"], BootstrapPlanResponse)


@router.post("/bootstrap-plans/{plan_id}/materialize-backlog", response_model=BootstrapPlanMaterializeResponse)
async def materialize_bootstrap_plan_backlog(plan_id: str):
    """Materialize persisted bootstrap-plan backlog items into project tasks."""
    service = BootstrapPlanService()
    success, result = service.materialize_backlog(plan_id)
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result, BootstrapPlanMaterializeResponse)


@router.post(
    "/projects/{project_id}/bootstrap-plans/preview",
    response_model=BootstrapPlanPreviewResponse,
)
async def preview_bootstrap_plan(
    project_id: str,
    body: BootstrapPlanPreviewRequest,
    dry_run: bool = True,
):
    """Generate a bootstrap plan preview without persisting any tasks.

    When dry_run=true (the default), the plan is computed and returned but
    no tasks or plan records are written to the database.  This is the
    canonical CI-7 dry-run path used by operator verification scripts.
    """
    if not dry_run:
        raise HTTPException(
            status_code=400,
            detail="This endpoint only supports dry_run=true. Use POST /api/projects to create a real project with tasks.",
        )

    context = build_bootstrap_context(
        project_title=body.project_title,
        project_description=body.project_description,
        github_repo=body.github_repo,
        bootstrap_template=body.bootstrap_template or DEFAULT_BOOTSTRAP_TEMPLATE,
        project_type=body.project_type or DEFAULT_PROJECT_TYPE,
        bootstrap_policy=body.bootstrap_policy or DEFAULT_BOOTSTRAP_POLICY,
    )
    requested_provider = normalize_bootstrap_architect_provider(body.bootstrap_architect_provider)
    envelope = await create_bootstrap_plan_envelope(
        context=context,
        requested_provider=requested_provider,
        model=body.bootstrap_architect_model,
    )
    service = BootstrapPlanService()
    success, result = service.preview_plan(
        project_id=project_id,
        source_app=body.source_app,
        context=context,
        envelope=envelope,
    )
    if not success:
        raise HTTPException(status_code=500, detail=result)
    return validate_response(result, BootstrapPlanPreviewResponse)
