"""Tests for Telegram webhook channel adapter."""

from unittest.mock import AsyncMock, MagicMock

from src.server.services.channels.telegram_service import TelegramChannelService


def test_handle_message_records_external_request():
    external = MagicMock()
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-001"}}))
    approvals = MagicMock()

    service = TelegramChannelService(
        approval_service=approvals,
        external_request_service=external,
    )

    ok, result = __import__("asyncio").run(
        service.handle_webhook(
            {
                "update_id": 123,
                "message": {
                    "message_id": 77,
                    "text": "Need a summary of current blockers",
                    "chat": {"id": 999},
                    "from": {"id": 55, "username": "owner"},
                },
            }
        )
    )

    assert ok is True
    assert result["status"] == "recorded"
    call = external.create_request.await_args.kwargs
    assert call["source_channel"] == "telegram"
    assert call["correlation_id"] == "telegram-update:123"
    assert call["materialize_as"] == "none"


def test_handle_callback_query_decides_approval():
    approvals = MagicMock()
    approvals.decide_request = AsyncMock(return_value=(True, {"approval": {"id": "apr-001", "status": "approved"}}))
    external = MagicMock()

    service = TelegramChannelService(
        approval_service=approvals,
        external_request_service=external,
    )
    service._answer_callback_query = AsyncMock()

    ok, result = __import__("asyncio").run(
        service.handle_webhook(
            {
                "callback_query": {
                    "id": "cbq-001",
                    "data": "approval:approve:apr-001",
                    "from": {"id": 55, "username": "owner"},
                }
            }
        )
    )

    assert ok is True
    assert result["approval"]["status"] == "approved"
    approvals.decide_request.assert_awaited_once()
    kwargs = approvals.decide_request.await_args.kwargs
    assert kwargs["decision"] == "approve"
    assert kwargs["decided_by"] == "telegram:owner"
    service._answer_callback_query.assert_awaited_once()


def test_handle_webhook_rejects_invalid_secret():
    service = TelegramChannelService(
        approval_service=MagicMock(),
        external_request_service=MagicMock(),
    )
    service.webhook_secret = "expected-secret"

    ok, result = __import__("asyncio").run(
        service.handle_webhook({"message": {"text": "hello"}}, secret_token="wrong-secret")
    )

    assert ok is False
    assert result["error"] == "Invalid Telegram webhook secret"


def test_handle_batch_callback_query_decides_multiple_approvals():
    approvals = MagicMock()
    approvals.decide_requests = AsyncMock(
        return_value=(True, {"approvals": [{"id": "apr-001"}, {"id": "apr-002"}], "processed_count": 2, "failed_count": 0, "failed_ids": []})
    )
    approvals.decide_request = AsyncMock()
    external = MagicMock()

    service = TelegramChannelService(
        approval_service=approvals,
        external_request_service=external,
    )
    service._answer_callback_query = AsyncMock()

    ok, result = __import__("asyncio").run(
        service.handle_webhook(
            {
                "callback_query": {
                    "id": "cbq-002",
                    "data": "approval-batch:approve:apr-001,apr-002",
                    "from": {"id": 55, "username": "owner"},
                }
            }
        )
    )

    assert ok is True
    assert result["batch"]["processed_count"] == 2
    approvals.decide_requests.assert_awaited_once()
    kwargs = approvals.decide_requests.await_args.kwargs
    assert kwargs["decision"] == "approve"
    assert kwargs["decided_by"] == "telegram:owner"


def test_handle_threshold_batch_callback_query_decides_multiple_approvals():
    approvals = MagicMock()
    approvals.decide_requests = AsyncMock(
        return_value=(
            True,
            {
                "approvals": [{"id": "apr-001"}, {"id": "apr-002"}],
                "processed_count": 2,
                "failed_count": 0,
                "failed_ids": [],
                "bundle_label": "telegram-threshold-batch",
                "minimum_required": 2,
                "threshold_met": True,
            },
        )
    )
    approvals.decide_request = AsyncMock()

    service = TelegramChannelService(
        approval_service=approvals,
        external_request_service=MagicMock(),
    )
    service._answer_callback_query = AsyncMock()

    ok, result = __import__("asyncio").run(
        service.handle_webhook(
            {
                "callback_query": {
                    "id": "cbq-003",
                    "data": "approval-batch-threshold:approve:2:apr-001,apr-002",
                    "from": {"id": 55, "username": "owner"},
                }
            }
        )
    )

    assert ok is True
    assert result["batch"]["threshold_met"] is True
    kwargs = approvals.decide_requests.await_args.kwargs
    assert kwargs["minimum_required"] == 2
    assert kwargs["bundle_label"] == "telegram-threshold-batch"


def test_build_digest_returns_preview_metadata():
    external = MagicMock()
    external.notifier = MagicMock()
    external.notifier.config = MagicMock(
        telegram_chat_id="chat-1",
        telegram_only_critical=False,
        telegram_notify_events=[],
        telegram_digest_events=["task_review_ready"],
        telegram_digest_limit=10,
    )
    service = TelegramChannelService(
        approval_service=MagicMock(),
        external_request_service=external,
    )

    digest = service.build_digest([
        {
            "event": "task_review_ready",
            "task_id": "task-1",
            "data": {"summary": "Ready"},
            "timestamp": "2026-03-21T10:00:00Z",
        }
    ])

    assert digest["delivery_mode"] == "preview"
    assert digest["ready"] is True
    assert digest["sent"] is False
    assert digest["routed_events"] >= 1


def test_send_digest_marks_sent_delivery_mode():
    approvals = MagicMock()
    external = MagicMock()
    external.notifier = MagicMock()
    external.notifier.config = MagicMock(
        telegram_chat_id="chat-1",
        telegram_only_critical=False,
        telegram_notify_events=[],
        telegram_digest_events=["task_review_ready"],
        telegram_digest_limit=10,
    )
    service = TelegramChannelService(approval_service=approvals, external_request_service=external)
    service.bot_token = "bot-1"

    service._answer_callback_query = AsyncMock()

    async def _run():
        from unittest.mock import patch

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False
        with patch("httpx.AsyncClient", return_value=mock_client):
            return await service.send_digest([
                {
                    "event": "task_review_ready",
                    "task_id": "task-1",
                    "data": {"summary": "Ready"},
                }
            ])

    ok, result = __import__("asyncio").run(_run())

    assert ok is True
    assert result["delivery_mode"] == "send"
    assert result["ready"] is True
    assert result["sent"] is True


def test_send_digest_uses_shared_telegram_channel_transport():
    approvals = MagicMock()
    external = MagicMock()
    external.notifier = MagicMock()
    external.notifier.config = MagicMock(
        telegram_chat_id="chat-1",
        telegram_only_critical=False,
        telegram_notify_events=[],
        telegram_digest_events=["task_review_ready"],
        telegram_digest_limit=10,
    )
    channel = MagicMock()
    channel.configure = MagicMock()
    channel.send_text = AsyncMock(return_value=(True, {"payload": {"chat_id": "chat-1"}}))
    service = TelegramChannelService(
        approval_service=approvals,
        external_request_service=external,
        telegram_channel=channel,
    )
    service.bot_token = "bot-1"

    ok, result = __import__("asyncio").run(
        service.send_digest([
            {
                "event": "task_review_ready",
                "task_id": "task-1",
                "data": {"summary": "Ready"},
            }
        ])
    )

    assert ok is True
    channel.send_text.assert_awaited_once()
    assert result["delivery_mode"] == "send"


def test_telegram_health_reports_readiness_and_issues():
    external = MagicMock()
    external.notifier = MagicMock()
    external.notifier.config = MagicMock(
        telegram_chat_id="",
        telegram_notify_events=["task_failed"],
        telegram_digest_events=["task_review_ready"],
    )
    service = TelegramChannelService(
        approval_service=MagicMock(),
        external_request_service=external,
    )
    service.bot_token = ""
    service.webhook_secret = ""

    health = service.get_health_status()

    assert health["status"] == "degraded"
    assert health["send_ready"] is False
    assert health["digest_ready"] is False
    assert "bot-token-missing" in health["issues"]
    assert "chat-id-missing" in health["issues"]
    assert "webhook-secret-missing" in health["issues"]


def test_send_due_digest_skips_when_not_due():
    external = MagicMock()
    external.notifier = MagicMock()
    external.notifier.config = MagicMock(
        telegram_chat_id="chat-1",
        telegram_only_critical=False,
        telegram_notify_events=[],
        telegram_digest_events=["task_review_ready"],
        telegram_digest_limit=10,
    )
    external.create_request = AsyncMock()
    service = TelegramChannelService(
        approval_service=MagicMock(),
        external_request_service=external,
    )
    service.bot_token = "bot-1"
    service.webhook_secret = "secret"
    service.digest_hour = 9
    service.digest_minute = 0
    service.digest_timezone = "UTC"

    ok, result = __import__("asyncio").run(
        service.send_due_digest(
            [{"event": "task_review_ready", "task_id": "task-1", "data": {}}],
            now_override="2026-03-21T08:30:00+00:00",
        )
    )

    assert ok is True
    assert result["delivery_mode"] == "scheduled-skip"
    assert result["skipped_reason"] == "not-due"
    external.create_request.assert_not_awaited()


def test_send_due_digest_deduplicates_already_sent_window():
    external = MagicMock()
    external.notifier = MagicMock()
    external.notifier.config = MagicMock(
        telegram_chat_id="chat-1",
        telegram_only_critical=False,
        telegram_notify_events=[],
        telegram_digest_events=["task_review_ready"],
        telegram_digest_limit=10,
    )
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-1"}, "deduplicated": True}))
    service = TelegramChannelService(
        approval_service=MagicMock(),
        external_request_service=external,
    )
    service.bot_token = "bot-1"
    service.webhook_secret = "secret"
    service.digest_hour = 9
    service.digest_minute = 0
    service.digest_timezone = "UTC"

    ok, result = __import__("asyncio").run(
        service.send_due_digest(
            [{"event": "task_review_ready", "task_id": "task-1", "data": {}}],
            now_override="2026-03-21T09:00:00+00:00",
        )
    )

    assert ok is True
    assert result["skipped_reason"] == "already-sent"
    assert result["sent"] is False


def test_send_due_digest_sends_when_due_and_not_yet_sent():
    approvals = MagicMock()
    external = MagicMock()
    external.notifier = MagicMock()
    external.notifier.config = MagicMock(
        telegram_chat_id="chat-1",
        telegram_only_critical=False,
        telegram_notify_events=[],
        telegram_digest_events=["task_review_ready"],
        telegram_digest_limit=10,
    )
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-2"}}))
    service = TelegramChannelService(approval_service=approvals, external_request_service=external)
    service.bot_token = "bot-1"
    service.webhook_secret = "secret"
    service.digest_hour = 9
    service.digest_minute = 0
    service.digest_timezone = "UTC"

    async def _run():
        from unittest.mock import patch

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False
        with patch("httpx.AsyncClient", return_value=mock_client):
            return await service.send_due_digest(
                [{"event": "task_review_ready", "task_id": "task-1", "data": {}}],
                now_override="2026-03-21T09:00:00+00:00",
            )

    ok, result = __import__("asyncio").run(_run())

    assert ok is True
    assert result["delivery_mode"] == "scheduled-send"
    assert result["sent"] is True
    assert result["due"] is True
    assert result["digest_key"] == "telegram-digest:2026-03-21"
    assert result["authority_scope"] == "summary-only"
    assert result["delivery_ok"] is True
    assert result["job_status"] == "sent"


def test_send_due_digest_fallback_delivery_failure_is_best_effort_by_default():
    approvals = MagicMock()
    external = MagicMock()
    external.notifier = MagicMock()
    external.notifier.config = MagicMock(
        telegram_chat_id="chat-1",
        telegram_only_critical=False,
        telegram_notify_events=[],
        telegram_digest_events=["task_review_ready"],
        telegram_digest_limit=10,
    )
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-3"}}))
    service = TelegramChannelService(approval_service=approvals, external_request_service=external)
    service.bot_token = "bot-1"
    service.webhook_secret = "secret"
    service.digest_hour = 9
    service.digest_minute = 0
    service.digest_timezone = "UTC"
    service.send_digest = AsyncMock(return_value=(False, {"error": "telegram-down"}))

    ok, result = __import__("asyncio").run(
        service.send_due_digest(
            [{"event": "task_review_ready", "task_id": "task-1", "data": {}}],
            now_override="2026-03-21T09:00:00+00:00",
            scheduler_origin="fallback",
            delivery_required=False,
        )
    )

    assert ok is True
    assert result["scheduler_origin"] == "fallback"
    assert result["authority_scope"] == "summary-only"
    assert result["delivery_ok"] is False
    assert result["job_status"] == "completed-with-delivery-failure"
    assert result["sent"] is False


def test_send_due_digest_fails_closed_when_delivery_is_required():
    approvals = MagicMock()
    external = MagicMock()
    external.notifier = MagicMock()
    external.notifier.config = MagicMock(
        telegram_chat_id="chat-1",
        telegram_only_critical=False,
        telegram_notify_events=[],
        telegram_digest_events=["task_review_ready"],
        telegram_digest_limit=10,
    )
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-4"}}))
    service = TelegramChannelService(approval_service=approvals, external_request_service=external)
    service.bot_token = "bot-1"
    service.webhook_secret = "secret"
    service.digest_hour = 9
    service.digest_minute = 0
    service.digest_timezone = "UTC"
    service.send_digest = AsyncMock(return_value=(False, {"error": "telegram-down"}))

    ok, result = __import__("asyncio").run(
        service.send_due_digest(
            [{"event": "task_review_ready", "task_id": "task-1", "data": {}}],
            now_override="2026-03-21T09:00:00+00:00",
            scheduler_origin="fallback",
            delivery_required=True,
        )
    )

    assert ok is False
    assert result["scheduler_origin"] == "fallback"
    assert result["authority_scope"] == "summary-only"
    assert result["delivery_ok"] is False
    assert result["job_status"] == "delivery-failed"


def test_run_due_digest_cycle_collects_replay_events_and_sends():
    external = MagicMock()
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-digest-3"}}))
    external.notifier = MagicMock()
    external.notifier.config = MagicMock(
        telegram_chat_id="chat-1",
        telegram_only_critical=False,
        telegram_notify_events=["task_failed"],
        telegram_digest_events=["task_review_ready", "task_failed"],
        telegram_digest_limit=10,
    )
    external.supabase_client = MagicMock()
    digest_table = MagicMock()
    digest_select = MagicMock()
    digest_select.eq.return_value = digest_select
    digest_select.order.return_value = digest_select
    digest_select.limit.return_value = digest_select
    digest_select.execute.return_value = MagicMock(data=[])
    digest_table.select.return_value = digest_select
    external.supabase_client.table.return_value = digest_table

    service = TelegramChannelService(
        approval_service=MagicMock(),
        external_request_service=external,
    )
    service.bot_token = "bot-1"
    service.webhook_secret = "secret"
    service.digest_timezone = "UTC"

    async def _run():
        from unittest.mock import patch

        replay_response = MagicMock()
        replay_response.raise_for_status = MagicMock()
        replay_response.json.return_value = {
            "events": [
                {
                    "id": "evt-1",
                    "event": "task_review_ready",
                    "taskId": "task-1",
                    "timestamp": "2026-03-21T09:00:00+00:00",
                    "data": {"summary": "Ready"},
                }
            ],
            "filters_applied": "limit=25",
        }
        send_client = AsyncMock()
        send_client.post = AsyncMock(return_value=MagicMock())
        send_client.__aenter__.return_value = send_client
        send_client.__aexit__.return_value = False

        replay_client = AsyncMock()
        replay_client.get = AsyncMock(return_value=replay_response)
        replay_client.__aenter__.return_value = replay_client
        replay_client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", side_effect=[replay_client, send_client]):
            return await service.run_due_digest_cycle(
                force=True,
                now_override="2026-03-21T09:00:00+00:00",
                limit=25,
            )

    ok, result = __import__("asyncio").run(_run())

    assert ok is True
    assert result["scheduler_mode"] == "observability-replay"
    assert result["authority_scope"] == "summary-only"
    assert result["delivery_mode"] == "scheduled-send"
    assert result["sent"] is True
    assert result["digest_key"] == "telegram-digest:2026-03-21"
    external.create_request.assert_awaited_once()
