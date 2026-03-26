"""Notification fan-out adapter shared by notifier and channel services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import httpx

from ...config.logfire_config import get_logger
from .notification_formatter import NotificationFormatter
from .notification_policy import NotificationPolicy

if TYPE_CHECKING:
    from ..engine.notifier import TaskEvent
    from .notification_channel import NotificationChannel

logger = get_logger(__name__)


class NotificationAdapter:
    """Fan out canonical notification events to configured outbound channels."""

    def __init__(
        self,
        *,
        telegram_policy: NotificationPolicy | None = None,
        telegram_channel: NotificationChannel | None = None,
        telegram_payload_builder: Callable[[TaskEvent], dict[str, Any]] | None = None,
        telegram_digest_limit: int = 10,
        discord_webhook_url: str = "",
        discord_only_critical: bool = True,
    ) -> None:
        self.telegram_policy = telegram_policy
        self.telegram_channel = telegram_channel
        self.telegram_payload_builder = telegram_payload_builder
        self.telegram_digest_limit = telegram_digest_limit
        self.discord_webhook_url = discord_webhook_url
        self.discord_only_critical = discord_only_critical

    async def deliver_event(
        self,
        evt: TaskEvent,
        *,
        enabled_channels: set[str] | None = None,
    ) -> dict[str, Any]:
        """Deliver one event to the enabled outbound channels with best-effort semantics."""
        channels = enabled_channels or {"telegram", "discord"}
        channel_results: dict[str, dict[str, Any]] = {}

        if "telegram" in channels:
            channel_results["telegram"] = await self._deliver_telegram(evt)
        if "discord" in channels:
            channel_results["discord"] = await self._deliver_discord(evt)

        return {
            "event": evt.event,
            "task_id": evt.task_id,
            "all_ok": all(result.get("ok", False) for result in channel_results.values()) if channel_results else True,
            "channels": channel_results,
        }

    def build_telegram_digest(
        self,
        events: list[TaskEvent],
        *,
        digest_limit: int | None = None,
    ) -> dict[str, Any]:
        """Build the canonical Telegram digest preview payload."""
        limit = int(digest_limit if digest_limit is not None else self.telegram_digest_limit)
        if self.telegram_policy is None:
            text = NotificationFormatter.build_telegram_daily_digest(
                events,
                digest_limit=limit,
                delivery_classifier=lambda _evt: "immediate",
            )
            routed_events = len(events)
        else:
            text = NotificationFormatter.build_telegram_daily_digest(
                events,
                digest_limit=limit,
                delivery_classifier=self.telegram_policy.classify_telegram_delivery,
            )
            routed_events = len([evt for evt in events if self.telegram_policy.is_telegram_routed(evt)])

        return {
            "text": text,
            "total_events": len(events),
            "routed_events": routed_events,
            "delivery_mode": "preview",
            "ready": True,
            "sent": False,
        }

    async def send_telegram_digest(
        self,
        events: list[TaskEvent],
        *,
        digest_limit: int | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Send the canonical Telegram digest through the shared outbound channel."""
        digest = self.build_telegram_digest(events, digest_limit=digest_limit)
        if self.telegram_channel is None:
            return False, {"error": "Telegram channel is not configured", **digest}

        try:
            ok, result = await self.telegram_channel.send_text(digest["text"], parse_mode="Markdown")
            channel_result = {
                "channel": "telegram",
                "attempted": True,
                "ok": bool(ok),
                "mode": "digest",
                "skipped": False,
            }
            if not ok:
                channel_result["error"] = result.get("error", "telegram-send-failed")
                return False, {
                    "error": channel_result["error"],
                    **digest,
                    "channels": {"telegram": channel_result},
                }

            digest["sent"] = True
            digest["delivery_mode"] = "send"
            digest["channels"] = {"telegram": channel_result}
            return True, digest
        except Exception as exc:
            logger.warning(f"Telegram digest send failed: {exc}")
            return False, {
                "error": str(exc),
                **digest,
                "channels": {
                    "telegram": {
                        "channel": "telegram",
                        "attempted": True,
                        "ok": False,
                        "mode": "digest",
                        "skipped": False,
                        "error": str(exc),
                    }
                },
            }

    async def _deliver_telegram(self, evt: TaskEvent) -> dict[str, Any]:
        """Deliver one event to Telegram when the routing policy allows it."""
        if self.telegram_policy is None or self.telegram_channel is None or self.telegram_payload_builder is None:
            return {
                "channel": "telegram",
                "attempted": False,
                "ok": True,
                "mode": "disabled",
                "skipped": True,
                "reason": "telegram-not-configured",
            }

        mode = self.telegram_policy.classify_telegram_delivery(evt)
        if mode != "immediate":
            return {
                "channel": "telegram",
                "attempted": False,
                "ok": True,
                "mode": mode,
                "skipped": True,
                "reason": "routed-to-digest" if mode == "digest" else "skipped-by-policy",
            }

        try:
            payload = self.telegram_payload_builder(evt)
            ok, result = await self.telegram_channel.send_payload(payload)
            response = {
                "channel": "telegram",
                "attempted": True,
                "ok": bool(ok),
                "mode": mode,
                "skipped": False,
            }
            if not ok:
                response["error"] = result.get("error", "telegram-send-failed")
            return response
        except Exception as exc:
            logger.warning(f"Telegram send failed: {exc}")
            return {
                "channel": "telegram",
                "attempted": True,
                "ok": False,
                "mode": mode,
                "skipped": False,
                "error": str(exc),
            }

    async def _deliver_discord(self, evt: TaskEvent) -> dict[str, Any]:
        """Deliver one event to Discord when configured and allowed by policy."""
        if not self.discord_webhook_url:
            return {
                "channel": "discord",
                "attempted": False,
                "ok": True,
                "mode": "disabled",
                "skipped": True,
                "reason": "discord-not-configured",
            }
        if self.discord_only_critical and not evt.is_critical:
            return {
                "channel": "discord",
                "attempted": False,
                "ok": True,
                "mode": "skip",
                "skipped": True,
                "reason": "discord-critical-only",
            }

        try:
            text = NotificationFormatter.format_discord(evt)
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(self.discord_webhook_url, json={"content": text})
            return {
                "channel": "discord",
                "attempted": True,
                "ok": True,
                "mode": "immediate",
                "skipped": False,
            }
        except Exception as exc:
            logger.warning(f"Discord send failed: {exc}")
            return {
                "channel": "discord",
                "attempted": True,
                "ok": False,
                "mode": "immediate",
                "skipped": False,
                "error": str(exc),
            }
