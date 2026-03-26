"""Aggregated health snapshots for external channels."""

from __future__ import annotations

from datetime import datetime, timezone

from .openclaw_service import OpenClawChannelService
from .telegram_service import TelegramChannelService


class ExternalChannelHealthService:
    """Aggregate per-channel health into one operator-friendly heartbeat."""

    def __init__(
        self,
        telegram_service: TelegramChannelService | None = None,
        openclaw_service: OpenClawChannelService | None = None,
    ):
        self.telegram_service = telegram_service or TelegramChannelService()
        self.openclaw_service = openclaw_service or OpenClawChannelService()

    def get_heartbeat(self) -> dict[str, object]:
        """Return one additive heartbeat response across supported channels."""
        telegram = self.telegram_service.get_health_status()
        openclaw = self.openclaw_service.get_health_status()

        channels = {
            "telegram": telegram,
            "openclaw": openclaw,
        }
        ready_channel_keys = [name for name, item in channels.items() if item.get("status") == "ready"]
        degraded_channel_keys = [name for name, item in channels.items() if item.get("status") != "ready"]
        issues: list[str] = []
        timestamps: list[datetime] = []

        for name, item in channels.items():
            issues.extend(f"{name}:{issue}" for issue in item.get("issues", []))
            parsed = self._parse_timestamp(item.get("last_checked_at"))
            if parsed is not None:
                timestamps.append(parsed)

        return {
            "status": "ready" if len(degraded_channel_keys) == 0 else "degraded",
            "total_channels": len(channels),
            "ready_channels": len(ready_channel_keys),
            "degraded_channels": len(degraded_channel_keys),
            "ready_channel_keys": ready_channel_keys,
            "degraded_channel_keys": degraded_channel_keys,
            "issues": issues,
            "last_checked_at": max(timestamps).isoformat() if timestamps else datetime.now(timezone.utc).isoformat(),
            "telegram": telegram,
            "openclaw": openclaw,
        }

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
