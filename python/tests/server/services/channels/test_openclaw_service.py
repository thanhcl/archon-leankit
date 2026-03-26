"""Tests for OpenClaw ingress channel adapter."""

from unittest.mock import AsyncMock, MagicMock

from src.server.services.channels.openclaw_service import OpenClawChannelService


def test_handle_ingest_records_openclaw_external_request():
    external = MagicMock()
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-open-001"}}))
    service = OpenClawChannelService(external_request_service=external)
    service.ingest_secret = None

    ok, result = __import__("asyncio").run(
        service.handle_ingest(
            {
                "request_type": "task-request",
                "title": "Create auth backlog",
                "summary": "Please create implementation tasks for auth hardening",
                "project_id": "proj-001",
                "materialize_as": "task",
                "request_id": "req-77",
                "model": "local-llm",
                "input_modality": "voice",
            }
        )
    )

    assert ok is True
    assert result["status"] == "recorded"
    kwargs = external.create_request.await_args.kwargs
    assert kwargs["source_channel"] == "openclaw"
    assert kwargs["correlation_id"] == "openclaw:req-77"
    assert kwargs["source_app"] == "openclaw"
    assert kwargs["input_modality"] == "voice"
    assert kwargs["input_text"] == "Please create implementation tasks for auth hardening"
    assert kwargs["payload"]["openclaw_model"] == "local-llm"
    assert kwargs["payload"]["openclaw_dedupe_strategy"] == "request-id"
    assert kwargs["payload"]["input_modality"] == "voice"


def test_handle_ingest_rejects_invalid_secret():
    service = OpenClawChannelService(external_request_service=MagicMock())
    service.ingest_secret = "expected-secret"

    ok, result = __import__("asyncio").run(
        service.handle_ingest(
            {"title": "Status", "summary": "What is the current status?"},
            secret_token="wrong-secret",
        )
    )

    assert ok is False
    assert result["error"] == "Invalid OpenClaw ingest secret"


def test_handle_status_query_returns_snapshots():
    external = MagicMock()
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-open-002"}}))
    task_table = MagicMock()
    task_select = MagicMock()
    task_select.eq.return_value = task_select
    task_select.limit.return_value = task_select
    task_select.execute.return_value = MagicMock(data=[{"id": "task-1", "status": "review"}])
    task_table.select.return_value = task_select

    run_table = MagicMock()
    run_select = MagicMock()
    run_select.eq.return_value = run_select
    run_select.limit.return_value = run_select
    run_select.execute.return_value = MagicMock(data=[{"id": "run-1", "status": "running"}])
    run_table.select.return_value = run_select

    def table_factory(name: str):
        if name == "archon_tasks":
            return task_table
        if name == "archon_execution_runs":
            return run_table
        raise AssertionError(f"Unexpected table {name}")

    external.supabase_client.table.side_effect = table_factory

    approvals = MagicMock()
    approvals.get_request.return_value = (True, {"approval": {"id": "apr-1", "status": "pending"}})
    approvals.list_requests.return_value = (True, {"approvals": [{"id": "apr-2", "status": "pending"}]})

    service = OpenClawChannelService(external_request_service=external, approval_request_service=approvals)
    service.ingest_secret = None

    ok, result = __import__("asyncio").run(
        service.handle_ingest(
            {
                "request_type": "status-query",
                "title": "Status",
                "summary": "What is the current status?",
                "task_id": "task-1",
                "execution_run_id": "run-1",
                "approval_id": "apr-1",
                "project_id": "proj-1",
            }
        )
    )

    assert ok is True
    assert result["snapshots"]["task"]["id"] == "task-1"
    assert result["snapshots"]["execution_run"]["id"] == "run-1"
    assert result["snapshots"]["approval"]["id"] == "apr-1"
    assert result["snapshots"]["pending_approvals"][0]["id"] == "apr-2"


def test_handle_approval_action_decides_approval():
    external = MagicMock()
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-open-003"}}))
    approvals = MagicMock()
    approvals.decide_request = AsyncMock(return_value=(True, {"approval": {"id": "apr-9", "status": "approved"}}))

    service = OpenClawChannelService(external_request_service=external, approval_request_service=approvals)
    service.ingest_secret = None

    ok, result = __import__("asyncio").run(
        service.handle_ingest(
            {
                "request_type": "approval-action",
                "title": "Approve deploy",
                "summary": "Approve deploy from voice flow",
                "approval_id": "apr-9",
                "decision": "approve",
                "actor_display": "owner-mobile",
            }
        )
    )

    assert ok is True
    assert result["approval"]["status"] == "approved"
    approvals.decide_request.assert_awaited_once()
    kwargs = approvals.decide_request.await_args.kwargs
    assert kwargs["decision"] == "approve"
    assert kwargs["decided_by"] == "owner-mobile"


def test_handle_architect_request_returns_structured_plan():
    external = MagicMock()
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-open-004", "project_id": "proj-1", "payload": {"input_modality": "voice", "input_text": "Need an architect plan for auth hardening"}}}))
    external._update_request = MagicMock(return_value={"id": "ext-open-004", "payload": {"architect_plan": {"summary": "plan"}}, "linked_task_id": "task-901"})
    external.materialize_architect_plan = MagicMock(return_value=(True, {"tasks": [{"id": "task-901"}]}))
    external.notifier = MagicMock()
    external.notifier.on_external_request_materialized = AsyncMock()
    approvals = MagicMock()
    architect = MagicMock()
    architect.create_plan = AsyncMock(
        return_value={
            "requested_provider": "chatgpt-codex",
            "resolved_provider": "chatgpt-codex",
            "strategy": "provider-generated",
            "model": "gpt-5.4",
            "summary": "Create a task and continue with implementation planning.",
            "recommended_materialization": "task",
            "suggested_tasks": [{"title": "Create task", "summary": "Create task", "task_type": "feature", "priority": "medium", "complexity": "simple", "tags": []}],
            "suggested_approval": None,
            "metadata": {},
        }
    )

    service = OpenClawChannelService(
        external_request_service=external,
        approval_request_service=approvals,
        architect_request_service=architect,
    )
    service.ingest_secret = None
    service._load_sequence_history = MagicMock(
        return_value=[
            {
                "id": "ext-open-prev",
                "request_type": "message",
                "title": "Initial voice request",
                "summary": "Harden auth flow",
                "step_key": "ingress",
                "input_modality": "voice",
                "status": "materialized",
                "created_at": "2026-03-21T10:00:00Z",
            }
        ]
    )

    ok, result = __import__("asyncio").run(
        service.handle_architect_request(
            {
                "title": "Plan auth hardening",
                "summary": "Need an architect plan for auth hardening",
                "project_id": "proj-1",
                "architect_provider": "chatgpt-codex",
                "input_modality": "voice",
                "command_sequence_id": "seq-77",
                "step_key": "architect-plan",
            }
        )
    )

    assert ok is True
    assert result["status"] == "planned"
    assert result["architect_plan"]["recommended_materialization"] == "task"
    architect.create_plan.assert_awaited_once()
    architect_kwargs = architect.create_plan.await_args.kwargs
    assert architect_kwargs["input_modality"] == "voice"
    assert architect_kwargs["sequence_context"]["sequence_id"] == "seq-77"
    assert architect_kwargs["sequence_context"]["history_count"] == 1
    assert architect_kwargs["sequence_context"]["pending_clarifying_questions"] == []
    external.materialize_architect_plan.assert_called_once()
    external._update_request.assert_called_once()
    external.notifier.on_external_request_materialized.assert_awaited_once()


def test_handle_architect_request_passes_clarification_answers_into_sequence_context():
    external = MagicMock()
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-open-006", "project_id": "proj-1", "payload": {"input_modality": "voice", "input_text": "Answering your question"}}}))
    external._update_request = MagicMock(return_value={"id": "ext-open-006", "payload": {"architect_plan": {"summary": "plan"}, "clarification_response_to_request_id": "ext-open-prev"}})
    external.merge_payload_fields = MagicMock()
    external.materialize_architect_plan = MagicMock(return_value=(True, {"tasks": []}))
    external.notifier = MagicMock()
    external.notifier.on_external_request_materialized = AsyncMock()
    architect = MagicMock()
    architect.create_plan = AsyncMock(
        return_value={
            "requested_provider": "rule-based",
            "resolved_provider": "rule-based",
            "strategy": "rule-based",
            "model": None,
            "summary": "Proceed with task creation.",
            "recommended_materialization": "task",
            "clarifying_questions": [],
            "suggested_tasks": [],
            "suggested_approval": None,
            "metadata": {},
        }
    )

    service = OpenClawChannelService(
        external_request_service=external,
        approval_request_service=MagicMock(),
        architect_request_service=architect,
    )
    service.ingest_secret = None
    service._load_sequence_history = MagicMock(
        return_value=[
            {
                "id": "ext-open-prev",
                "request_type": "architect-request",
                "title": "Plan auth hardening",
                "summary": "Need a plan",
                "step_key": "architect-plan",
                "input_modality": "voice",
                "architect_plan": {
                    "clarifying_questions": [
                        "Do you want LeanKit to decompose this into tasks immediately, or only produce a plan?",
                    ]
                },
                "clarification_answers": [],
                "status": "materialized",
                "created_at": "2026-03-21T10:00:00Z",
            }
        ]
    )

    ok, result = __import__("asyncio").run(
        service.handle_architect_request(
            {
                "title": "Plan auth hardening",
                "summary": "Yes, decompose this into tasks now.",
                "project_id": "proj-1",
                "input_modality": "voice",
                "command_sequence_id": "seq-88",
                "step_key": "clarification-answer",
                "clarification_answers": ["Yes, decompose this into tasks now."],
                "clarification_response_to_request_id": "ext-open-prev",
            }
        )
    )

    assert ok is True
    architect_kwargs = architect.create_plan.await_args.kwargs
    assert architect_kwargs["clarification_answers"] == ["Yes, decompose this into tasks now."]
    assert architect_kwargs["sequence_context"]["pending_clarifying_request_id"] == "ext-open-prev"
    assert architect_kwargs["sequence_context"]["pending_clarifying_questions"] == [
        "Do you want LeanKit to decompose this into tasks immediately, or only produce a plan?",
    ]
    assert result["request"]["payload"]["clarification_response_to_request_id"] == "ext-open-prev"
    external.merge_payload_fields.assert_called_once()
    merge_args = external.merge_payload_fields.call_args.args
    merge_kwargs = external.merge_payload_fields.call_args.kwargs
    assert merge_args[0] == "ext-open-prev"
    assert merge_args[1]["clarification_status"] == "resolved"
    assert merge_args[1]["clarification_resolved_by_request_id"] == "ext-open-006"
    assert merge_args[1]["clarification_resolution_step_key"] == "clarification-answer"
    assert merge_args[1]["clarification_answers"] == ["Yes, decompose this into tasks now."]
    assert merge_kwargs == {}


def test_semantic_correlation_ignores_volatile_payload_fields():
    first = OpenClawChannelService._semantic_correlation_id(
        {
            "request_type": "message",
            "title": "  Create   Auth Backlog ",
            "summary": "Need plan for auth hardening",
            "project_id": "proj-1",
            "payload": {
                "voice_transcript": "create auth backlog",
                "timestamp": "2026-03-21T10:00:00Z",
                "retry_count": 1,
            },
        }
        ,
        replay_window_minutes=30,
        now=__import__("datetime").datetime(2026, 3, 21, 10, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    second = OpenClawChannelService._semantic_correlation_id(
        {
            "request_type": "message",
            "title": "create auth backlog",
            "summary": "Need   plan for auth hardening",
            "project_id": "proj-1",
            "payload": {
                "voice_transcript": "create auth backlog",
                "timestamp": "2026-03-21T10:00:07Z",
                "retry_count": 2,
            },
        }
        ,
        replay_window_minutes=30,
        now=__import__("datetime").datetime(2026, 3, 21, 10, 5, tzinfo=__import__("datetime").timezone.utc),
    )

    assert first == second


def test_handle_ingest_returns_deduplicated_status_when_request_already_exists():
    external = MagicMock()
    external.create_request = AsyncMock(
        return_value=(
            True,
            {
                "request": {"id": "ext-open-005", "payload": {}, "deduplicated": True},
                "deduplicated": True,
            },
        )
    )
    service = OpenClawChannelService(external_request_service=external)
    service.ingest_secret = None

    ok, result = __import__("asyncio").run(
        service.handle_ingest(
            {
                "title": "Status",
                "summary": "What is the current status?",
                "payload": {"voice_transcript": "status now", "timestamp": "2026-03-21T10:00:00Z"},
            }
        )
    )

    assert ok is True
    assert result["status"] == "deduplicated"
    assert result["request"]["dedupe_strategy"] == "semantic-window-v3"


def test_openclaw_health_reports_degraded_without_secret():
    service = OpenClawChannelService(external_request_service=MagicMock())
    service.ingest_secret = None

    health = service.get_health_status()

    assert health["status"] == "degraded"
    assert health["ingest_ready"] is False
    assert "ingest-secret-missing" in health["issues"]
    assert health["replay_guard_strategy"] == "semantic-window-v3"


def test_semantic_correlation_changes_across_replay_windows():
    first = OpenClawChannelService._semantic_correlation_id(
        {
            "request_type": "message",
            "title": "create auth backlog",
            "summary": "Need plan for auth hardening",
        },
        replay_window_minutes=30,
        now=__import__("datetime").datetime(2026, 3, 21, 10, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    second = OpenClawChannelService._semantic_correlation_id(
        {
            "request_type": "message",
            "title": "create auth backlog",
            "summary": "Need plan for auth hardening",
        },
        replay_window_minutes=30,
        now=__import__("datetime").datetime(2026, 3, 21, 10, 45, tzinfo=__import__("datetime").timezone.utc),
    )

    assert first != second


def test_sequence_correlation_differs_by_step_key():
    first = OpenClawChannelService._sequence_correlation_id(
        "seq-1",
        "capture-intent",
        request_type="command",
        replay_window_minutes=30,
        now=__import__("datetime").datetime(2026, 3, 21, 10, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    second = OpenClawChannelService._sequence_correlation_id(
        "seq-1",
        "materialize-task",
        request_type="command",
        replay_window_minutes=30,
        now=__import__("datetime").datetime(2026, 3, 21, 10, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert first != second


def test_handle_ingest_rejects_conflicting_sequence_step_type():
    external = MagicMock()
    history_table = MagicMock()
    history_select = MagicMock()
    history_select.eq.return_value = history_select
    history_select.order.return_value = history_select
    history_select.limit.return_value = history_select
    history_select.execute.return_value = MagicMock(data=[
        {
            "id": "ext-prev",
            "request_type": "status-query",
            "payload": {
                "openclaw_command_sequence_id": "seq-1",
                "openclaw_step_key": "review-status",
            },
            "status": "received",
        }
    ])
    history_table.select.return_value = history_select
    external.supabase_client.table.return_value = history_table
    external.create_request = AsyncMock()

    service = OpenClawChannelService(external_request_service=external)
    service.ingest_secret = None

    ok, result = __import__("asyncio").run(
        service.handle_ingest(
            {
                "request_type": "architect-request",
                "title": "Plan next step",
                "summary": "Need architecture plan",
                "command_sequence_id": "seq-1",
                "step_key": "review-status",
            }
        )
    )

    assert ok is False
    assert "already used" in result["error"]
    external.create_request.assert_not_awaited()


def test_handle_ingest_attaches_sequence_snapshot_to_payload():
    external = MagicMock()
    history_table = MagicMock()
    history_select = MagicMock()
    history_select.eq.return_value = history_select
    history_select.order.return_value = history_select
    history_select.limit.return_value = history_select
    history_select.execute.return_value = MagicMock(data=[
        {
            "id": "ext-prev",
            "request_type": "status-query",
            "payload": {
                "openclaw_command_sequence_id": "seq-2",
                "openclaw_step_key": "status-check",
            },
            "status": "received",
        }
    ])
    history_table.select.return_value = history_select
    external.supabase_client.table.return_value = history_table
    external.create_request = AsyncMock(return_value=(True, {"request": {"id": "ext-open-006"}}))

    service = OpenClawChannelService(external_request_service=external)
    service.ingest_secret = None

    ok, result = __import__("asyncio").run(
        service.handle_ingest(
            {
                "request_type": "architect-request",
                "title": "Plan next step",
                "summary": "Need architecture plan",
                "command_sequence_id": "seq-2",
                "step_key": "architect-plan",
            }
        )
    )

    assert ok is True
    kwargs = external.create_request.await_args.kwargs
    snapshot = kwargs["payload"]["openclaw_sequence_snapshot"]
    assert snapshot["history_count"] == 1
    assert snapshot["step_key"] == "architect-plan"
    assert "approval-action" in snapshot["next_recommended_request_types"]
