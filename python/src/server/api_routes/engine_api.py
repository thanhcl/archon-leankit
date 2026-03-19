"""
Engine API endpoints — review config, concurrency config, and status for LeanKit V3 Task Engine.
"""

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config.logfire_config import get_logger
from ..services.credential_service import credential_service
from ..services.projects import ProjectService, TaskService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/engine", tags=["engine"])

# Default config stored in archon_settings under "engine" category
_CONFIG_KEY = "REVIEW_CONFIG"
_CONCURRENCY_CONFIG_KEY = "CONCURRENCY_CONFIG"
_CONFIG_CATEGORY = "engine"


class ReviewConfigRequest(BaseModel):
    review_mode: str | None = None
    security_override_to_api: bool | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int | None = None
    api_fallback_to_self_review: bool | None = None
    independent_review_enabled: bool | None = None
    confidence_approve_threshold: float | None = None
    confidence_retry_threshold: float | None = None


_DEFAULTS: dict[str, Any] = {
    "review_mode": "self-review",
    "security_override_to_api": True,
    "provider": "anthropic",
    "model": "",
    "temperature": 0.3,
    "max_tokens": 2000,
    "timeout": 60,
    "api_fallback_to_self_review": True,
    "independent_review_enabled": True,
    "confidence_approve_threshold": 0.8,
    "confidence_retry_threshold": 0.5,
}


@router.get("/review-config")
async def get_review_config():
    """Get current review configuration."""
    try:
        stored = await credential_service.get_credential(_CONFIG_KEY)
        if stored and isinstance(stored, dict):
            config = {**_DEFAULTS, **stored}
        else:
            config = dict(_DEFAULTS)
        return config
    except Exception as e:
        logger.error(f"Failed to get review config: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


@router.put("/review-config")
async def update_review_config(request: ReviewConfigRequest):
    """Update review configuration (partial updates supported)."""
    try:
        # Get current config
        stored = await credential_service.get_credential(_CONFIG_KEY)
        current = {**_DEFAULTS}
        if stored and isinstance(stored, dict):
            current.update(stored)

        # Apply updates
        updates = request.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Validate mode
        valid_modes = {"self-review", "api", "multi-perspective"}
        if "review_mode" in updates and updates["review_mode"] not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid review_mode: must be one of {valid_modes}",
            )

        valid_providers = {"anthropic", "openai", "google"}
        if "provider" in updates and updates["provider"] not in valid_providers:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid provider: must be one of {valid_providers}",
            )

        current.update(updates)

        # Store as JSON string in settings
        import json
        await credential_service.set_credential(
            _CONFIG_KEY,
            json.dumps(current),
            category=_CONFIG_CATEGORY,
            description="Task engine review configuration",
        )

        logger.info(f"Review config updated | fields={list(updates.keys())}")
        return {"message": "Review config updated", "config": current}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update review config: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# Concurrency configuration
# ---------------------------------------------------------------------------

_CONCURRENCY_DEFAULTS: dict[str, Any] = {
    "max_parallel_default": int(os.environ.get("MAX_PARALLEL", "3")),
    "max_parallel_global": int(os.environ.get("MAX_PARALLEL_GLOBAL", "10")),
}


class ConcurrencyConfigRequest(BaseModel):
    max_parallel_default: int | None = None
    max_parallel_global: int | None = None


@router.get("/concurrency-config")
async def get_concurrency_config():
    """Get current engine concurrency limits."""
    try:
        stored = await credential_service.get_credential(_CONCURRENCY_CONFIG_KEY)
        if stored and isinstance(stored, dict):
            config = {**_CONCURRENCY_DEFAULTS, **stored}
        else:
            config = dict(_CONCURRENCY_DEFAULTS)
        return config
    except Exception as e:
        logger.error(f"Failed to get concurrency config: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


@router.put("/concurrency-config")
async def update_concurrency_config(request: ConcurrencyConfigRequest):
    """Update engine concurrency limits (takes effect on next engine restart)."""
    try:
        stored = await credential_service.get_credential(_CONCURRENCY_CONFIG_KEY)
        current = {**_CONCURRENCY_DEFAULTS}
        if stored and isinstance(stored, dict):
            current.update(stored)

        updates = request.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Validate limits
        for field in ("max_parallel_default", "max_parallel_global"):
            if field in updates:
                val = updates[field]
                if not isinstance(val, int) or val < 1 or val > 50:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{field} must be an integer between 1 and 50",
                    )

        current.update(updates)

        import json
        await credential_service.set_credential(
            _CONCURRENCY_CONFIG_KEY,
            json.dumps(current),
            category=_CONFIG_CATEGORY,
            description="Task engine concurrency configuration",
        )

        logger.info(f"Concurrency config updated | fields={list(updates.keys())}")
        return {"message": "Concurrency config updated (restart engine to apply)", "config": current}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update concurrency config: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# Engine status — derived from task statuses in database
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_engine_status():
    """Show engine execution slot usage per project.

    Derives status from tasks currently in 'executing' state,
    combined with configured concurrency limits from project settings.
    """
    try:
        task_service = TaskService()
        project_service = ProjectService()

        # Get all executing tasks
        ok, result = task_service.list_tasks(
            status="executing",
            include_closed=False,
            include_archived=False,
        )
        executing_tasks = result.get("tasks", []) if ok else []

        # Count executing tasks per project
        per_project_executing: dict[str, int] = {}
        for task in executing_tasks:
            pid = task.get("project_id", "unknown")
            per_project_executing[pid] = per_project_executing.get(pid, 0) + 1

        # Get concurrency config
        stored = await credential_service.get_credential(_CONCURRENCY_CONFIG_KEY)
        concurrency_config = {**_CONCURRENCY_DEFAULTS}
        if stored and isinstance(stored, dict):
            concurrency_config.update(stored)

        max_global = concurrency_config["max_parallel_global"]
        default_per_project = concurrency_config["max_parallel_default"]

        # Get per-project limits from office_settings
        ok, proj_result = project_service.list_office_configs()
        project_slots = []
        if ok:
            for proj in proj_result.get("projects", []):
                pid = proj["id"]
                settings = proj.get("office_settings") or {}
                max_concurrent = settings.get("max_concurrent", default_per_project)
                try:
                    max_concurrent = int(max_concurrent)
                except (TypeError, ValueError):
                    max_concurrent = default_per_project

                used = per_project_executing.get(pid, 0)
                project_slots.append({
                    "project_id": pid,
                    "project_name": proj.get("title", ""),
                    "max_concurrent": max_concurrent,
                    "slots_used": used,
                    "slots_available": max(0, max_concurrent - used),
                })

        total_executing = sum(per_project_executing.values())

        return {
            "global": {
                "max_parallel_global": max_global,
                "total_executing": total_executing,
                "global_slots_available": max(0, max_global - total_executing),
            },
            "default_per_project": default_per_project,
            "projects": project_slots,
        }

    except Exception as e:
        logger.error(f"Failed to get engine status: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e
