"""Channel adapters for external ingress and outbound surfaces."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ArchitectRequestService",
    "TelegramChannelService",
    "OpenClawChannelService",
    "TelegramChannel",
    "NotificationAdapter",
    "NotificationSchedulerService",
    "ChannelHealthMonitor",
]


def __getattr__(name: str) -> Any:
    """Lazy-load channel services to avoid package import cycles."""
    if name == "ArchitectRequestService":
        from .architect_request_service import ArchitectRequestService

        return ArchitectRequestService
    if name == "TelegramChannelService":
        from .telegram_service import TelegramChannelService

        return TelegramChannelService
    if name == "OpenClawChannelService":
        from .openclaw_service import OpenClawChannelService

        return OpenClawChannelService
    if name == "TelegramChannel":
        from .telegram_channel import TelegramChannel

        return TelegramChannel
    if name == "NotificationAdapter":
        from .notification_adapter import NotificationAdapter

        return NotificationAdapter
    if name == "NotificationSchedulerService":
        from .notification_scheduler_service import NotificationSchedulerService

        return NotificationSchedulerService
    if name == "ChannelHealthMonitor":
        from .channel_health_monitor import ChannelHealthMonitor

        return ChannelHealthMonitor
    raise AttributeError(name)
