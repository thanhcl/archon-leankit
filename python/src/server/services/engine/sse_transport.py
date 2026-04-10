"""
SSE Event Transport — Server-Sent Events for real-time engine updates.

Adopted from upstream Archon v0.3.2 SSE transport pattern.
Provides a buffered event stream with reconnection replay,
replacing HTTP polling for task status, wiki ops, and engine health.

Features:
- Event buffer with TTL (60s) and max capacity (500 events)
- Reconnection replay via Last-Event-ID header
- Per-client subscription with optional event type filtering
- Thread-safe event publishing from any engine component

Usage:
    transport = SSETransport()
    # Publish events from engine
    transport.publish("task_status", {"task_id": "t-1", "status": "executing"})
    # Stream to client via FastAPI
    @app.get("/api/engine/events")
    async def engine_events(request: Request):
        return transport.create_response(request)
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Defaults
DEFAULT_BUFFER_TTL_SECONDS = 60
DEFAULT_BUFFER_MAX_SIZE = 500
DEFAULT_KEEPALIVE_SECONDS = 15


@dataclass
class SSEEvent:
    """A single SSE event in the buffer."""

    id: str
    event_type: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_sse_dict(self) -> dict[str, str]:
        """Format for sse-starlette."""
        return {
            "id": self.id,
            "event": self.event_type,
            "data": json.dumps(self.data, default=str),
        }

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp


class SSETransport:
    """Buffered SSE event transport with reconnection replay.

    Events are stored in a ring buffer with TTL-based expiration.
    When a client reconnects with Last-Event-ID, missed events
    are replayed from the buffer.
    """

    def __init__(
        self,
        buffer_ttl_seconds: int = DEFAULT_BUFFER_TTL_SECONDS,
        buffer_max_size: int = DEFAULT_BUFFER_MAX_SIZE,
        keepalive_seconds: int = DEFAULT_KEEPALIVE_SECONDS,
    ) -> None:
        self._buffer: deque[SSEEvent] = deque(maxlen=buffer_max_size)
        self._buffer_ttl = buffer_ttl_seconds
        self._keepalive_seconds = keepalive_seconds
        self._subscribers: dict[str, asyncio.Queue[SSEEvent | None]] = {}
        self._lock = asyncio.Lock()

    def publish(
        self,
        event_type: str,
        data: dict[str, Any],
        event_id: str | None = None,
    ) -> str:
        """Publish an event to all subscribers and buffer it.

        Args:
            event_type: Event type string (e.g., "task_status", "engine_health").
            data: Event payload.
            event_id: Optional custom event ID. Auto-generated if not provided.

        Returns:
            The event ID.
        """
        eid = event_id or str(uuid.uuid4())[:8]
        event = SSEEvent(id=eid, event_type=event_type, data=data)

        # Add to buffer
        self._buffer.append(event)

        # Expire old events
        self._expire_buffer()

        # Broadcast to active subscribers
        dead_subs: list[str] = []
        for sub_id, queue in self._subscribers.items():
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead_subs.append(sub_id)
                logger.warning(f"SSE subscriber queue full, dropping: sub_id={sub_id}")

        for sub_id in dead_subs:
            self._subscribers.pop(sub_id, None)

        return eid

    def _expire_buffer(self) -> None:
        """Remove events older than TTL from the buffer."""
        now = time.time()
        while self._buffer and (now - self._buffer[0].timestamp) > self._buffer_ttl:
            self._buffer.popleft()

    def _get_events_after(self, last_event_id: str | None) -> list[SSEEvent]:
        """Get buffered events after the given ID (for reconnection replay)."""
        if not last_event_id:
            return []

        self._expire_buffer()

        # Find the event with matching ID, then return everything after it
        found = False
        replay: list[SSEEvent] = []
        for event in self._buffer:
            if found:
                replay.append(event)
            elif event.id == last_event_id:
                found = True

        if not found:
            # ID not in buffer — client is too far behind, send all buffered events
            logger.info(
                f"SSE replay: last_event_id={last_event_id} not in buffer, "
                f"sending all {len(self._buffer)} buffered events"
            )
            return list(self._buffer)

        return replay

    async def _event_generator(
        self,
        last_event_id: str | None = None,
        event_filter: set[str] | None = None,
    ) -> AsyncGenerator[dict[str, str], None]:
        """Generate SSE events for a single client connection.

        Args:
            last_event_id: Last-Event-ID from reconnection header.
            event_filter: Optional set of event types to include.
        """
        sub_id = str(uuid.uuid4())[:12]
        queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue(maxsize=100)
        self._subscribers[sub_id] = queue

        logger.debug(f"SSE client connected: sub_id={sub_id} filter={event_filter}")

        try:
            # Replay missed events on reconnection
            replay_events = self._get_events_after(last_event_id)
            for event in replay_events:
                if event_filter and event.event_type not in event_filter:
                    continue
                yield event.to_sse_dict()

            # Stream live events
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=self._keepalive_seconds,
                    )
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield {"comment": "keepalive"}
                    continue

                if event is None:
                    # Shutdown signal
                    break

                if event_filter and event.event_type not in event_filter:
                    continue

                yield event.to_sse_dict()

        finally:
            self._subscribers.pop(sub_id, None)
            logger.debug(f"SSE client disconnected: sub_id={sub_id}")

    def create_response(
        self,
        request: Request,
        event_filter: set[str] | None = None,
    ) -> EventSourceResponse:
        """Create a FastAPI SSE response for a client.

        Args:
            request: The incoming HTTP request (for Last-Event-ID header).
            event_filter: Optional set of event types to stream.

        Returns:
            EventSourceResponse that can be returned from a FastAPI route.
        """
        last_event_id = request.headers.get("Last-Event-ID")

        return EventSourceResponse(
            self._event_generator(
                last_event_id=last_event_id,
                event_filter=event_filter,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @property
    def subscriber_count(self) -> int:
        """Number of active SSE subscribers."""
        return len(self._subscribers)

    @property
    def buffer_size(self) -> int:
        """Number of events in the buffer."""
        return len(self._buffer)

    async def shutdown(self) -> None:
        """Gracefully shut down all subscriber streams."""
        for sub_id, queue in list(self._subscribers.items()):
            try:
                queue.put_nowait(None)  # Shutdown signal
            except asyncio.QueueFull:
                pass
        self._subscribers.clear()
        logger.info("SSE transport shut down")


# Module-level singleton
_default_transport: SSETransport | None = None


def get_sse_transport() -> SSETransport:
    """Get or create the module-level SSETransport singleton."""
    global _default_transport
    if _default_transport is None:
        _default_transport = SSETransport()
    return _default_transport
