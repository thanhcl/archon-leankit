"""
Engine SSE API — Server-Sent Events endpoint for real-time engine updates.

Streams task status changes, engine health events, approval requests,
and other lifecycle events to connected clients. Replaces HTTP polling
for real-time scenarios.

Adopted from upstream Archon v0.3.2 SSE transport pattern.
"""

from fastapi import APIRouter, Query, Request

from ..services.engine.sse_transport import get_sse_transport

router = APIRouter(prefix="/api/engine", tags=["engine-sse"])


@router.get("/events")
async def engine_events(
    request: Request,
    filter: str | None = Query(
        None,
        description="Comma-separated event types to filter (e.g., 'task_status,engine_health')",
    ),
):
    """Stream engine events via SSE.

    Supports reconnection replay via Last-Event-ID header.
    Optional filter parameter to subscribe to specific event types.
    """
    transport = get_sse_transport()

    event_filter: set[str] | None = None
    if filter:
        event_filter = {t.strip() for t in filter.split(",") if t.strip()}

    return transport.create_response(request, event_filter=event_filter)


@router.get("/events/stats")
async def engine_events_stats():
    """Get SSE transport statistics."""
    transport = get_sse_transport()
    return {
        "subscribers": transport.subscriber_count,
        "buffer_size": transport.buffer_size,
    }
