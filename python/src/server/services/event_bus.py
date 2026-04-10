"""
Internal Event Bus for Archon Control Plane.

Lightweight pub/sub for decoupling handlers from side effects.
Listeners react independently: activity logger, notification sender,
WebSocket broadcaster, analytics updater.

Does NOT replace the observability plane (append-only telemetry).
Supplements it for internal concerns like activity logging and notifications.

Adopted from Multica internal/events/ pattern (Workstream M-P4-01).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine
from uuid import uuid4

from ..config.logfire_config import get_logger

logger = get_logger(__name__)

# Type alias for event handlers
EventHandler = Callable[["BusEvent"], Coroutine[Any, Any, None]]


@dataclass
class BusEvent:
    """An internal control-plane event."""

    topic: str
    data: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""
    timestamp: str = ""
    source: str = "control-plane"

    def __post_init__(self) -> None:
        if not self.event_id:
            self.event_id = str(uuid4())
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# ── Event Topics ──────────────────────────────────────────────────────────

# Task lifecycle
TOPIC_TASK_CREATED = "task.created"
TOPIC_TASK_UPDATED = "task.updated"
TOPIC_TASK_ASSIGNED = "task.assigned"
TOPIC_TASK_STATUS_CHANGED = "task.status_changed"
TOPIC_TASK_COMPLETED = "task.completed"
TOPIC_TASK_FAILED = "task.failed"

# Runtime lifecycle
TOPIC_RUNTIME_REGISTERED = "runtime.registered"
TOPIC_RUNTIME_UNREGISTERED = "runtime.unregistered"
TOPIC_RUNTIME_HEARTBEAT = "runtime.heartbeat"
TOPIC_RUNTIME_STATUS_CHANGED = "runtime.status_changed"

# Execution
TOPIC_RUN_STARTED = "run.started"
TOPIC_RUN_COMPLETED = "run.completed"
TOPIC_RUN_FAILED = "run.failed"
TOPIC_RUN_PROGRESS = "run.progress"

# Task claiming
TOPIC_TASK_CLAIMED = "task.claimed"
TOPIC_TASK_RELEASED = "task.released"

# Assignment
TOPIC_ASSIGNMENT_CHANGED = "assignment.changed"

# Learning
TOPIC_LEARNING_CREATED = "learning.created"
TOPIC_LEARNING_PROMOTED = "learning.promoted"

# Project
TOPIC_PROJECT_CREATED = "project.created"

# Comments
TOPIC_COMMENT_CREATED = "comment.created"
TOPIC_MENTION = "mention"

# Wildcard
TOPIC_ALL = "*"


class EventBus:
    """Lightweight async event bus for internal control-plane events.

    Usage:
        bus = EventBus()

        # Subscribe
        bus.on("task.created", my_handler)
        bus.on("*", audit_log_handler)  # subscribe to all events

        # Publish
        await bus.emit("task.created", {"task_id": "t-1", "title": "Fix bug"})
    """

    _instance: EventBus | None = None

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._event_count = 0

    @classmethod
    def get_instance(cls) -> EventBus:
        """Get or create the singleton event bus."""
        if cls._instance is None:
            cls._instance = EventBus()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None

    def on(self, topic: str, handler: EventHandler) -> None:
        """Subscribe a handler to a topic.

        Args:
            topic: Event topic (e.g., "task.created") or "*" for all events.
            handler: Async function that receives a BusEvent.
        """
        self._handlers[topic].append(handler)
        logger.debug(f"Event handler registered: {topic} → {handler.__name__}")

    def off(self, topic: str, handler: EventHandler) -> None:
        """Unsubscribe a handler from a topic."""
        handlers = self._handlers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(
        self,
        topic: str,
        data: dict[str, Any] | None = None,
        source: str = "control-plane",
    ) -> int:
        """Publish an event to all subscribed handlers.

        Args:
            topic: Event topic string.
            data: Event payload.
            source: Origin of the event.

        Returns:
            Number of handlers that received the event.
        """
        event = BusEvent(topic=topic, data=data or {}, source=source)
        self._event_count += 1

        # Collect handlers: topic-specific + wildcard
        handlers = list(self._handlers.get(topic, []))
        if topic != TOPIC_ALL:
            handlers.extend(self._handlers.get(TOPIC_ALL, []))

        if not handlers:
            return 0

        # Fire all handlers concurrently (fire-and-forget with error isolation)
        results = await asyncio.gather(
            *[self._safe_invoke(handler, event) for handler in handlers],
            return_exceptions=True,
        )

        success_count = sum(1 for r in results if r is True)
        return success_count

    async def _safe_invoke(self, handler: EventHandler, event: BusEvent) -> bool:
        """Invoke a handler with error isolation."""
        try:
            await handler(event)
            return True
        except Exception as e:
            logger.error(f"Event handler error: {handler.__name__} topic={event.topic} error={e}", exc_info=True)
            return False

    @property
    def event_count(self) -> int:
        """Total events emitted since creation."""
        return self._event_count

    @property
    def handler_count(self) -> int:
        """Total registered handlers across all topics."""
        return sum(len(h) for h in self._handlers.values())

    def topics(self) -> list[str]:
        """List all topics with registered handlers."""
        return [t for t, h in self._handlers.items() if h]


# ── Built-in Listeners ────────────────────────────────────────────────────


class ActivityLogListener:
    """Logs all events to an activity log table.

    Subscribes to TOPIC_ALL to record every event for workspace activity history.
    Adopted from Multica's activity log pattern (M-P4-02).
    """

    TABLE = "archon_activity_log"

    def __init__(self, supabase_client: Any = None) -> None:
        self._client = supabase_client
        self._buffer: list[dict[str, Any]] = []
        self._buffer_size = 10  # Flush every N events

    async def handle(self, event: BusEvent) -> None:
        """Handle an event by buffering and flushing to the database."""
        record = {
            "event_id": event.event_id,
            "topic": event.topic,
            "source": event.source,
            "timestamp": event.timestamp,
            "data": event.data,
        }
        self._buffer.append(record)

        if len(self._buffer) >= self._buffer_size:
            await self._flush()

    async def _flush(self) -> None:
        """Write buffered events to the activity log table."""
        if not self._buffer:
            return

        if not self._client:
            from ..utils import get_supabase_client
            self._client = get_supabase_client()

        batch = list(self._buffer)
        self._buffer.clear()

        try:
            self._client.table(self.TABLE).insert(batch).execute()
        except Exception as e:
            logger.warning(f"Activity log flush failed: {e}")
            # Re-buffer on failure (bounded to prevent memory leak)
            if len(self._buffer) < 100:
                self._buffer.extend(batch)

    async def flush_remaining(self) -> None:
        """Flush any remaining buffered events (call on shutdown)."""
        await self._flush()
