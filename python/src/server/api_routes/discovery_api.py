"""
Discovery API endpoints — manage feeds, view history, run discovery, manage gaps.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config.logfire_config import get_logger
from ..services.discovery.discovery_pipeline import DiscoveryPipeline

logger = get_logger(__name__)

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


# ── Request Models ────────────────────────────────────────────

class AddFeedRequest(BaseModel):
    project_id: str
    feed_type: str
    name: str
    config: dict
    poll_interval_hours: int = 24
    max_items_per_poll: int = 5


class UpdateFeedRequest(BaseModel):
    enabled: bool | None = None
    config: dict | None = None
    poll_interval_hours: int | None = None
    max_items_per_poll: int | None = None


class RecordDiscoveryRequest(BaseModel):
    project_id: str
    url: str
    title: str | None = None
    snippet: str | None = None
    feed_id: str | None = None


class ResolveGapRequest(BaseModel):
    resolved_by_page_id: str


# ── Feed Endpoints ────────────────────────────────────────────

@router.post("/feeds")
async def add_feed(req: AddFeedRequest):
    pipeline = DiscoveryPipeline()
    ok, result = pipeline.add_feed(
        project_id=req.project_id,
        feed_type=req.feed_type,
        name=req.name,
        config=req.config,
        poll_interval_hours=req.poll_interval_hours,
        max_items_per_poll=req.max_items_per_poll,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


@router.get("/feeds")
async def list_feeds(project_id: str | None = None, enabled_only: bool = False):
    pipeline = DiscoveryPipeline()
    ok, result = pipeline.list_feeds(project_id=project_id, enabled_only=enabled_only)
    if not ok:
        raise HTTPException(status_code=500, detail={"error": "Failed to list feeds"}) from None
    return {"feeds": result, "count": len(result)}


@router.put("/feeds/{feed_id}")
async def update_feed(feed_id: str, req: UpdateFeedRequest):
    pipeline = DiscoveryPipeline()
    ok, result = pipeline.update_feed(
        feed_id=feed_id,
        enabled=req.enabled,
        config=req.config,
        poll_interval_hours=req.poll_interval_hours,
        max_items_per_poll=req.max_items_per_poll,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


@router.delete("/feeds/{feed_id}")
async def delete_feed(feed_id: str):
    pipeline = DiscoveryPipeline()
    ok, msg = pipeline.delete_feed(feed_id)
    if not ok:
        raise HTTPException(status_code=500, detail={"error": msg}) from None
    return {"message": msg}


# ── History Endpoints ─────────────────────────────────────────

@router.get("/history")
async def get_history(
    project_id: str | None = None,
    feed_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
):
    pipeline = DiscoveryPipeline()
    ok, result = pipeline.get_history(
        project_id=project_id,
        feed_id=feed_id,
        status=status,
        limit=limit,
    )
    if not ok:
        raise HTTPException(status_code=500, detail={"error": "Failed to get history"}) from None
    return {"items": result, "count": len(result)}


@router.post("/record")
async def record_discovery(req: RecordDiscoveryRequest):
    pipeline = DiscoveryPipeline()
    ok, result = pipeline.record_discovery(
        project_id=req.project_id,
        url=req.url,
        title=req.title,
        snippet=req.snippet,
        feed_id=req.feed_id,
    )
    if not ok:
        raise HTTPException(status_code=409, detail={"error": result}) from None
    return result


# ── Gap Endpoints ─────────────────────────────────────────────

@router.get("/gaps")
async def get_gaps(project_id: str, status: str = "open", limit: int = 20):
    pipeline = DiscoveryPipeline()
    ok, result = pipeline.get_gaps(project_id=project_id, status=status, limit=limit)
    if not ok:
        raise HTTPException(status_code=500, detail={"error": "Failed to get gaps"}) from None
    return {"gaps": result, "count": len(result)}


@router.post("/gaps/{gap_id}/resolve")
async def resolve_gap(gap_id: str, req: ResolveGapRequest):
    pipeline = DiscoveryPipeline()
    ok, msg = pipeline.resolve_gap(gap_id, req.resolved_by_page_id)
    if not ok:
        raise HTTPException(status_code=500, detail={"error": msg}) from None
    return {"message": msg}


# ── Due Feeds ─────────────────────────────────────────────────

@router.get("/due-feeds")
async def get_due_feeds(project_id: str | None = None):
    pipeline = DiscoveryPipeline()
    feeds = pipeline.get_due_feeds(project_id=project_id)
    return {"feeds": feeds, "count": len(feeds)}


# ── Run Endpoint ──────────────────────────────────────────────

class RunDiscoveryRequest(BaseModel):
    project_id: str | None = None
    feed_id: str | None = None
    max_iterations: int = 2


@router.post("/run")
async def run_discovery(req: RunDiscoveryRequest):
    """Run a full discovery cycle: discover → ingest → lint."""
    from ..services.discovery.discovery_runner import DiscoveryRunner

    runner = DiscoveryRunner()
    result = await runner.run_cycle(
        project_id=req.project_id,
        feed_id=req.feed_id,
        max_iterations=req.max_iterations,
    )
    return result
