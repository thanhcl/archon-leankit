"""Telegram channel adapter for inbound webhook handling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from ...config.env_aliases import (
    get_observability_replay_url,
    get_telegram_bot_token,
    get_telegram_digest_hour,
    get_telegram_digest_lookback_minutes,
    get_telegram_digest_minute,
    get_telegram_digest_scheduler_enabled,
    get_telegram_digest_timezone,
    get_telegram_webhook_secret,
)
from ...config.logfire_config import get_logger
from .notification_adapter import NotificationAdapter
from ..engine.notifier import TaskEvent
from .notification_policy import NotificationPolicy
from .telegram_channel import TelegramChannel
from ..projects.approval_request_service import ApprovalRequestService
from ..projects.external_request_service import ExternalRequestService

logger = get_logger(__name__)


class TelegramChannelService:
    """Handle Telegram webhook updates and route them into control-plane APIs."""

    def __init__(
        self,
        approval_service: ApprovalRequestService | None = None,
        external_request_service: ExternalRequestService | None = None,
        telegram_channel: TelegramChannel | None = None,
    ):
        self.approval_service = approval_service or ApprovalRequestService()
        self.external_request_service = external_request_service or ExternalRequestService()
        self.bot_token = get_telegram_bot_token()
        self.webhook_secret = get_telegram_webhook_secret()
        self.digest_hour = get_telegram_digest_hour()
        self.digest_minute = get_telegram_digest_minute()
        self.digest_timezone = get_telegram_digest_timezone()
        self.digest_lookback_minutes = get_telegram_digest_lookback_minutes()
        self.digest_scheduler_enabled = get_telegram_digest_scheduler_enabled()
        self.observability_replay_url = get_observability_replay_url()
        self.telegram_channel = telegram_channel or TelegramChannel(
            bot_token=self.bot_token or "",
            default_chat_id=self._resolve_chat_id(),
        )

    def _resolve_chat_id(self) -> str:
        """Resolve the currently configured Telegram chat id."""
        return str(self.external_request_service.notifier.config.telegram_chat_id or "")

    def _get_outbound_channel(self) -> TelegramChannel:
        """Return the shared outbound Telegram transport configured with current runtime values."""
        self.telegram_channel.configure(
            bot_token=self.bot_token or "",
            default_chat_id=self._resolve_chat_id(),
        )
        return self.telegram_channel

    def _get_delivery_policy(self) -> NotificationPolicy:
        """Return the current Telegram delivery policy derived from notifier runtime config."""
        return NotificationPolicy.from_runtime_config(self.external_request_service.notifier.config)

    def _get_notification_adapter(self) -> NotificationAdapter:
        """Return a shared canonical adapter configured for Telegram digest flows."""
        channel = self._get_outbound_channel()
        return NotificationAdapter(
            telegram_policy=self._get_delivery_policy(),
            telegram_channel=channel,
            telegram_digest_limit=int(self.external_request_service.notifier.config.telegram_digest_limit),
        )

    @staticmethod
    def _coerce_digest_events(events: list[dict[str, Any]]) -> list[TaskEvent]:
        """Normalize digest input items into canonical TaskEvent records."""
        return [
            TaskEvent(
                event=str(item.get("event") or "unknown"),
                task_id=str(item.get("task_id") or ""),
                data=dict(item.get("data") or {}),
                is_critical=bool(item.get("is_critical", False)),
                timestamp=str(item["timestamp"]) if item.get("timestamp") else "",
            )
            for item in events
        ]

    async def handle_webhook(
        self,
        update: dict[str, Any],
        *,
        secret_token: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Process one Telegram webhook update."""
        if self.webhook_secret and secret_token != self.webhook_secret:
            return False, {"error": "Invalid Telegram webhook secret"}

        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            return await self._handle_callback_query(callback_query)

        message = update.get("message")
        if isinstance(message, dict):
            return await self._handle_message(update, message)

        return True, {"status": "ignored", "reason": "Unsupported Telegram update"}

    async def _handle_callback_query(self, callback_query: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        data = callback_query.get("data")
        if not isinstance(data, str) or not (
            data.startswith("approval:")
            or data.startswith("approval-batch:")
            or data.startswith("approval-batch-threshold:")
        ):
            return True, {"status": "ignored", "reason": "Unsupported callback"}

        from_user = callback_query.get("from") if isinstance(callback_query.get("from"), dict) else {}
        decided_by = self._format_actor(from_user)
        comment = f"Telegram callback by {decided_by}"

        if data.startswith("approval-batch:"):
            parts = data.split(":", 2)
            if len(parts) != 3:
                return False, {"error": "Malformed batch callback payload"}
            _, decision, approval_ids = parts
            ok, result = await self.approval_service.decide_requests(
                approval_ids.split(","),
                decision=decision,
                decided_by=decided_by,
                decision_comment=comment,
            )
        elif data.startswith("approval-batch-threshold:"):
            parts = data.split(":", 3)
            if len(parts) != 4:
                return False, {"error": "Malformed threshold batch callback payload"}
            _, decision, minimum_required, approval_ids = parts
            ok, result = await self.approval_service.decide_requests(
                approval_ids.split(","),
                decision=decision,
                decided_by=decided_by,
                decision_comment=comment,
                bundle_label="telegram-threshold-batch",
                minimum_required=int(minimum_required),
            )
        else:
            parts = data.split(":", 2)
            if len(parts) != 3:
                return False, {"error": "Malformed callback payload"}

            _, decision, approval_id = parts
            ok, result = await self.approval_service.decide_request(
                approval_id,
                decision=decision,
                decided_by=decided_by,
                decision_comment=comment,
            )

        callback_id = callback_query.get("id")
        if isinstance(callback_id, str):
            await self._answer_callback_query(
                callback_id,
                "Approved" if decision == "approve" and ok else "Rejected" if ok else "Unable to process",
            )

        if not ok:
            return False, result
        if data.startswith("approval-batch:") or data.startswith("approval-batch-threshold:"):
            return True, {"status": "processed", "batch": result}
        return True, {"status": "processed", "approval": result["approval"]}

    async def _handle_message(self, update: dict[str, Any], message: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return True, {"status": "ignored", "reason": "No text body"}

        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        from_user = message.get("from") if isinstance(message.get("from"), dict) else {}
        correlation_id = f"telegram-update:{update.get('update_id', message.get('message_id', 'unknown'))}"

        ok, result = await self.external_request_service.create_request(
            source_channel="telegram",
            request_type="message",
            title=text.strip()[:80],
            summary=text.strip(),
            materialize_as="none",
            correlation_id=correlation_id,
            actor_id=str(from_user.get("id")) if from_user.get("id") is not None else None,
            actor_display=self._format_actor(from_user),
            payload={
                "chat_id": chat.get("id"),
                "message_id": message.get("message_id"),
                "username": from_user.get("username"),
                "text": text.strip(),
            },
        )
        if not ok:
            return False, result
        return True, {"status": "recorded", "request": result["request"]}

    async def _answer_callback_query(self, callback_query_id: str, text: str) -> None:
        """Acknowledge Telegram callback query best-effort."""
        try:
            channel = self._get_outbound_channel()
            ok, result = await channel.answer_callback_query(callback_query_id, text)
            if not ok:
                logger.warning(f"Failed to answer Telegram callback query: {result.get('error')}")
        except Exception as exc:
            logger.warning(f"Failed to answer Telegram callback query: {exc}")

    def build_digest(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a Telegram digest preview from caller-supplied events."""
        adapter = self._get_notification_adapter()
        task_events = self._coerce_digest_events(events)
        return adapter.build_telegram_digest(task_events)

    async def send_digest(self, events: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
        """Send a Telegram digest message if bot configuration is present."""
        adapter = self._get_notification_adapter()
        task_events = self._coerce_digest_events(events)
        digest = adapter.build_telegram_digest(task_events)
        if not self.bot_token or not self._resolve_chat_id():
            return False, {"error": "Telegram bot token and chat id are required to send digest"}
        return await adapter.send_telegram_digest(task_events)

    async def send_due_digest(
        self,
        events: list[dict[str, Any]],
        *,
        force: bool = False,
        now_override: str | None = None,
        scheduler_origin: str = "primary",
        delivery_required: bool = False,
        scheduler_mode: str = "caller-supplied",
    ) -> tuple[bool, dict[str, Any]]:
        """Send one scheduled digest if the current time is due and not already sent."""
        health = self.get_health_status()
        if not health["digest_ready"]:
            return False, {
                "error": "Telegram digest channel is not ready",
                "scheduler_origin": scheduler_origin,
                "authority_scope": "summary-only",
                "job_status": "preflight-failed",
                "delivery_ok": False,
                **health,
            }

        due_state = self._resolve_due_digest_state(now_override=now_override)
        base = self.build_digest(events)
        base.update({
            "due": due_state["due"],
            "digest_key": due_state["digest_key"],
            "scheduler_origin": scheduler_origin,
            "authority_scope": "summary-only",
            "scheduler_mode": scheduler_mode,
        })
        if not due_state["due"] and not force:
            return True, {
                **base,
                "delivery_mode": "scheduled-skip",
                "ready": True,
                "skipped_reason": "not-due",
                "job_status": "skipped",
                "delivery_ok": False,
                "sent": False,
            }

        created_ok, created_result = await self.external_request_service.create_request(
            source_channel="telegram",
            request_type="command",
            title=f"Telegram digest {due_state['digest_date']}",
            summary="Scheduled Telegram digest delivery",
            materialize_as="none",
            correlation_id=due_state["digest_key"],
            actor_display="telegram-bot",
            source_app="telegram",
            payload={
                "delivery_mode": "scheduled",
                "digest_date": due_state["digest_date"],
                "digest_timezone": self.digest_timezone,
                "digest_event_count": len(events),
                "scheduler_origin": scheduler_origin,
                "authority_scope": "summary-only",
                "delivery_required": delivery_required,
            },
        )
        if not created_ok:
            return False, created_result
        if created_result.get("deduplicated"):
            return True, {
                **base,
                "delivery_mode": "scheduled-skip",
                "ready": True,
                "skipped_reason": "already-sent",
                "job_status": "skipped",
                "delivery_ok": False,
                "sent": False,
            }

        sent_ok, sent_result = await self.send_digest(events)
        if not sent_ok:
            if delivery_required:
                return False, {
                    **sent_result,
                    "scheduler_origin": scheduler_origin,
                    "authority_scope": "summary-only",
                    "scheduler_mode": scheduler_mode,
                    "job_status": "delivery-failed",
                    "delivery_ok": False,
                }
            return True, {
                **base,
                "error": sent_result.get("error", "telegram-digest-send-failed"),
                "delivery_mode": "scheduled-send",
                "ready": True,
                "job_status": "completed-with-delivery-failure",
                "delivery_ok": False,
                "sent": False,
            }
        return True, {
            **sent_result,
            "due": due_state["due"],
            "digest_key": due_state["digest_key"],
            "delivery_mode": "scheduled-send",
            "scheduler_mode": scheduler_mode,
            "scheduler_origin": scheduler_origin,
            "authority_scope": "summary-only",
            "job_status": "sent",
            "delivery_ok": True,
        }

    async def run_due_digest_cycle(
        self,
        *,
        force: bool = False,
        now_override: str | None = None,
        limit: int = 25,
        scheduler_origin: str = "primary",
        delivery_required: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """Collect recent routed events and execute one scheduler-backed due-digest cycle."""
        health = self.get_health_status()
        if not health["digest_scheduler_enabled"]:
            return False, {
                "error": "Telegram digest scheduler is disabled",
                "scheduler_origin": scheduler_origin,
                "authority_scope": "summary-only",
                "job_status": "preflight-failed",
                "delivery_ok": False,
                **health,
            }
        if not health["observability_replay_ready"]:
            return False, {
                "error": "Observability replay endpoint is not configured",
                "scheduler_origin": scheduler_origin,
                "authority_scope": "summary-only",
                "job_status": "preflight-failed",
                "delivery_ok": False,
                **health,
            }

        collected_ok, collected = await self.collect_due_digest_events(limit=limit)
        if not collected_ok:
            return False, collected

        digest_ok, digest_result = await self.send_due_digest(
            collected["events"],
            force=force,
            now_override=now_override,
            scheduler_origin=scheduler_origin,
            delivery_required=delivery_required,
            scheduler_mode="observability-replay",
        )
        if not digest_ok:
            return False, digest_result
        return True, {
            **digest_result,
            "scheduler_mode": digest_result.get("scheduler_mode", "observability-replay"),
            "delivery_mode": digest_result.get("delivery_mode", "scheduled-send"),
        }

    async def collect_due_digest_events(self, *, limit: int = 25) -> tuple[bool, dict[str, Any]]:
        """Collect routed digest events from Observability replay."""
        params = {
            "limit": str(limit),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(self.observability_replay_url, params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning(f"Failed to collect due digest events: {exc}")
            return False, {"error": str(exc)}

        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            return True, {"events": [], "total_count": 0, "filters_applied": "invalid-response"}

        cutoff = self._resolve_digest_cutoff()
        collected: list[dict[str, Any]] = []
        for item in events:
            if not isinstance(item, dict):
                continue
            mapped = self._map_unified_event_to_digest_event(item, cutoff=cutoff)
            if mapped is not None:
                collected.append(mapped)

        return True, {
            "events": collected[-limit:],
            "total_count": len(collected),
            "filters_applied": payload.get("filters_applied") if isinstance(payload, dict) else "limit",
        }

    def get_health_status(self) -> dict[str, Any]:
        """Return readiness snapshot for Telegram operations."""
        config = self.external_request_service.notifier.config
        issues: list[str] = []
        if not self.bot_token:
            issues.append("bot-token-missing")
        if not config.telegram_chat_id:
            issues.append("chat-id-missing")
        if not self.webhook_secret:
            issues.append("webhook-secret-missing")
        try:
            ZoneInfo(self.digest_timezone)
        except ZoneInfoNotFoundError:
            issues.append("digest-timezone-invalid")
        send_ready = not {"bot-token-missing", "chat-id-missing"} & set(issues)
        digest_ready = send_ready and "digest-timezone-invalid" not in issues
        if not self.digest_scheduler_enabled:
            issues.append("digest-scheduler-disabled")
        if not self.observability_replay_url:
            issues.append("observability-replay-missing")
        status = "ready" if not issues else "degraded"
        due_state = self._resolve_due_digest_state()
        last_digest = self._find_latest_digest_request()
        return {
            "bot_configured": bool(self.bot_token),
            "chat_configured": bool(config.telegram_chat_id),
            "webhook_secret_configured": bool(self.webhook_secret),
            "send_ready": send_ready,
            "digest_ready": digest_ready,
            "status": status,
            "issues": issues,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
            "digest_schedule_hour": self.digest_hour,
            "digest_schedule_minute": self.digest_minute,
            "digest_timezone": self.digest_timezone,
            "digest_lookback_minutes": self.digest_lookback_minutes,
            "digest_scheduler_enabled": self.digest_scheduler_enabled,
            "observability_replay_ready": bool(self.observability_replay_url),
            "next_due_at": due_state["next_due_at"],
            "last_digest_sent_at": last_digest.get("updated_at") if last_digest else None,
            "last_digest_key": last_digest.get("correlation_id") if last_digest else None,
            "digest_events": list(config.telegram_digest_events),
            "notify_events": list(config.telegram_notify_events),
        }

    def _resolve_due_digest_state(self, *, now_override: str | None = None) -> dict[str, Any]:
        """Resolve whether the current local time is due for the scheduled digest."""
        try:
            tz = ZoneInfo(self.digest_timezone)
        except ZoneInfoNotFoundError:
            tz = timezone.utc
        if now_override:
            current = datetime.fromisoformat(now_override)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
        else:
            current = datetime.now(timezone.utc)
        local_now = current.astimezone(tz)
        due = local_now.hour == self.digest_hour and local_now.minute == self.digest_minute
        digest_date = local_now.date().isoformat()
        scheduled_today = local_now.replace(hour=self.digest_hour, minute=self.digest_minute, second=0, microsecond=0)
        next_due = scheduled_today
        if local_now > scheduled_today:
            next_due = scheduled_today + timedelta(days=1)
        return {
            "due": due,
            "digest_key": f"telegram-digest:{digest_date}",
            "digest_date": digest_date,
            "next_due_at": next_due.astimezone(timezone.utc).isoformat(),
        }

    def _resolve_digest_cutoff(self) -> datetime:
        """Return the UTC cutoff used to collect replay events for one digest cycle."""
        return datetime.now(timezone.utc) - timedelta(minutes=self.digest_lookback_minutes)

    def _map_unified_event_to_digest_event(
        self,
        item: dict[str, Any],
        *,
        cutoff: datetime,
    ) -> dict[str, Any] | None:
        """Map one unified event into the digest event input shape."""
        timestamp_value = item.get("timestamp")
        event_name = item.get("event")
        if not isinstance(timestamp_value, str) or not isinstance(event_name, str):
            return None

        try:
            parsed_timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed_timestamp.tzinfo is None:
            parsed_timestamp = parsed_timestamp.replace(tzinfo=timezone.utc)
        if parsed_timestamp < cutoff:
            return None

        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        task_id = item.get("taskId")
        is_critical = event_name in {"task_failed", "task_escalated", "health_alert", "budget_exceeded"}
        return {
            "event": event_name,
            "task_id": task_id if isinstance(task_id, str) else "",
            "data": data,
            "is_critical": is_critical,
            "timestamp": parsed_timestamp.isoformat(),
        }

    def _find_latest_digest_request(self) -> dict[str, Any] | None:
        """Return the most recent persisted digest request, if any."""
        try:
            response = (
                self.external_request_service.supabase_client.table("archon_external_requests")
                .select("*")
                .eq("source_channel", "telegram")
                .eq("request_type", "command")
                .order("created_at", desc=True)
                .limit(20)
                .execute()
            )
        except Exception:
            return None

        for candidate in response.data or []:
            if isinstance(candidate, dict):
                correlation_id = candidate.get("correlation_id")
                if isinstance(correlation_id, str) and correlation_id.startswith("telegram-digest:"):
                    return candidate
        return None

    @staticmethod
    def _format_actor(user: dict[str, Any]) -> str:
        username = user.get("username")
        if isinstance(username, str) and username.strip():
            return f"telegram:{username}"
        user_id = user.get("id")
        if user_id is not None:
            return f"telegram:{user_id}"
        return "telegram:unknown"
