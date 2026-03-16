"""
Code Patterns API endpoints — list, validate, deprecate, stats for expert code patterns.
"""

from fastapi import APIRouter, HTTPException

from ..config.logfire_config import get_logger
from ..services.engine.learning_processor import LearningProcessor

logger = get_logger(__name__)

router = APIRouter(prefix="/api/patterns", tags=["patterns"])


def _get_processor() -> LearningProcessor:
    return LearningProcessor()


@router.get("")
async def list_patterns(
    project_id: str | None = None,
    category: str | None = None,
    status: str | None = None,
):
    """List code patterns, optionally filtered by project, category, and status."""
    processor = _get_processor()
    ok, result = processor.list_code_patterns(
        project_id=project_id, category=category, status=status,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


@router.get("/stats")
async def get_pattern_stats(project_id: str | None = None):
    """Get code pattern stats: total, promoted, validated, by category, top patterns."""
    processor = _get_processor()
    ok, result = processor.get_pattern_stats(project_id=project_id)
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


@router.get("/{pattern_id}")
async def get_pattern(pattern_id: str):
    """Get a single code pattern by ID."""
    processor = _get_processor()
    ok, result = processor.get_code_pattern(pattern_id)
    if not ok:
        raise HTTPException(status_code=404, detail=result) from None
    return result


@router.put("/{pattern_id}/validate")
async def validate_pattern(pattern_id: str):
    """Mark a pattern as expert-validated by the owner."""
    processor = _get_processor()
    ok, msg = await processor.validate_pattern(pattern_id)
    if not ok:
        raise HTTPException(status_code=500, detail={"error": msg}) from None
    return {"message": msg}


@router.put("/{pattern_id}/deprecate")
async def deprecate_pattern(pattern_id: str):
    """Deprecate an outdated pattern."""
    processor = _get_processor()
    ok, msg = await processor.deprecate_pattern(pattern_id)
    if not ok:
        raise HTTPException(status_code=500, detail={"error": msg}) from None
    return {"message": msg}
