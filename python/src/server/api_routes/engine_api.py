"""
Engine API endpoints — review config management for LeanKit V3 Task Engine.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config.logfire_config import get_logger
from ..services.credential_service import credential_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/engine", tags=["engine"])

# Default config stored in archon_settings under "engine" category
_CONFIG_KEY = "REVIEW_CONFIG"
_CONFIG_CATEGORY = "engine"


class ReviewConfigRequest(BaseModel):
    simple_task_mode: str | None = None
    complex_task_mode: str | None = None
    security_sensitive_mode: str | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int | None = None
    api_fallback_to_self_review: bool | None = None


_DEFAULTS: dict[str, Any] = {
    "simple_task_mode": "self-review",
    "complex_task_mode": "api",
    "security_sensitive_mode": "api",
    "provider": "anthropic",
    "model": "",
    "temperature": 0.3,
    "max_tokens": 2000,
    "timeout": 60,
    "api_fallback_to_self_review": True,
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

        # Validate modes
        valid_modes = {"self-review", "api"}
        for field in ("simple_task_mode", "complex_task_mode", "security_sensitive_mode"):
            if field in updates and updates[field] not in valid_modes:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid {field}: must be 'self-review' or 'api'",
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
