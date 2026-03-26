"""Tests for Notifier — event emission, channels, formatting."""

from unittest.mock import AsyncMock, patch

import pytest

from src.server.services.engine.notifier import (
    EVENT_AGENT_STATUS,
    EVENT_APPROVAL_DECIDED,
    EVENT_APPROVAL_REQUESTED,
    EVENT_EXTERNAL_REQUEST_CREATED,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_ESCALATED,
    EVENT_TASK_FAILED,
    EVENT_TASK_REVIEW_READY,
    EVENT_TASK_STARTED,
    Notifier,
    NotifierConfig,
    TaskEvent,
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
    await notifier.on_task_started({
        "id": "t-1",
        "project_id": "proj-1",
        "source_app": "proj-app",
        "title": "Auth",
        "priority": "high",
        "assignee": "Agent",
    })
    assert notifier.event_log[0].event == EVENT_TASK_STARTED
    assert notifier.event_log[0].data["title"] == "Auth"
    assert notifier.event_log[0].data["project_id"] == "proj-1"
    assert notifier.event_log[0].data["source_app"] == "proj-app"


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
    notifier.telegram_channel.send_payload = AsyncMock(return_value=(True, {"payload": {}}))

    # Non-critical event
    await notifier.emit(EVENT_TASK_STARTED, task_id="t-1")
    notifier.telegram_channel.send_payload.assert_not_called()

    # Critical event
    await notifier.emit(EVENT_TASK_ESCALATED, task_id="t-2")
    notifier.telegram_channel.send_payload.assert_awaited_once()


@pytest.mark.asyncio
async def test_discord_only_critical():
    config = NotifierConfig(
        discord_webhook_url="https://discord.com/api/webhooks/test",
        discord_only_critical=True,
    )
    notifier = Notifier(config=config)

    with patch("httpx.AsyncClient") as MC:
        mc = _mock_httpx()
        MC.return_value = mc
        await notifier.emit(EVENT_TASK_COMPLETED, task_id="t-1")
        discord_calls = [c for c in mc.post.call_args_list if "discord.com/api/webhooks/test" in str(c)]
        assert len(discord_calls) == 0

        await notifier.emit(EVENT_TASK_FAILED, task_id="t-2")
        discord_calls = [c for c in mc.post.call_args_list if "discord.com/api/webhooks/test" in str(c)]
        assert len(discord_calls) == 1


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


@pytest.mark.asyncio
async def test_emit_records_notification_adapter_delivery_results():
    notifier = Notifier(
        config=NotifierConfig(
            telegram_bot_token="bot123",
            telegram_chat_id="chat456",
        )
    )
    notifier.notification_adapter.deliver_event = AsyncMock(return_value={
        "event": EVENT_TASK_STARTED,
        "task_id": "t-1",
        "all_ok": True,
        "channels": {
            "telegram": {"channel": "telegram", "attempted": True, "ok": True, "mode": "immediate", "skipped": False},
            "discord": {"channel": "discord", "attempted": False, "ok": True, "mode": "disabled", "skipped": True},
        },
    })

    await notifier.emit(EVENT_TASK_STARTED, task_id="t-1")

    notifier.notification_adapter.deliver_event.assert_awaited_once()
    assert notifier.delivery_results[-1]["all_ok"] is True
    assert notifier.delivery_results[-1]["channels"]["telegram"]["mode"] == "immediate"


# ── Observability channel tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_observability_sends_unified_event():
    """Observability channel sends UnifiedEvent schema to configured URL."""
    import json

    config = NotifierConfig(observability_url="http://obs:4000/api/events")
    notifier = Notifier(config=config, source_app="my-app")

    with patch("src.server.services.engine.notifier.urllib.request.urlopen") as mock_urlopen:
        await notifier.emit(EVENT_TASK_STARTED, task_id="t-1", data={
            "title": "Auth",
            "source_app": "project-x",
            "project_id": "proj-1",
            "session_id": "sess-123",
            "execution_run_id": "run-123",
            "tags": ["project-bootstrap", "bootstrap-plan:plan-123"],
        })

        assert mock_urlopen.call_count >= 1
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://obs:4000/api/events"
        assert req.get_method() == "POST"
        assert req.get_header("Content-type") == "application/json"

        payload = json.loads(req.data.decode("utf-8"))
        assert payload["type"] == "task"
        assert payload["event"] == "task_started"
        assert payload["source"] == "task-engine"
        assert payload["sourceApp"] == "project-x"
        assert payload["taskId"] == "t-1"
        assert payload["runId"] == "sess-123"
        assert payload["executionRunId"] == "run-123"
        assert payload["bootstrapPlanId"] == "plan-123"
        assert payload["data"]["title"] == "Auth"
        assert payload["data"]["execution_run_id"] == "run-123"
        assert payload["data"]["bootstrap_plan_id"] == "plan-123"
        assert "id" in payload
        assert "timestamp" in payload


@pytest.mark.asyncio
async def test_observability_url_from_env():
    """OBSERVABILITY_URL env var configures the URL."""
    with patch.dict("os.environ", {"OBSERVABILITY_URL": "http://custom:9000/events"}):
        config = NotifierConfig()
    assert config.observability_url == "http://custom:9000/events"


@pytest.mark.asyncio
async def test_observability_url_prefers_platform_alias():
    """LEANKIT_OBSERVABILITY_INGEST_URL should override the legacy env name."""
    with patch.dict(
        "os.environ",
        {
            "LEANKIT_OBSERVABILITY_INGEST_URL": "http://platform:9000/api/events",
            "OBSERVABILITY_URL": "http://legacy:9000/events",
        },
        clear=True,
    ):
        config = NotifierConfig()
    assert config.observability_url == "http://platform:9000/api/events"


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


@pytest.mark.asyncio
async def test_on_agent_status_runtime_context_maps_to_unified_event():
    """Agent status sends agentId/runId via observability payload."""
    import json

    notifier = Notifier(config=NotifierConfig(observability_url="http://obs:4000/api/events"))

    with patch("src.server.services.engine.notifier.urllib.request.urlopen") as mock_urlopen:
        await notifier.on_agent_status(
            task_id="t-2",
            agent_id="agent-xyz",
            stream_event={"event": "assistant", "message": "Working"},
            runtime={
                "source_app": "project-y",
                "session_id": "sess-456",
                "execution_run_id": "run-456",
                "stage": "execute",
            },
        )

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["sourceApp"] == "project-y"
        assert payload["runId"] == "sess-456"
        assert payload["agentId"] == "agent-xyz"
        assert payload["data"]["execution_run_id"] == "run-456"


@pytest.mark.asyncio
async def test_observability_sends_external_event_type():
    """External ingress notifications should be normalized as type=external."""
    import json

    notifier = Notifier(config=NotifierConfig(observability_url="http://obs:4000/api/events"))

    with patch("src.server.services.engine.notifier.urllib.request.urlopen") as mock_urlopen:
        await notifier.emit(
            EVENT_EXTERNAL_REQUEST_CREATED,
            data={
                "external_request_id": "ext-001",
                "project_id": "proj-001",
                "source_channel": "telegram",
            },
        )

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["type"] == "external"
        assert payload["event"] == EVENT_EXTERNAL_REQUEST_CREATED
        assert payload["projectId"] == "proj-001"


@pytest.mark.asyncio
async def test_observability_sends_approval_event_type():
    """Approval notifications should be normalized as type=approval."""
    import json

    notifier = Notifier(config=NotifierConfig(observability_url="http://obs:4000/api/events"))

    with patch("src.server.services.engine.notifier.urllib.request.urlopen") as mock_urlopen:
        await notifier.emit(
            EVENT_APPROVAL_REQUESTED,
            task_id="task-001",
            data={
                "approval_request_id": "apr-001",
                "project_id": "proj-001",
                "execution_run_id": "run-001",
            },
            is_critical=True,
        )

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["type"] == "approval"
        assert payload["event"] == EVENT_APPROVAL_REQUESTED
        assert payload["taskId"] == "task-001"
        assert payload["executionRunId"] == "run-001"


@pytest.mark.asyncio
async def test_approval_decision_can_be_marked_critical():
    """Approval decisions stay visible as critical operator events."""
    notifier = Notifier()
    await notifier.emit(EVENT_APPROVAL_DECIDED, data={"approval_request_id": "apr-001"}, is_critical=True)
    assert notifier.event_log[0].is_critical is True


@pytest.mark.asyncio
async def test_approval_requested_telegram_payload_contains_inline_actions():
    """Approval notifications should include inline Approve/Reject buttons for Telegram."""
    config = NotifierConfig(
        observability_url="http://obs:4000/api/events",
        telegram_bot_token="bot123",
        telegram_chat_id="chat456",
    )
    notifier = Notifier(config=config)

    payload = notifier._build_telegram_payload(notifier.event_log[0]) if notifier.event_log else None
    assert payload is None

    await notifier.emit(
        EVENT_APPROVAL_REQUESTED,
        data={"approval_request_id": "apr-001"},
        is_critical=True,
    )
    telegram_payload = notifier._build_telegram_payload(notifier.event_log[0])
    inline = telegram_payload["reply_markup"]["inline_keyboard"][0]
    assert inline[0]["callback_data"] == "approval:approve:apr-001"
    assert inline[1]["callback_data"] == "approval:reject:apr-001"


@pytest.mark.asyncio
async def test_telegram_notify_events_allow_non_critical_review_ready():
    config = NotifierConfig(
        telegram_bot_token="bot123",
        telegram_chat_id="chat456",
        telegram_only_critical=True,
        telegram_notify_events=["task_review_ready"],
    )
    notifier = Notifier(config=config)
    notifier.telegram_channel.send_payload = AsyncMock(return_value=(True, {"payload": {}}))

    await notifier.on_task_review_ready("task-001", {"summary": "Ready for owner review"})

    notifier.telegram_channel.send_payload.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_formatters_render_compact_review_and_error_text():
    notifier = Notifier()
    await notifier.on_task_review_ready("task-001", {"summary": "Ready for owner review", "verdict": "APPROVE"})
    await notifier.on_task_failed("task-002", "Timeout while running tests")

    review_text = notifier._format_telegram(notifier.event_log[0])
    failed_text = notifier._format_telegram(notifier.event_log[1])

    assert "Task ready for review: `task-001`" in review_text
    assert "Summary: Ready for owner review" in review_text
    assert "Task failed: `task-002`" in failed_text
    assert "Reason: Timeout while running tests" in failed_text


def test_telegram_delivery_classifies_digest_events():
    config = NotifierConfig(
        telegram_only_critical=True,
        telegram_notify_events=["approval_requested"],
        telegram_digest_events=["task_review_ready"],
    )
    notifier = Notifier(config=config)

    digest_evt = TaskEvent(event=EVENT_TASK_REVIEW_READY, task_id="t-1", data={}, is_critical=False)
    immediate_evt = TaskEvent(event=EVENT_APPROVAL_REQUESTED, task_id="t-2", data={}, is_critical=True)

    assert notifier._classify_telegram_delivery(digest_evt) == "digest"
    assert notifier._classify_telegram_delivery(immediate_evt) == "immediate"


def test_build_telegram_daily_digest_summarizes_recent_events():
    config = NotifierConfig(telegram_digest_limit=5, telegram_digest_events=[EVENT_TASK_REVIEW_READY, EVENT_TASK_FAILED])
    notifier = Notifier(config=config)
    notifier._event_log.extend([
        TaskEvent(event=EVENT_TASK_REVIEW_READY, task_id="t-1", data={"summary": "ready"}, is_critical=False),
        TaskEvent(event=EVENT_TASK_FAILED, task_id="t-2", data={"error": "timeout"}, is_critical=True),
    ])

    digest = notifier.build_telegram_daily_digest()

    assert "LeanKit Daily Digest" in digest
    assert "task_review_ready: 1" in digest
    assert "task_failed: 1" in digest
    assert "Critical: 1" in digest


@pytest.mark.asyncio
async def test_notifier_delegates_telegram_send_to_shared_channel():
    config = NotifierConfig(
        telegram_bot_token="bot123",
        telegram_chat_id="chat456",
    )
    notifier = Notifier(config=config)
    notifier.telegram_channel.send_payload = AsyncMock(return_value=(True, {"payload": {}}))

    await notifier._send_telegram(
        TaskEvent(
            event=EVENT_TASK_FAILED,
            task_id="task-001",
            data={"error": "Timeout while running tests"},
            is_critical=True,
        )
    )

    notifier.telegram_channel.send_payload.assert_awaited_once()
