"""Tests for Notifier — event emission, channels, formatting."""

from unittest.mock import AsyncMock, patch

import pytest

from src.server.services.engine.notifier import (
    EVENT_AGENT_STATUS,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_ESCALATED,
    EVENT_TASK_FAILED,
    EVENT_TASK_STARTED,
    Notifier,
    NotifierConfig,
)


def _mock_httpx():
    """Create a mock httpx.AsyncClient context manager."""
    mc = AsyncMock()
    mc.post = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    return mc


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
        mc = _mock_httpx()
        MC.return_value = mc

        await notifier.emit("test", task_id="t-1")
        # post called twice: once for websocket, once for observability
        assert mc.post.call_count >= 1
        ws_calls = [c for c in mc.post.call_args_list if "/events" in str(c) and "/api/" not in str(c)]
        assert len(ws_calls) == 1, "WebSocket POST should be called once"


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


# ── Observability channel tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_observability_sends_unified_event():
    """Observability channel sends UnifiedEvent schema to configured URL."""
    import json

    config = NotifierConfig(observability_url="http://obs:4000/api/events")
    notifier = Notifier(config=config, source_app="my-app")

    with patch("src.server.services.engine.notifier.urllib.request.urlopen") as mock_urlopen:
        await notifier.emit(EVENT_TASK_STARTED, task_id="t-1", data={"title": "Auth"})

        assert mock_urlopen.call_count >= 1
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://obs:4000/api/events"
        assert req.get_method() == "POST"
        assert req.get_header("Content-type") == "application/json"

        payload = json.loads(req.data.decode("utf-8"))
        assert payload["type"] == "task"
        assert payload["event"] == "task_started"
        assert payload["source"] == "task-engine"
        assert payload["sourceApp"] == "my-app"
        assert payload["taskId"] == "t-1"
        assert payload["data"]["title"] == "Auth"
        assert "id" in payload
        assert "timestamp" in payload


@pytest.mark.asyncio
async def test_observability_url_from_env():
    """OBSERVABILITY_URL env var configures the URL."""
    with patch.dict("os.environ", {"OBSERVABILITY_URL": "http://custom:9000/events"}):
        config = NotifierConfig()
    assert config.observability_url == "http://custom:9000/events"


@pytest.mark.asyncio
async def test_observability_url_default():
    """Default observability URL is http://localhost:4000/api/events."""
    with patch.dict("os.environ", {}, clear=True):
        config = NotifierConfig()
    assert config.observability_url == "http://localhost:4000/api/events"


@pytest.mark.asyncio
async def test_observability_failure_non_fatal():
    """Observability send failure should not raise or block."""
    notifier = Notifier()
    with patch.object(notifier, "_send_to_observability", side_effect=Exception("timeout")):
        # Patch to actually raise — but emit should still succeed
        # since _send_observability is called directly, we need to test the internal try/except
        pass

    # Test the internal error handling
    with patch("src.server.services.engine.notifier.urllib.request.urlopen", side_effect=Exception("connection refused")):
        await notifier.emit("test", task_id="t-1")  # Should not raise
    assert len(notifier.event_log) == 1


@pytest.mark.asyncio
async def test_observability_timeout_is_2s():
    """Observability client uses 2-second timeout."""
    config = NotifierConfig(observability_url="http://obs:4000/api/events")
    notifier = Notifier(config=config)

    with patch("src.server.services.engine.notifier.urllib.request.urlopen") as mock_urlopen:
        await notifier.emit("test", task_id="t-1")

        assert mock_urlopen.call_count >= 1
        assert mock_urlopen.call_args.kwargs.get("timeout") == 2


@pytest.mark.asyncio
async def test_source_app_default():
    """Notifier defaults source_app to 'unknown'."""
    notifier = Notifier()
    assert notifier.source_app == "unknown"


# ── Agent status event tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_on_agent_status_emits_event():
    """on_agent_status emits an agent_status event with stream data."""
    notifier = Notifier()
    await notifier.on_agent_status(
        task_id="t-1",
        agent_id="agent-abc",
        stream_event={
            "event": "tool_use",
            "tool_name": "Read",
            "args_summary": "/src/main.py",
        },
    )

    assert len(notifier.event_log) == 1
    evt = notifier.event_log[0]
    assert evt.event == EVENT_AGENT_STATUS
    assert evt.task_id == "t-1"
    assert evt.data["agent_id"] == "agent-abc"
    assert evt.data["event"] == "tool_use"
    assert evt.data["tool_name"] == "Read"
    assert evt.data["args_summary"] == "/src/main.py"


@pytest.mark.asyncio
async def test_on_agent_status_assistant_event():
    """on_agent_status handles assistant events correctly."""
    notifier = Notifier()
    await notifier.on_agent_status(
        task_id="t-2",
        agent_id="agent-xyz",
        stream_event={
            "event": "assistant",
            "message": "Working on the implementation",
        },
    )

    evt = notifier.event_log[0]
    assert evt.data["event"] == "assistant"
    assert evt.data["message"] == "Working on the implementation"
    assert evt.data["tool_name"] == ""


@pytest.mark.asyncio
async def test_on_agent_status_not_critical():
    """Agent status events are not critical."""
    notifier = Notifier()
    await notifier.on_agent_status("t-1", "agent-1", {"event": "thinking", "message": "hmm"})
    assert notifier.event_log[0].is_critical is False
