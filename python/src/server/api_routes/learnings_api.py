"""
Learnings API endpoints — view, dismiss, promote learnings from CC task executions.
"""

from fastapi import APIRouter, HTTPException

from ..config.logfire_config import get_logger
from ..services.engine.learning_processor import LearningProcessor
from ..utils import get_supabase_client

logger = get_logger(__name__)

router = APIRouter(prefix="/api/learnings", tags=["learnings"])


def _get_processor() -> LearningProcessor:
    return LearningProcessor()


@router.get("")
async def list_learnings(project_id: str | None = None, status: str | None = None):
    """List learnings, optionally filtered by project and status."""
    processor = _get_processor()
    ok, result = processor.list_learnings(project_id=project_id, status=status)
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


@router.get("/patterns")
async def list_patterns(project_id: str | None = None, min_recurrence: int = 2):
    """List recurring patterns (recurrence >= min_recurrence)."""
    processor = _get_processor()
    ok, result = processor.list_patterns(project_id=project_id, min_recurrence=min_recurrence)
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


@router.get("/stats")
async def get_stats(project_id: str | None = None):
    """Get learning stats: total, promoted, recurring, by type, by area."""
    processor = _get_processor()
    ok, result = processor.get_stats(project_id=project_id)
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


@router.put("/{learning_id}/dismiss")
async def dismiss_learning(learning_id: str):
    """Dismiss a learning (Owner decides it's not useful)."""
    processor = _get_processor()
    ok, msg = await processor.dismiss(learning_id)
    if not ok:
        raise HTTPException(status_code=500, detail={"error": msg}) from None
    return {"message": msg}


@router.put("/{learning_id}/promote")
async def promote_learning(learning_id: str):
    """Manually promote a learning to KB."""
    processor = _get_processor()
    ok, msg = await processor.manual_promote(learning_id)
    if not ok:
        raise HTTPException(status_code=500, detail={"error": msg}) from None
    return {"message": msg}


@router.get("/promotion-log")
async def get_promotion_log(project_id: str | None = None, limit: int = 50):
    """List promotion audit log entries ordered by most recent first."""
    try:
        client = get_supabase_client()
        query = (
            client.table("archon_promotion_log")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if project_id:
            query = query.eq("project_id", project_id)
        resp = query.execute()
        entries = resp.data or []
        return {"entries": entries, "count": len(entries)}
    except Exception as e:
        logger.error(f"Failed to fetch promotion log: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from None
