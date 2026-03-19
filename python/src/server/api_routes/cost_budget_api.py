"""
Cost Budget API endpoints

Provides GET/PUT /api/projects/{project_id}/budget-config for managing
budget limits, and GET /api/projects/{project_id}/cost-status for viewing
current spend vs budget for a project.
"""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..config.logfire_config import get_logger, logfire
from ..services.cost_budget_service import CostBudgetService
from ..utils.etag_utils import check_etag, generate_etag

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["cost-budget"])


class UpdateBudgetConfigRequest(BaseModel):
    max_cost_per_day: float = Field(..., gt=0, description="Maximum allowed cost per day in USD")
    max_cost_per_sprint: float = Field(..., gt=0, description="Maximum allowed cost per sprint in USD")


@router.get("/projects/{project_id}/budget-config")
async def get_budget_config(project_id: str, request: Request, response: Response):
    """Get budget configuration (limits) for a project."""
    try:
        if_none_match = request.headers.get("If-None-Match")

        service = CostBudgetService()
        config = service._get_budget_config(project_id)

        current_etag = generate_etag(config)
        if check_etag(if_none_match, current_etag):
            response.status_code = 304
            response.headers["ETag"] = current_etag
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return None

        response.headers["ETag"] = current_etag
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return config

    except HTTPException:
        raise
    except Exception as e:
        logfire.error(f"Failed to get budget config | project_id={project_id} | error={str(e)}")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@router.put("/projects/{project_id}/budget-config")
async def update_budget_config(project_id: str, body: UpdateBudgetConfigRequest):
    """Update budget configuration (limits) for a project."""
    try:
        service = CostBudgetService()
        success, result = service.update_budget_config(
            project_id=project_id,
            max_cost_per_day=body.max_cost_per_day,
            max_cost_per_sprint=body.max_cost_per_sprint,
        )

        if not success:
            error_msg = result.get("error", "Unknown error")
            if "not found" in error_msg.lower():
                raise HTTPException(status_code=404, detail=result)
            raise HTTPException(status_code=500, detail=result)

        logfire.info(
            f"Budget config updated via API | project_id={project_id} | "
            f"max_cost_per_day={body.max_cost_per_day} | max_cost_per_sprint={body.max_cost_per_sprint}"
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        logfire.error(f"Failed to update budget config | project_id={project_id} | error={str(e)}")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@router.get("/projects/{project_id}/cost-status")
async def get_cost_status(project_id: str, request: Request, response: Response):
    """Get current cost status for a project including spend vs budget."""
    try:
        if_none_match = request.headers.get("If-None-Match")

        service = CostBudgetService()
        success, result = service.get_cost_status(project_id)

        if not success:
            error_msg = result.get("error", "Unknown error")
            logfire.error(f"Failed to get cost status | project_id={project_id} | error={error_msg}")
            raise HTTPException(status_code=500, detail=result)

        current_etag = generate_etag(result)
        if check_etag(if_none_match, current_etag):
            response.status_code = 304
            response.headers["ETag"] = current_etag
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return None

        response.headers["ETag"] = current_etag
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return result

    except HTTPException:
        raise
    except Exception as e:
        logfire.error(f"Failed to get cost status | project_id={project_id} | error={str(e)}")
        raise HTTPException(status_code=500, detail={"error": str(e)})
