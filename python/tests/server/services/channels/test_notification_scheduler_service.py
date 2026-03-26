"""Tests for scheduler-safe notification summary orchestration."""

from unittest.mock import AsyncMock, MagicMock

from src.server.services.channels.notification_scheduler_service import NotificationSchedulerService


def test_scheduler_service_uses_replay_collection_for_due_digest_cycle():
    telegram_service = MagicMock()
    telegram_service.collect_due_digest_events = AsyncMock(
        return_value=(True, {"events": [{"event": "task_review_ready", "task_id": "task-1", "data": {}}]})
    )
    telegram_service.send_due_digest = AsyncMock(
        return_value=(True, {"delivery_mode": "scheduled-send", "scheduler_mode": "observability-replay"})
    )
    scheduler = NotificationSchedulerService(telegram_service=telegram_service)

    ok, result = __import__("asyncio").run(
        scheduler.run_telegram_due_digest(
            collect_mode="observability-replay",
            scheduler_origin="fallback",
            delivery_required=False,
            limit=30,
        )
    )

    assert ok is True
    telegram_service.collect_due_digest_events.assert_awaited_once_with(limit=30)
    telegram_service.send_due_digest.assert_awaited_once()
    kwargs = telegram_service.send_due_digest.await_args.kwargs
    assert kwargs["scheduler_origin"] == "fallback"
    assert kwargs["scheduler_mode"] == "observability-replay"
    assert kwargs["delivery_required"] is False
    assert result["scheduler_mode"] == "observability-replay"
