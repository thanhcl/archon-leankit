"""Background health monitor that alerts on channel degradation and recovery."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from ...config.env_aliases import (
    get_channel_health_alert_dedupe_seconds,
    get_channel_health_check_interval_seconds,
    get_channel_health_monitor_enabled,
    get_telegram_bot_token,
    get_telegram_chat_id,
)
from ...config.logfire_config import get_logger
from .channel_health_service import ExternalChannelHealthService
from .telegram_channel import TelegramChannel

logger = get_logger(__name__)

_DEGRADED = "degraded"
_READY = "ready"


def _format_degradation_alert(degraded_keys: list[str], issues: list[str]) -> str:
    """Format a Telegram degradation alert message."""
    channels_str = ", ".join(degraded_keys) if degraded_keys else "unknown"
    lines = [
        "🔴 *Channel Health Alert*",
        f"Degraded channels: {channels_str}",
    ]
    if issues:
        lines.append("Issues:")
        for issue in issues[:10]:
            lines.append(f"  • {issue}")
    return "\n".join(lines)


def _format_recovery_alert(ready_keys: list[str]) -> str:
    """Format a Telegram all-clear recovery message."""
    channels_str = ", ".join(ready_keys) if ready_keys else "all"
    return f"✅ *Channel Health Recovered*\nAll channels ready: {channels_str}"


class ChannelHealthMonitor:
    """Poll channel health on a schedule and alert via Telegram on state transitions.

    - Sends a degradation alert the first time health drops.
    - Re-sends degradation alert if the dedupe window has elapsed.
    - Sends an all-clear when health recovers from degraded to ready.
    - Silently skips duplicate alerts within the dedupe window.
    """

    def __init__(
        self,
        *,
        health_service: ExternalChannelHealthService | None = None,
        telegram_channel: TelegramChannel | None = None,
        check_interval_seconds: int | None = None,
        alert_dedupe_seconds: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._health_service = health_service or ExternalChannelHealthService()
        self._telegram = telegram_channel or TelegramChannel(
            bot_token=get_telegram_bot_token() or "",
            default_chat_id=get_telegram_chat_id() or "",
        )
        self._check_interval = check_interval_seconds if check_interval_seconds is not None else get_channel_health_check_interval_seconds()
        self._dedupe_seconds = alert_dedupe_seconds if alert_dedupe_seconds is not None else get_channel_health_alert_dedupe_seconds()
        self._enabled = enabled if enabled is not None else get_channel_health_monitor_enabled()

        # State tracking
        self._last_overall_status: str = _READY
        self._last_alert_at: datetime | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Launch the background polling loop."""
        if not self._enabled:
            logger.info("channel_health_monitor.disabled — skipping startup")
            return
        if self._task is not None and not self._task.done():
            logger.warning("channel_health_monitor.already_running")
            return
        self._task = asyncio.create_task(self._run(), name="channel-health-monitor")
        logger.info(
            "channel_health_monitor.started check_interval=%ds dedupe=%ds",
            self._check_interval,
            self._dedupe_seconds,
        )

    async def stop(self) -> None:
        """Cancel the background polling loop and wait for it to finish."""
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        logger.info("channel_health_monitor.stopped")

    async def _run(self) -> None:
        """Inner polling loop — runs until cancelled."""
        while True:
            try:
                await self._check_once()
            except Exception:
                logger.error("channel_health_monitor.check_error", exc_info=True)
            await asyncio.sleep(self._check_interval)

    async def _check_once(self) -> None:
        """Run one health check cycle and send alerts if needed."""
        heartbeat: dict[str, Any] = self._health_service.get_heartbeat()
        current_status: str = str(heartbeat.get("status", _DEGRADED))
        degraded_keys: list[str] = list(heartbeat.get("degraded_channel_keys", []))
        ready_keys: list[str] = list(heartbeat.get("ready_channel_keys", []))
        issues: list[str] = list(heartbeat.get("issues", []))

        if current_status == _DEGRADED:
            await self._handle_degraded(degraded_keys, issues)
        elif current_status == _READY and self._last_overall_status == _DEGRADED:
            await self._handle_recovered(ready_keys)

        self._last_overall_status = current_status

    async def _handle_degraded(self, degraded_keys: list[str], issues: list[str]) -> None:
        """Send degradation alert if not suppressed by dedupe window."""
        now = datetime.now(timezone.utc)
        if self._last_alert_at is not None:
            elapsed = (now - self._last_alert_at).total_seconds()
            if elapsed < self._dedupe_seconds:
                logger.debug(
                    "channel_health_monitor.alert_suppressed elapsed=%ds dedupe=%ds",
                    int(elapsed),
                    self._dedupe_seconds,
                )
                return

        text = _format_degradation_alert(degraded_keys, issues)
        ok, _ = await self._telegram.send_text(text)
        if ok:
            self._last_alert_at = now
            logger.warning(
                "channel_health_monitor.degradation_alert_sent channels=%s issues=%s",
                degraded_keys,
                issues,
            )
        else:
            logger.error("channel_health_monitor.alert_send_failed channels=%s", degraded_keys)

    async def _handle_recovered(self, ready_keys: list[str]) -> None:
        """Send all-clear notification on recovery."""
        text = _format_recovery_alert(ready_keys)
        ok, _ = await self._telegram.send_text(text)
        if ok:
            self._last_alert_at = None
            logger.info("channel_health_monitor.recovery_alert_sent channels=%s", ready_keys)
        else:
            logger.error("channel_health_monitor.recovery_send_failed channels=%s", ready_keys)
