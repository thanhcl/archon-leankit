"""
Sprint Stats & Task Estimation API endpoints

Provides:
- GET /api/projects/{project_id}/sprint-stats - Sprint completion metrics
- GET /api/projects/{project_id}/task-estimates - Predicted duration/cost for active tasks
- GET /api/tasks/{task_id}/estimate - Single task estimate
"""

from fastapi import APIRouter, HTTPException, Request, Response

from ..config.logfire_config import get_logger, logfire
from ..services.sprint_stats_service import SprintStatsService
from ..services.task_estimation_service import TaskEstimationService
from ..utils.etag_utils import check_etag, generate_etag

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["sprint-stats"])


@router.get("/projects/{project_id}/sprint-stats")
async def get_sprint_stats(
    project_id: str,
    request: Request,
    response: Response,
    group_by: str = "date",
):
    """Get sprint statistics for a project, optionally grouped by task_type, module, sprint, feature, or phase."""
    try:
        if_none_match = request.headers.get("If-None-Match")

        service = SprintStatsService()
        success, result = service.get_sprint_stats(project_id, group_by=group_by)

        if not success:
            error_msg = result.get("error", "Unknown error")
            logfire.error(f"Failed to get sprint stats | project_id={project_id} | error={error_msg}")
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
        logfire.error(f"Failed to get sprint stats | project_id={project_id} | error={str(e)}")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@router.get("/projects/{project_id}/task-estimates")
async def get_project_task_estimates(project_id: str, request: Request, response: Response):
    """Get predicted duration and cost for all active tasks in a project.

    Includes predicted vs actual comparison for completed tasks.
    """
    try:
        if_none_match = request.headers.get("If-None-Match")

        service = TaskEstimationService()
        success, result = service.get_project_estimates(project_id)

        if not success:
            error_msg = result.get("error", "Unknown error")
            logfire.error(f"Failed to get task estimates | project_id={project_id} | error={error_msg}")
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
        logfire.error(f"Failed to get task estimates | project_id={project_id} | error={str(e)}")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@router.get("/tasks/{task_id}/estimate")
async def get_task_estimate(task_id: str):
    """Get duration and cost estimate for a single task based on historical data."""
    try:
        from ..services.projects.task_service import TaskService

        task_service = TaskService()
        success, result = task_service.get_task(task_id)
        if not success:
            error_msg = result.get("error", "Unknown error")
            if "not found" in error_msg.lower():
                raise HTTPException(status_code=404, detail=error_msg)
            raise HTTPException(status_code=500, detail=result)

        task = result["task"]
        project_id = task["project_id"]
        complexity = task.get("complexity", "simple")
        priority = task.get("priority", "medium")

        estimation_service = TaskEstimationService()
        est_success, estimate = estimation_service.estimate_task(project_id, complexity, priority)

        if not est_success:
            raise HTTPException(status_code=500, detail=estimate)

        return {
            "task_id": task_id,
            "project_id": project_id,
            **estimate,
        }

    except HTTPException:
        raise
    except Exception as e:
        logfire.error(f"Failed to get task estimate | task_id={task_id} | error={str(e)}")
        raise HTTPException(status_code=500, detail={"error": str(e)})
