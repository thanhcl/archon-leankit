"""
Notifier for LeanKit V3 Task Engine.

Pushes task lifecycle events via WebSocket broadcast and optional
external channels (Telegram, Discord).

Usage:
    notifier = Notifier()
    await notifier.emit("task_started", task_id="t-1", data={...})
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from ...config.logfire_config import get_logger

logger = get_logger(__name__)


@dataclass
class NotifierConfig:
    """Notification channel configuration."""

    websocket_url: str = "ws://localhost:4000"
    telegram_chat_id: str = ""
    telegram_bot_token: str = ""
    telegram_only_critical: bool = False
    discord_webhook_url: str = ""
    discord_only_critical: bool = True


@dataclass
class TaskEvent:
    """A task lifecycle event."""

    event: str
    task_id: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    is_critical: bool = False

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "task_event",
            "event": self.event,
            "task_id": self.task_id,
            "data": self.data,
            "timestamp": self.timestamp,
            "is_critical": self.is_critical,
        }


# Event name constants
EVENT_TASK_STARTED = "task_started"
EVENT_TASK_COMPLETED = "task_completed"
EVENT_TASK_REVIEW_READY = "task_review_ready"
EVENT_TASK_ESCALATED = "task_escalated"
EVENT_TASK_DONE = "task_done"
EVENT_TASK_FAILED = "task_failed"
EVENT_HEALTH_ALERT = "health_alert"
EVENT_LEARNING_PROMOTED = "learning_promoted"
EVENT_LEARNING_PATTERN = "learning_pattern_detected"
EVENT_PATTERN_PROMOTED = "code_pattern_promoted"

_CRITICAL_EVENTS = frozenset({EVENT_TASK_ESCALATED, EVENT_TASK_FAILED, EVENT_HEALTH_ALERT})


class Notifier:
    """Pushes task events to configured channels."""

    def __init__(self, config: NotifierConfig | None = None):
        self.config = config or NotifierConfig()
        self._event_log: list[TaskEvent] = []

    # ── Public API ────────────────────────────────────────────────────

    async def emit(
        self,
        event: str,
        task_id: str = "",
        data: dict[str, Any] | None = None,
        is_critical: bool | None = None,
    ) -> None:
        """Emit a task event to all configured channels."""
        if is_critical is None:
            is_critical = event in _CRITICAL_EVENTS

        evt = TaskEvent(
            event=event,
            task_id=task_id,
            data=data or {},
            is_critical=is_critical,
        )
        self._event_log.append(evt)

        logger.info(f"Event emitted | event={event} | task_id={task_id} | critical={is_critical}")

        # Fire to all channels concurrently (best-effort)
        await self._send_websocket(evt)

        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            if not self.config.telegram_only_critical or is_critical:
                await self._send_telegram(evt)

        if self.config.discord_webhook_url:
            if not self.config.discord_only_critical or is_critical:
                await self._send_discord(evt)

    # ── Convenience methods ───────────────────────────────────────────

    async def on_task_started(self, task: dict[str, Any]) -> None:
        await self.emit(EVENT_TASK_STARTED, task.get("id", ""), {
            "title": task.get("title"),
            "priority": task.get("priority"),
            "assignee": task.get("assignee"),
        })

    async def on_task_completed(self, task_id: str, result: dict[str, Any]) -> None:
        await self.emit(EVENT_TASK_COMPLETED, task_id, {
            "result": result.get("result"),
            "files_changed": result.get("files_changed"),
            "duration_seconds": result.get("duration_seconds"),
        })

    async def on_task_review_ready(self, task_id: str, review: dict[str, Any]) -> None:
        await self.emit(EVENT_TASK_REVIEW_READY, task_id, {
            "verdict": review.get("verdict"),
            "confidence": review.get("confidence"),
            "summary": review.get("summary"),
        })

    async def on_task_escalated(self, task_id: str, reason: str) -> None:
        await self.emit(EVENT_TASK_ESCALATED, task_id, {"reason": reason}, is_critical=True)

    async def on_task_done(self, task_id: str) -> None:
        await self.emit(EVENT_TASK_DONE, task_id)

    async def on_task_failed(self, task_id: str, error: str) -> None:
        await self.emit(EVENT_TASK_FAILED, task_id, {"error": error}, is_critical=True)

    async def on_health_alert(self, alert: dict[str, Any]) -> None:
        await self.emit(EVENT_HEALTH_ALERT, "", alert, is_critical=True)

    async def on_learning_promoted(self, learning: dict[str, Any]) -> None:
        await self.emit(EVENT_LEARNING_PROMOTED, "", {
            "id": learning.get("id"),
            "suggested_rule": learning.get("suggested_rule"),
            "recurrence_count": learning.get("recurrence_count"),
            "type": learning.get("type"),
            "area": learning.get("area"),
        })

    async def on_learning_pattern_detected(self, learning: dict[str, Any]) -> None:
        await self.emit(EVENT_LEARNING_PATTERN, "", {
            "id": learning.get("id"),
            "description": learning.get("description"),
            "recurrence_count": learning.get("recurrence_count"),
            "type": learning.get("type"),
        })

    async def on_pattern_promoted(self, pattern: dict[str, Any]) -> None:
        await self.emit(EVENT_PATTERN_PROMOTED, "", {
            "id": pattern.get("id"),
            "pattern_name": pattern.get("pattern_name"),
            "category": pattern.get("category"),
            "usage_count": pattern.get("usage_count"),
        })

    # ── Channel implementations ───────────────────────────────────────

    async def _send_websocket(self, evt: TaskEvent) -> None:
        """Send event via HTTP POST to observability server (WebSocket bridge)."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                # POST to HTTP endpoint that broadcasts to WebSocket clients
                url = self.config.websocket_url.replace("ws://", "http://").replace("wss://", "https://")
                await client.post(f"{url}/events", json=evt.to_dict())
        except Exception as e:
            logger.debug(f"WebSocket send failed (non-fatal): {e}")

    async def _send_telegram(self, evt: TaskEvent) -> None:
        """Send event to Telegram chat."""
        try:
            text = self._format_telegram(evt)
            url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(url, json={
                    "chat_id": self.config.telegram_chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                })
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    async def _send_discord(self, evt: TaskEvent) -> None:
        """Send event to Discord webhook."""
        try:
            text = self._format_discord(evt)
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(self.config.discord_webhook_url, json={"content": text})
        except Exception as e:
            logger.warning(f"Discord send failed: {e}")

    # ── Formatters ────────────────────────────────────────────────────

    @staticmethod
    def _format_telegram(evt: TaskEvent) -> str:
        icon = "🔴" if evt.is_critical else "🔵"
        lines = [f"{icon} *{evt.event}*"]
        if evt.task_id:
            lines.append(f"Task: `{evt.task_id}`")
        for k, v in evt.data.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    @staticmethod
    def _format_discord(evt: TaskEvent) -> str:
        icon = "🔴" if evt.is_critical else "🔵"
        lines = [f"{icon} **{evt.event}**"]
        if evt.task_id:
            lines.append(f"Task: `{evt.task_id}`")
        for k, v in evt.data.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    @property
    def event_log(self) -> list[TaskEvent]:
        """Access recorded events (for testing / debugging)."""
        return list(self._event_log)
