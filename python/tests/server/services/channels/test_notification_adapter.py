"""Tests for notification fan-out adapter."""

from unittest.mock import AsyncMock, patch

import pytest

from src.server.services.channels.notification_adapter import NotificationAdapter
from src.server.services.channels.notification_policy import NotificationPolicy
from src.server.services.engine.notifier import TaskEvent


@pytest.mark.asyncio
async def test_adapter_sends_immediate_telegram_event():
    channel = AsyncMock()
    channel.send_payload = AsyncMock(return_value=(True, {"payload": {"chat_id": "chat-1"}}))
    adapter = NotificationAdapter(
        telegram_policy=NotificationPolicy(
            telegram_only_critical=True,
            telegram_notify_events=["task_failed"],
            telegram_digest_events=["task_review_ready"],
        ),
        telegram_channel=channel,
        telegram_payload_builder=lambda evt: {"chat_id": "chat-1", "text": evt.event},
    )

    result = await adapter.deliver_event(
        TaskEvent(event="task_failed", task_id="task-1", data={}, is_critical=True)
    )

    channel.send_payload.assert_awaited_once()
    assert result["all_ok"] is True
    assert result["channels"]["telegram"]["attempted"] is True
    assert result["channels"]["telegram"]["mode"] == "immediate"


@pytest.mark.asyncio
async def test_adapter_skips_digest_routed_telegram_event():
    channel = AsyncMock()
    channel.send_payload = AsyncMock(return_value=(True, {"payload": {"chat_id": "chat-1"}}))
    adapter = NotificationAdapter(
        telegram_policy=NotificationPolicy(
            telegram_only_critical=True,
            telegram_notify_events=[],
            telegram_digest_events=["task_review_ready"],
        ),
        telegram_channel=channel,
        telegram_payload_builder=lambda evt: {"chat_id": "chat-1", "text": evt.event},
    )

    result = await adapter.deliver_event(
        TaskEvent(event="task_review_ready", task_id="task-1", data={}, is_critical=False)
    )

    channel.send_payload.assert_not_called()
    assert result["channels"]["telegram"]["skipped"] is True
    assert result["channels"]["telegram"]["mode"] == "digest"


@pytest.mark.asyncio
async def test_adapter_reports_discord_failure_without_raising():
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=Exception("discord-down"))
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    adapter = NotificationAdapter(
        discord_webhook_url="https://discord.com/api/webhooks/test",
        discord_only_critical=True,
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.deliver_event(
            TaskEvent(event="task_failed", task_id="task-1", data={}, is_critical=True),
            enabled_channels={"discord"},
        )

    assert result["all_ok"] is False
    assert result["channels"]["discord"]["attempted"] is True
    assert result["channels"]["discord"]["ok"] is False
    assert "discord-down" in result["channels"]["discord"]["error"]


def test_adapter_builds_canonical_telegram_digest_preview():
    adapter = NotificationAdapter(
        telegram_policy=NotificationPolicy(
            telegram_only_critical=False,
            telegram_notify_events=["task_failed"],
            telegram_digest_events=["task_review_ready"],
        ),
        telegram_digest_limit=5,
    )

    result = adapter.build_telegram_digest([
        TaskEvent(event="task_review_ready", task_id="task-1", data={"summary": "Ready"}, is_critical=False),
        TaskEvent(event="task_failed", task_id="task-2", data={"error": "boom"}, is_critical=True),
    ])

    assert result["delivery_mode"] == "preview"
    assert result["sent"] is False
    assert result["routed_events"] == 2
    assert "LeanKit Daily Digest" in result["text"]


@pytest.mark.asyncio
async def test_adapter_sends_canonical_telegram_digest_through_shared_channel():
    channel = AsyncMock()
    channel.send_text = AsyncMock(return_value=(True, {"payload": {"chat_id": "chat-1"}}))
    adapter = NotificationAdapter(
        telegram_policy=NotificationPolicy(
            telegram_only_critical=False,
            telegram_notify_events=[],
            telegram_digest_events=["task_review_ready"],
        ),
        telegram_channel=channel,
        telegram_digest_limit=5,
    )

    ok, result = await adapter.send_telegram_digest([
        TaskEvent(event="task_review_ready", task_id="task-1", data={"summary": "Ready"}, is_critical=False),
    ])

    assert ok is True
    channel.send_text.assert_awaited_once()
    assert result["delivery_mode"] == "send"
    assert result["sent"] is True
    assert result["channels"]["telegram"]["mode"] == "digest"
