"""Tests for Notifier — event emission, channels, formatting."""

from unittest.mock import AsyncMock, patch

import pytest

from src.server.services.engine.notifier import (
    EVENT_TASK_COMPLETED,
    EVENT_TASK_ESCALATED,
    EVENT_TASK_FAILED,
    EVENT_TASK_STARTED,
    Notifier,
    NotifierConfig,
)


@pytest.mark.asyncio
async def test_emit_records_event():
    notifier = Notifier()
    await notifier.emit("test_event", task_id="t-1", data={"key": "val"})
    assert len(notifier.event_log) == 1
    assert notifier.event_log[0].event == "test_event"
    assert notifier.event_log[0].task_id == "t-1"


@pytest.mark.asyncio
async def test_critical_events_auto_detected():
    notifier = Notifier()
    await notifier.emit(EVENT_TASK_ESCALATED, task_id="t-1")
    await notifier.emit(EVENT_TASK_FAILED, task_id="t-2")
    await notifier.emit(EVENT_TASK_STARTED, task_id="t-3")

    assert notifier.event_log[0].is_critical is True
    assert notifier.event_log[1].is_critical is True
    assert notifier.event_log[2].is_critical is False


@pytest.mark.asyncio
async def test_on_task_started():
    notifier = Notifier()
    await notifier.on_task_started({"id": "t-1", "title": "Auth", "priority": "high", "assignee": "Agent"})
    assert notifier.event_log[0].event == EVENT_TASK_STARTED
    assert notifier.event_log[0].data["title"] == "Auth"


@pytest.mark.asyncio
async def test_on_task_completed():
    notifier = Notifier()
    await notifier.on_task_completed("t-1", {"result": "SUCCESS", "files_changed": 3, "duration_seconds": 45})
    assert notifier.event_log[0].event == EVENT_TASK_COMPLETED
    assert notifier.event_log[0].data["files_changed"] == 3


@pytest.mark.asyncio
async def test_on_task_escalated():
    notifier = Notifier()
    await notifier.on_task_escalated("t-1", "Security issue found")
    assert notifier.event_log[0].is_critical is True
    assert "Security" in notifier.event_log[0].data["reason"]


@pytest.mark.asyncio
async def test_on_task_failed():
    notifier = Notifier()
    await notifier.on_task_failed("t-1", "Timeout")
    assert notifier.event_log[0].is_critical is True


@pytest.mark.asyncio
async def test_websocket_send_called():
    notifier = Notifier()
    with patch("httpx.AsyncClient") as MC:
        mc = AsyncMock()
        mc.post = AsyncMock()
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=False)
        MC.return_value = mc

        await notifier.emit("test", task_id="t-1")
        mc.post.assert_called_once()


@pytest.mark.asyncio
async def test_telegram_only_critical():
    config = NotifierConfig(
        telegram_bot_token="bot123",
        telegram_chat_id="chat456",
        telegram_only_critical=True,
    )
    notifier = Notifier(config=config)

    with patch.object(notifier, "_send_telegram", new_callable=AsyncMock) as mock_tg:
        # Non-critical event
        await notifier.emit(EVENT_TASK_STARTED, task_id="t-1")
        mock_tg.assert_not_called()

        # Critical event
        await notifier.emit(EVENT_TASK_ESCALATED, task_id="t-2")
        mock_tg.assert_called_once()


@pytest.mark.asyncio
async def test_discord_only_critical():
    config = NotifierConfig(
        discord_webhook_url="https://discord.com/api/webhooks/test",
        discord_only_critical=True,
    )
    notifier = Notifier(config=config)

    with patch.object(notifier, "_send_discord", new_callable=AsyncMock) as mock_dc:
        await notifier.emit(EVENT_TASK_COMPLETED, task_id="t-1")
        mock_dc.assert_not_called()

        await notifier.emit(EVENT_TASK_FAILED, task_id="t-2")
        mock_dc.assert_called_once()


@pytest.mark.asyncio
async def test_event_to_dict():
    notifier = Notifier()
    await notifier.emit("test", task_id="t-1", data={"x": 1})
    d = notifier.event_log[0].to_dict()
    assert d["type"] == "task_event"
    assert d["event"] == "test"
    assert d["task_id"] == "t-1"
    assert "timestamp" in d


@pytest.mark.asyncio
async def test_channel_failure_non_fatal():
    """Channel send failure should not raise."""
    notifier = Notifier()
    with patch("httpx.AsyncClient", side_effect=Exception("connection refused")):
        await notifier.emit("test", task_id="t-1")  # Should not raise
    assert len(notifier.event_log) == 1
