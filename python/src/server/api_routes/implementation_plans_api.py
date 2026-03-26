"""API endpoints for project implementation plans."""

import os

from fastapi import APIRouter, HTTPException

from ..models.api_contracts import (
    AutoLinkResult,
    CreateImplementationDependencyRequest,
    CreateImplementationItemRequest,
    CreateImplementationPhaseRequest,
    CreateImplementationPlanRequest,
    CreateTaskLinkRequest,
    ImplementationDependencyListResponse,
    ImplementationDependencyResponse,
    ImplementationItemListResponse,
    ImplementationItemResponse,
    ImplementationPhaseListResponse,
    ImplementationPhaseResponse,
    ImplementationPlanListResponse,
    ImplementationPlanResponse,
    ImportDiffItem,
    ImportPlanDiff,
    ImportPlanRequest,
    ImportPlanResponse,
    ItemRollup,
    PhaseRollup,
    PlanRollup,
    TaskLinkListResponse,
    TaskLinkResponse,
    UpdateImplementationItemRequest,
    UpdateImplementationPlanRequest,
    validate_response,
)
from ..services.projects.plan_import_service import PlanImportService
from ..services.projects.plan_rollup_service import PlanRollupService
from ..services.projects.plan_service import PlanService

router = APIRouter(prefix="/api", tags=["implementation-plans"])


# ── Plans ─────────────────────────────────────────────────────────────────────


@router.get("/plans", response_model=ImplementationPlanListResponse)
async def list_plans(project_id: str, limit: int = 50):
    """List all implementation plans for a project."""
    service = PlanService()
    ok, result = service.list_plans(project_id=project_id, limit=limit)
    if not ok:
        raise HTTPException(status_code=400, detail=result.get("error"))
    return validate_response(result, ImplementationPlanListResponse)


@router.post("/plans", response_model=ImplementationPlanResponse, status_code=201)
async def create_plan(body: CreateImplementationPlanRequest):
    """Create a new implementation plan."""
    service = PlanService()
    ok, result = service.create_plan(
        project_id=body.project_id,
        title=body.title,
        description=body.description,
        status=body.status,
        created_by=body.created_by,
        metadata=body.metadata,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=result.get("error"))
    return validate_response(result["plan"], ImplementationPlanResponse)


@router.get("/plans/{plan_id}", response_model=ImplementationPlanResponse)
async def get_plan(plan_id: str):
    """Get a single implementation plan by ID."""
    service = PlanService()
    ok, result = service.get_plan(plan_id)
    if not ok:
        raise HTTPException(status_code=404, detail=result.get("error"))
    return validate_response(result["plan"], ImplementationPlanResponse)


@router.put("/plans/{plan_id}", response_model=ImplementationPlanResponse)
async def update_plan(plan_id: str, body: UpdateImplementationPlanRequest):
    """Update an existing implementation plan."""
    service = PlanService()
    ok, result = service.update_plan(
        plan_id,
        title=body.title,
        description=body.description,
        status=body.status,
        metadata=body.metadata,
    )
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result["plan"], ImplementationPlanResponse)


# ── Phases ────────────────────────────────────────────────────────────────────


@router.get("/plans/{plan_id}/phases", response_model=ImplementationPhaseListResponse)
async def list_phases(plan_id: str):
    """List all phases for an implementation plan."""
    service = PlanService()
    ok, result = service.list_phases(plan_id)
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result, ImplementationPhaseListResponse)


@router.post("/plans/{plan_id}/phases", response_model=ImplementationPhaseResponse, status_code=201)
async def create_phase(plan_id: str, body: CreateImplementationPhaseRequest):
    """Create a new phase within an implementation plan."""
    service = PlanService()
    ok, result = service.create_phase(
        plan_id,
        title=body.title,
        description=body.description,
        phase_order=body.phase_order,
        metadata=body.metadata,
    )
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result["phase"], ImplementationPhaseResponse)


# ── Items ─────────────────────────────────────────────────────────────────────


@router.get("/plans/{plan_id}/items", response_model=ImplementationItemListResponse)
async def list_items(plan_id: str, phase_id: str | None = None):
    """List items for a plan, optionally filtered by phase."""
    service = PlanService()
    ok, result = service.list_items(plan_id, phase_id=phase_id)
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result, ImplementationItemListResponse)


@router.post("/plans/{plan_id}/items", response_model=ImplementationItemResponse, status_code=201)
async def create_item(plan_id: str, body: CreateImplementationItemRequest):
    """Create a new item within an implementation plan."""
    service = PlanService()
    ok, result = service.create_item(
        plan_id,
        phase_id=body.phase_id,
        title=body.title,
        description=body.description,
        status=body.status,
        item_order=body.item_order,
        priority=body.priority,
        complexity=body.complexity,
        item_key=body.item_key,
        metadata=body.metadata,
    )
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result["item"], ImplementationItemResponse)


@router.put("/plan-items/{item_id}", response_model=ImplementationItemResponse)
async def update_item(item_id: str, body: UpdateImplementationItemRequest):
    """Update an existing implementation item."""
    service = PlanService()
    ok, result = service.update_item(
        item_id,
        phase_id=body.phase_id,
        title=body.title,
        description=body.description,
        status=body.status,
        item_order=body.item_order,
        priority=body.priority,
        complexity=body.complexity,
        item_key=body.item_key,
        metadata=body.metadata,
    )
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result["item"], ImplementationItemResponse)


# ── Single Item ───────────────────────────────────────────────────────────────


@router.get("/plan-items/{item_id}", response_model=ImplementationItemResponse)
async def get_item(item_id: str):
    """Get a single implementation item by ID, including linked tasks."""
    service = PlanService()
    ok, result = service.get_item(item_id)
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))

    item_data = result["item"]

    tasks_ok, tasks_result = service.get_item_tasks(item_id)
    if tasks_ok:
        item_data["linked_tasks"] = tasks_result["tasks"]

    return validate_response(item_data, ImplementationItemResponse)


# ── Task Links ────────────────────────────────────────────────────────────────


@router.get("/plan-items/{item_id}/tasks", response_model=TaskLinkListResponse)
async def list_item_tasks(item_id: str):
    """List all tasks linked to an implementation item."""
    service = PlanService()
    ok, result = service.list_task_links(item_id)
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result, TaskLinkListResponse)


@router.post("/plan-item-task-links", response_model=TaskLinkResponse, status_code=201)
async def create_task_link(body: CreateTaskLinkRequest):
    """Create an explicit link between a plan item and an Archon task."""
    service = PlanService()
    ok, result = service.create_task_link(body.item_id, task_id=body.task_id, link_type=body.link_type)
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result["link"], TaskLinkResponse)


@router.delete("/plan-item-task-links/{link_id}", status_code=204)
async def delete_task_link(link_id: str):
    """Delete a plan-item–task link by ID."""
    service = PlanService()
    ok, result = service.delete_task_link(link_id)
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))


@router.post("/plan-items/auto-link", response_model=AutoLinkResult)
async def auto_link_tasks(project_id: str | None = None):
    """Scan task descriptions for 'Ref: <item_key>' patterns and auto-populate plan_item_id."""
    service = PlanService()
    ok, result = service.run_ref_link_parser(project_id=project_id)
    if not ok:
        raise HTTPException(status_code=500, detail=result.get("error"))
    return AutoLinkResult(
        scanned=result["scanned"],
        linked=result["linked"],
        skipped=result["skipped"],
        errors=result["errors"],
    )


# ── Dependencies ──────────────────────────────────────────────────────────────


@router.get("/plan-items/{item_id}/dependencies", response_model=ImplementationDependencyListResponse)
async def list_dependencies(item_id: str):
    """List all dependencies for an implementation item."""
    service = PlanService()
    ok, result = service.list_dependencies(item_id)
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result, ImplementationDependencyListResponse)


@router.post("/plan-items/{item_id}/dependencies", response_model=ImplementationDependencyResponse, status_code=201)
async def create_dependency(item_id: str, body: CreateImplementationDependencyRequest):
    """Create a dependency edge from item_id to a dependency item."""
    service = PlanService()
    ok, result = service.create_dependency(
        item_id,
        dependency_id=body.dependency_id,
        dependency_type=body.dependency_type,
        metadata=body.metadata,
    )
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result["dependency"], ImplementationDependencyResponse)


# ── Plan Import ────────────────────────────────────────────────────────────────


@router.post("/plans/import", response_model=ImportPlanResponse, status_code=200)
async def import_plan(body: ImportPlanRequest):
    """Import an implementation plan from a canonical markdown document.

    Accepts either inline ``content`` or a server-accessible ``file_path``.
    On first import creates plan + phases + items + dependencies atomically.
    On re-import (same project + title) returns a diff preview when
    ``preview_only=true`` (default), or applies the changes when ``preview_only=false``.

    Removed items are flagged in metadata and never silently deleted.
    """
    # Resolve content from file_path if not provided inline
    content = body.content
    source_path: str | None = body.file_path
    if not content:
        if not body.file_path:
            raise HTTPException(status_code=400, detail="Either 'content' or 'file_path' must be provided")
        if not os.path.isfile(body.file_path):
            raise HTTPException(status_code=400, detail=f"file_path not found: {body.file_path}")
        try:
            with open(body.file_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"Cannot read file_path: {exc}") from exc

    service = PlanImportService()
    ok, result = service.import_plan(
        body.project_id,
        content,
        plan_id=body.plan_id,
        source_path=source_path,
        preview_only=body.preview_only,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=result.get("error"))

    # Convert raw diff dicts to typed models
    raw_diff = result.get("diff")
    typed_diff: ImportPlanDiff | None = None
    if raw_diff is not None:
        typed_diff = ImportPlanDiff(
            added=[ImportDiffItem(**i) for i in raw_diff.get("added", [])],
            removed=[ImportDiffItem(**i) for i in raw_diff.get("removed", [])],
            changed=[ImportDiffItem(**i) for i in raw_diff.get("changed", [])],
            unchanged=[ImportDiffItem(**i) for i in raw_diff.get("unchanged", [])],
        )

    return ImportPlanResponse(
        action=result["action"],
        plan_id=result.get("plan_id"),
        source_hash=result["source_hash"],
        hash_changed=result.get("hash_changed"),
        phases_created=result.get("phases_created"),
        items_created=result.get("items_created"),
        dependencies_created=result.get("dependencies_created"),
        dependencies_updated=result.get("dependencies_updated"),
        diff=typed_diff,
    )


# ── Rollup ────────────────────────────────────────────────────────────────────


@router.get("/plans/{plan_id}/rollup", response_model=PlanRollup)
async def get_plan_rollup(plan_id: str):
    """Get aggregated rollup statistics for an entire plan.

    Returns per-item stats (task_count, run_count, cost_usd, progress_percent),
    phase-level aggregations, and plan-level totals. Null cost_usd is returned
    when no execution runs have cost data — task/run counts are always populated.
    """
    service = PlanRollupService()
    ok, result = service.get_plan_rollup(plan_id)
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result["rollup"], PlanRollup)


@router.get("/plans/{plan_id}/phases/{phase_id}/rollup", response_model=PhaseRollup)
async def get_phase_rollup(plan_id: str, phase_id: str):
    """Get rollup statistics for a single phase within a plan.

    Aggregates child item stats including task_count, run_count, cost_usd,
    and progress_percent. Null cost_usd when no cost data is available.
    """
    service = PlanRollupService()
    ok, result = service.get_phase_rollup(plan_id, phase_id)
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result["rollup"], PhaseRollup)


@router.get("/plan-items/{item_id}/rollup", response_model=ItemRollup)
async def get_item_rollup(item_id: str):
    """Get rollup statistics for a single plan item.

    Returns task_count, status_distribution of linked tasks, run_count,
    cost_usd (None when no cost data), and progress_percent based on item status.
    """
    service = PlanRollupService()
    ok, result = service.get_item_rollup(item_id)
    if not ok:
        status_code = 404 if "not found" in (result.get("error") or "").lower() else 400
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return validate_response(result["rollup"], ItemRollup)
