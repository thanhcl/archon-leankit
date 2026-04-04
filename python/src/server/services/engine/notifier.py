"""
Notifier for LeanKit V3 Task Engine.

Pushes task lifecycle events via WebSocket broadcast, Observability server,
and optional external channels (Telegram, Discord).

Usage:
    notifier = Notifier()
    await notifier.emit("task_started", task_id="t-1", data={...})
"""

import json
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from ...config.env_aliases import (
    get_observability_ingest_url,
    get_telegram_bot_token,
    get_telegram_chat_id,
    get_telegram_digest_events,
    get_telegram_digest_limit,
    get_telegram_notify_events,
    get_telegram_only_critical,
)
from ...config.logfire_config import get_logger
from ..channels.notification_adapter import NotificationAdapter
from ..channels.notification_formatter import NotificationFormatter
from ..channels.notification_policy import NotificationPolicy
from ..channels.telegram_channel import TelegramChannel

logger = get_logger(__name__)


@dataclass
class NotifierConfig:
    """Notification channel configuration."""

    websocket_url: str = "ws://localhost:4000"
    observability_url: str = ""
    telegram_chat_id: str = ""
    telegram_bot_token: str = ""
    telegram_only_critical: bool = False
    telegram_notify_events: list[str] = field(default_factory=list)
    telegram_digest_events: list[str] = field(default_factory=list)
    telegram_digest_limit: int = 10
    discord_webhook_url: str = ""
    discord_only_critical: bool = True

    def __post_init__(self):
        if not self.observability_url:
            self.observability_url = get_observability_ingest_url()
        if not self.telegram_bot_token:
            self.telegram_bot_token = get_telegram_bot_token() or ""
        if not self.telegram_chat_id:
            self.telegram_chat_id = get_telegram_chat_id() or ""
        if not self.telegram_only_critical:
            self.telegram_only_critical = get_telegram_only_critical()
        if not self.telegram_notify_events:
            self.telegram_notify_events = get_telegram_notify_events()
        if not self.telegram_digest_events:
            self.telegram_digest_events = get_telegram_digest_events()
        if self.telegram_digest_limit == 10:
            self.telegram_digest_limit = get_telegram_digest_limit()


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
EVENT_CODE_REVIEW_CHANGES = "code_review_changes_requested"
EVENT_BUDGET_WARNING = "budget_warning"
EVENT_BUDGET_EXCEEDED = "budget_exceeded"
EVENT_AGENT_STATUS = "agent_status"
EVENT_EXTERNAL_REQUEST_CREATED = "external_request_created"
EVENT_EXTERNAL_REQUEST_MATERIALIZED = "external_request_materialized"
EVENT_APPROVAL_REQUESTED = "approval_requested"
EVENT_APPROVAL_DECIDED = "approval_decided"
EVENT_TASK_CONTRACT_GATE_BLOCKED = "task_contract_gate_blocked"
EVENT_REVIEW_FEEDBACK_WRITTEN = "review_feedback_written"

_CRITICAL_EVENTS = frozenset({
    EVENT_TASK_ESCALATED,
    EVENT_TASK_FAILED,
    EVENT_HEALTH_ALERT,
    EVENT_BUDGET_EXCEEDED,
})


def _extract_bootstrap_plan_id(data: dict[str, Any]) -> str | None:
    """Resolve bootstrap plan id from task-event payload data."""
    direct = data.get("bootstrap_plan_id") or data.get("bootstrapPlanId")
    if isinstance(direct, str) and direct.strip():
        return direct

    tags = data.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag.startswith("bootstrap-plan:"):
                plan_id = tag.split(":", 1)[1].strip()
                if plan_id:
                    return plan_id
    return None


def _resolve_unified_type(event_name: str) -> str:
    """Map notifier event names to UnifiedEvent type."""
    if event_name in {
        EVENT_HEALTH_ALERT,
        EVENT_BUDGET_WARNING,
        EVENT_BUDGET_EXCEEDED,
    }:
        return "health"
    if event_name in {
        EVENT_LEARNING_PROMOTED,
        EVENT_LEARNING_PATTERN,
        EVENT_PATTERN_PROMOTED,
    }:
        return "learning"
    if event_name in {
        EVENT_EXTERNAL_REQUEST_CREATED,
        EVENT_EXTERNAL_REQUEST_MATERIALIZED,
    }:
        return "external"
    if event_name in {
        EVENT_APPROVAL_REQUESTED,
        EVENT_APPROVAL_DECIDED,
    }:
        return "approval"
    return "task"


class Notifier:
    """Pushes task events to configured channels."""

    def __init__(self, config: NotifierConfig | None = None, source_app: str = "unknown"):
        self.config = config or NotifierConfig()
        self.source_app = source_app
        self._event_log: list[TaskEvent] = []
        self._delivery_results: list[dict[str, Any]] = []
        self.telegram_policy = NotificationPolicy.from_runtime_config(self.config)
        self.telegram_channel = TelegramChannel(
            bot_token=self.config.telegram_bot_token,
            default_chat_id=self.config.telegram_chat_id,
        )
        self.notification_adapter = NotificationAdapter(
            telegram_policy=self.telegram_policy,
            telegram_channel=self.telegram_channel,
            telegram_payload_builder=self._build_telegram_payload,
            telegram_digest_limit=self.config.telegram_digest_limit,
            discord_webhook_url=self.config.discord_webhook_url,
            discord_only_critical=self.config.discord_only_critical,
        )

    @staticmethod
    def _merge_runtime_context(
        data: dict[str, Any] | None,
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Merge event payload with optional runtime context."""
        payload = dict(data or {})
        for key, value in (runtime or {}).items():
            if value is not None:
                payload[key] = value
        return payload

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
        self._send_to_observability(evt)

        delivery_result = await self.notification_adapter.deliver_event(evt)
        self._delivery_results.append(delivery_result)

        # Log channel delivery failures explicitly so fallback decisions are visible
        if not delivery_result.get("all_ok", True):
            for channel, channel_result in delivery_result.get("channels", {}).items():
                if channel_result.get("attempted") and not channel_result.get("ok"):
                    logger.warning(
                        f"Notification channel failed | event={event} | task_id={task_id} | "
                        f"channel={channel} | error={channel_result.get('error', 'unknown')} | "
                        f"fallback=log-only"
                    )

    # ── Convenience methods ───────────────────────────────────────────

    async def on_task_started(self, task: dict[str, Any], runtime: dict[str, Any] | None = None) -> None:
        await self.emit(
            EVENT_TASK_STARTED,
            task.get("id", ""),
            self._merge_runtime_context(
                {
                    "title": task.get("title"),
                    "priority": task.get("priority"),
                    "assignee": task.get("assignee"),
                    "tags": task.get("tags"),
                },
                {
                    "project_id": task.get("project_id"),
                    "source_app": task.get("source_app"),
                    **(runtime or {}),
                },
            ),
        )

    async def on_task_completed(
        self,
        task_id: str,
        result: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> None:
        await self.emit(
            EVENT_TASK_COMPLETED,
            task_id,
            self._merge_runtime_context(
                {
                    "result": result.get("result"),
                    "files_changed": result.get("files_changed"),
                    "duration_seconds": result.get("duration_seconds"),
                },
                runtime,
            ),
        )

    async def on_task_review_ready(
        self,
        task_id: str,
        review: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> None:
        await self.emit(
            EVENT_TASK_REVIEW_READY,
            task_id,
            self._merge_runtime_context(
                {
                    "verdict": review.get("verdict"),
                    "confidence": review.get("confidence"),
                    "summary": review.get("summary"),
                },
                runtime,
            ),
        )

    async def on_task_escalated(
        self,
        task_id: str,
        reason: str,
        runtime: dict[str, Any] | None = None,
    ) -> None:
        await self.emit(
            EVENT_TASK_ESCALATED,
            task_id,
            self._merge_runtime_context({"reason": reason}, runtime),
            is_critical=True,
        )

    async def on_task_done(self, task_id: str) -> None:
        await self.emit(EVENT_TASK_DONE, task_id)

    async def on_task_failed(
        self,
        task_id: str,
        error: str,
        runtime: dict[str, Any] | None = None,
    ) -> None:
        await self.emit(
            EVENT_TASK_FAILED,
            task_id,
            self._merge_runtime_context({"error": error}, runtime),
            is_critical=True,
        )

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

    async def on_code_review_changes_requested(
        self,
        task_id: str,
        review: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> None:
        await self.emit(
            EVENT_CODE_REVIEW_CHANGES,
            task_id,
            self._merge_runtime_context(
                {
                    "verdict": review.get("verdict"),
                    "review_cycle": review.get("review_cycle"),
                    "findings_count": len(review.get("findings", [])),
                },
                runtime,
            ),
        )

    async def on_budget_warning(self, project_id: str, details: dict[str, Any]) -> None:
        await self.emit(EVENT_BUDGET_WARNING, "", {
            "project_id": project_id,
            **details,
        })

    async def on_budget_exceeded(self, project_id: str, reason: str) -> None:
        await self.emit(EVENT_BUDGET_EXCEEDED, "", {
            "project_id": project_id,
            "reason": reason,
        }, is_critical=True)

    async def on_agent_status(
        self,
        task_id: str,
        agent_id: str,
        stream_event: dict,
        runtime: dict[str, Any] | None = None,
    ) -> None:
        """Forward a real-time CC stream event as an agent_status notification.

        Args:
            task_id: Task being executed.
            agent_id: Agent/session identifier.
            stream_event: Parsed stream event from CCSpawner.parse_stream_line().
        """
        await self.emit(
            EVENT_AGENT_STATUS,
            task_id,
            self._merge_runtime_context(
                {
                    "agent_id": agent_id,
                    "event": stream_event.get("event", "unknown"),
                    "message": stream_event.get("message", ""),
                    "tool_name": stream_event.get("tool_name", ""),
                    "args_summary": stream_event.get("args_summary", ""),
                    "output_summary": stream_event.get("output_summary", ""),
                },
                runtime,
            ),
        )

    async def on_external_request_created(self, request_record: dict[str, Any]) -> None:
        """Emit when a new external ingress request is recorded."""
        await self.emit(
            EVENT_EXTERNAL_REQUEST_CREATED,
            request_record.get("task_id", "") or "",
            {
                "external_request_id": request_record.get("id"),
                "project_id": request_record.get("project_id"),
                "execution_run_id": request_record.get("execution_run_id"),
                "bootstrap_plan_id": request_record.get("bootstrap_plan_id"),
                "source_channel": request_record.get("source_channel"),
                "request_type": request_record.get("request_type"),
                "materialize_as": request_record.get("materialize_as"),
                "title": request_record.get("title"),
                "summary": request_record.get("summary"),
                "correlation_id": request_record.get("correlation_id"),
                "source_app": request_record.get("source_app"),
                "actor_id": request_record.get("actor_id"),
                "actor_display": request_record.get("actor_display"),
            },
        )

    async def on_external_request_materialized(self, request_record: dict[str, Any], target: str) -> None:
        """Emit when an external request is materialized into system state."""
        await self.emit(
            EVENT_EXTERNAL_REQUEST_MATERIALIZED,
            request_record.get("linked_task_id") or request_record.get("task_id", "") or "",
            {
                "external_request_id": request_record.get("id"),
                "project_id": request_record.get("project_id"),
                "execution_run_id": request_record.get("execution_run_id"),
                "bootstrap_plan_id": request_record.get("bootstrap_plan_id"),
                "linked_task_id": request_record.get("linked_task_id"),
                "linked_approval_request_id": request_record.get("linked_approval_request_id"),
                "source_channel": request_record.get("source_channel"),
                "request_type": request_record.get("request_type"),
                "materialize_as": request_record.get("materialize_as"),
                "materialized_target": target,
                "correlation_id": request_record.get("correlation_id"),
                "source_app": request_record.get("source_app"),
            },
        )

    async def on_approval_requested(self, approval: dict[str, Any]) -> None:
        """Emit when an approval request is created."""
        await self.emit(
            EVENT_APPROVAL_REQUESTED,
            approval.get("task_id", "") or "",
            {
                "approval_request_id": approval.get("id"),
                "external_request_id": approval.get("external_request_id"),
                "project_id": approval.get("project_id"),
                "execution_run_id": approval.get("execution_run_id"),
                "bootstrap_plan_id": approval.get("bootstrap_plan_id"),
                "requested_by": approval.get("requested_by"),
                "requested_channel": approval.get("requested_channel"),
                "title": approval.get("title"),
                "summary": approval.get("summary"),
                "actor_id": approval.get("actor_id"),
                "actor_display": approval.get("actor_display"),
            },
            is_critical=True,
        )

    async def on_review_feedback_written(self, task_id: str, payload: dict[str, Any]) -> None:
        """Emit when review feedback is persisted to archon_review_feedback."""
        await self.emit(
            EVENT_REVIEW_FEEDBACK_WRITTEN,
            task_id,
            {
                "verdict": payload.get("verdict"),
                "reviewer_identity": payload.get("reviewer_identity"),
                "overall_score": payload.get("overall_score"),
                "stage": payload.get("stage"),
                "contract_revision": payload.get("contract_revision"),
            },
        )

    async def on_approval_decided(self, approval: dict[str, Any], decision: str) -> None:
        """Emit when an approval request receives a decision."""
        await self.emit(
            EVENT_APPROVAL_DECIDED,
            approval.get("task_id", "") or "",
            {
                "approval_request_id": approval.get("id"),
                "external_request_id": approval.get("external_request_id"),
                "project_id": approval.get("project_id"),
                "execution_run_id": approval.get("execution_run_id"),
                "bootstrap_plan_id": approval.get("bootstrap_plan_id"),
                "requested_by": approval.get("requested_by"),
                "requested_channel": approval.get("requested_channel"),
                "decision": decision,
                "status": approval.get("status"),
                "decided_by": approval.get("decided_by"),
                "decision_comment": approval.get("decision_comment"),
            },
            is_critical=True,
        )

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

    def _send_to_observability(self, evt: TaskEvent) -> None:
        """Send UnifiedEvent to Observability server via urllib. Fail-safe, never blocks engine."""
        try:
            source_app = str(evt.data.get("source_app") or self.source_app or "unknown")
            payload = {
                "id": str(uuid.uuid4()),
                "type": _resolve_unified_type(evt.event),
                "event": evt.event,
                "source": "task-engine",
                "sourceApp": source_app,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "taskId": evt.task_id,
                "data": evt.data,
            }
            run_id = evt.data.get("session_id") or evt.data.get("execution_run_id") or evt.data.get("run_id")
            if run_id:
                payload["runId"] = str(run_id)
            execution_run_id = evt.data.get("execution_run_id")
            if execution_run_id:
                payload["executionRunId"] = str(execution_run_id)
            agent_id = evt.data.get("agent_id")
            if agent_id:
                payload["agentId"] = str(agent_id)
            bootstrap_plan_id = _extract_bootstrap_plan_id(evt.data)
            if bootstrap_plan_id:
                payload["bootstrapPlanId"] = bootstrap_plan_id
                payload["data"]["bootstrap_plan_id"] = bootstrap_plan_id
            project_id = evt.data.get("project_id")
            if project_id:
                payload["projectId"] = str(project_id)
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.config.observability_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception as e:
            logger.debug(f"Observability send failed (non-fatal): {e}")

    async def _send_telegram(self, evt: TaskEvent) -> None:
        """Send event to Telegram chat."""
        self.telegram_channel.configure(
            bot_token=self.config.telegram_bot_token,
            default_chat_id=self.config.telegram_chat_id,
        )
        result = await self.notification_adapter.deliver_event(evt, enabled_channels={"telegram"})
        self._delivery_results.append(result)

    def _should_send_telegram(self, evt: TaskEvent) -> bool:
        """Return whether an event should be sent to Telegram."""
        return self.telegram_policy.should_send_telegram(evt)

    def _classify_telegram_delivery(self, evt: TaskEvent) -> str:
        """Classify Telegram delivery mode for one event."""
        return self.telegram_policy.classify_telegram_delivery(evt)

    async def _send_discord(self, evt: TaskEvent) -> None:
        """Send event to Discord webhook."""
        result = await self.notification_adapter.deliver_event(evt, enabled_channels={"discord"})
        self._delivery_results.append(result)

    # ── Formatters ────────────────────────────────────────────────────

    @staticmethod
    def _format_telegram(evt: TaskEvent) -> str:
        return NotificationFormatter.format_telegram(evt)

    def _build_telegram_payload(self, evt: TaskEvent) -> dict[str, Any]:
        """Build Telegram message payload, including inline approval actions when applicable."""
        text = self._format_telegram(evt)
        payload: dict[str, Any] = {
            "chat_id": self.config.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        approval_id = evt.data.get("approval_request_id")
        if evt.event == EVENT_APPROVAL_REQUESTED and isinstance(approval_id, str) and approval_id:
            payload["reply_markup"] = {
                "inline_keyboard": [[
                    {"text": "Approve", "callback_data": f"approval:approve:{approval_id}"},
                    {"text": "Reject", "callback_data": f"approval:reject:{approval_id}"},
                ]]
            }
        return payload

    def build_telegram_daily_digest(self, events: list[TaskEvent] | None = None) -> str:
        """Build a compact Telegram-friendly daily digest from recent events."""
        source_events = events if events is not None else self._event_log
        digest = self.notification_adapter.build_telegram_digest(
            source_events,
            digest_limit=self.config.telegram_digest_limit,
        )
        return str(digest["text"])

    @staticmethod
    def _format_discord(evt: TaskEvent) -> str:
        return NotificationFormatter.format_discord(evt)

    @property
    def event_log(self) -> list[TaskEvent]:
        """Access recorded events (for testing / debugging)."""
        return list(self._event_log)

    @property
    def delivery_results(self) -> list[dict[str, Any]]:
        """Access structured outbound delivery results for testing and debugging."""
        return list(self._delivery_results)
