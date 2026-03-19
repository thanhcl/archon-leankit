"""
Cost Budget API endpoint

Provides GET /api/projects/{project_id}/cost-status for viewing
current spend vs budget for a project.
"""

from fastapi import APIRouter, HTTPException, Request, Response

from ..config.logfire_config import get_logger, logfire
from ..services.cost_budget_service import CostBudgetService
from ..utils.etag_utils import check_etag, generate_etag

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["cost-budget"])


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
