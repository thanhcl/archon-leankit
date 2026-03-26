"""Scheduler-safe summary delivery orchestration for notification channels."""

from __future__ import annotations

from typing import Any

from .telegram_service import TelegramChannelService


class NotificationSchedulerService:
    """Run summary-only notification jobs without elevating them into workflow authority."""

    def __init__(self, telegram_service: TelegramChannelService | None = None) -> None:
        self.telegram_service = telegram_service or TelegramChannelService()

    async def run_telegram_due_digest(
        self,
        *,
        events: list[dict[str, Any]] | None = None,
        force: bool = False,
        now_override: str | None = None,
        limit: int = 25,
        collect_mode: str = "caller-supplied",
        scheduler_origin: str = "primary",
        delivery_required: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """Run one summary-only Telegram digest job using either supplied or replay-collected events."""
        if collect_mode == "observability-replay":
            collected_ok, collected = await self.telegram_service.collect_due_digest_events(limit=limit)
            if not collected_ok:
                return False, {
                    "scheduler_mode": "observability-replay",
                    "scheduler_origin": scheduler_origin,
                    "authority_scope": "summary-only",
                    "job_status": "collection-failed",
                    "delivery_ok": False,
                    **collected,
                }
            return await self.telegram_service.send_due_digest(
                collected["events"],
                force=force,
                now_override=now_override,
                scheduler_origin=scheduler_origin,
                delivery_required=delivery_required,
                scheduler_mode="observability-replay",
            )

        return await self.telegram_service.send_due_digest(
            events or [],
            force=force,
            now_override=now_override,
            scheduler_origin=scheduler_origin,
            delivery_required=delivery_required,
            scheduler_mode="caller-supplied",
        )
