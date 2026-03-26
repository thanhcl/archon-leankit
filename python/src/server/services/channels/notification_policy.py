"""Notification routing policy helpers shared by outbound channels."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..engine.notifier import TaskEvent


class NotificationPolicy:
    """Centralize event routing policy away from transport and event emission."""

    def __init__(
        self,
        *,
        telegram_only_critical: bool = False,
        telegram_notify_events: list[str] | tuple[str, ...] | None = None,
        telegram_digest_events: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.telegram_only_critical = bool(telegram_only_critical)
        self.telegram_notify_events = frozenset(telegram_notify_events or [])
        self.telegram_digest_events = frozenset(telegram_digest_events or [])

    @classmethod
    def from_runtime_config(cls, config: Any) -> "NotificationPolicy":
        """Build a routing policy from a config-like object."""
        return cls(
            telegram_only_critical=bool(getattr(config, "telegram_only_critical", False)),
            telegram_notify_events=list(getattr(config, "telegram_notify_events", []) or []),
            telegram_digest_events=list(getattr(config, "telegram_digest_events", []) or []),
        )

    def classify_telegram_delivery(self, evt: TaskEvent) -> str:
        """Classify Telegram delivery mode for one event."""
        if evt.event in self.telegram_notify_events:
            return "immediate"
        if evt.is_critical:
            return "immediate"
        if evt.event in self.telegram_digest_events:
            return "digest"
        return "immediate" if not self.telegram_only_critical else "skip"

    def should_send_telegram(self, evt: TaskEvent) -> bool:
        """Return whether an event should be delivered immediately to Telegram."""
        return self.classify_telegram_delivery(evt) == "immediate"

    def is_telegram_routed(self, evt: TaskEvent) -> bool:
        """Return whether an event belongs to any Telegram delivery path."""
        return self.classify_telegram_delivery(evt) in {"immediate", "digest"}
