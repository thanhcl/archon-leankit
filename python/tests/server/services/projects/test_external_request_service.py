"""Tests for external ingress request control-plane service."""

from unittest.mock import AsyncMock, MagicMock

from src.server.services.projects.external_request_service import ExternalRequestService


def _make_notifier():
    notifier = MagicMock()
    notifier.on_external_request_created = AsyncMock()
    notifier.on_external_request_materialized = AsyncMock()
    notifier.on_approval_requested = AsyncMock()
    notifier.on_approval_decided = AsyncMock()
    return notifier


def test_create_external_request_materializes_task():
    client = MagicMock()
    external_table = MagicMock()
    task_table = MagicMock()

    insert_external = MagicMock()
    insert_external.execute.return_value = MagicMock(
        data=[{
            "id": "ext-001",
            "source_channel": "telegram",
            "request_type": "task-request",
            "status": "received",
            "materialize_as": "task",
            "title": "Fix auth bug",
            "summary": "Please investigate auth failures",
            "correlation_id": "corr-001",
            "project_id": "proj-001",
            "source_app": "archon-leankit",
        }]
    )
    external_table.insert.return_value = insert_external

    update_external = MagicMock()
    update_external.eq.return_value = update_external
    update_external.execute.return_value = MagicMock(
        data=[{
            "id": "ext-001",
            "source_channel": "telegram",
            "request_type": "task-request",
            "status": "materialized",
            "materialize_as": "task",
            "title": "Fix auth bug",
            "summary": "Please investigate auth failures",
            "correlation_id": "corr-001",
            "project_id": "proj-001",
            "source_app": "archon-leankit",
            "linked_task_id": "task-001",
        }]
    )
    external_table.update.return_value = update_external

    insert_task = MagicMock()
    insert_task.execute.return_value = MagicMock(
        data=[{
            "id": "task-001",
            "title": "Fix auth bug",
            "status": "approved",
            "created_from": "telegram",
            "tags": ["external-request", "external-channel:telegram", "external-request:ext-001", "input-modality:voice", "voice-to-task"],
        }]
    )
    task_table.insert.return_value = insert_task

    def table_factory(name: str):
        if name == "archon_external_requests":
            return external_table
        if name == "archon_tasks":
            return task_table
        raise AssertionError(f"Unexpected table {name}")

    client.table.side_effect = table_factory
    notifier = _make_notifier()
    service = ExternalRequestService(supabase_client=client, notifier=notifier)

    ok, result = __import__("asyncio").run(
        service.create_request(
            source_channel="telegram",
            request_type="task-request",
            title="Fix auth bug",
            summary="Please investigate auth failures",
            materialize_as="task",
            project_id="proj-001",
            source_app="archon-leankit",
            actor_display="Owner",
            input_modality="voice",
            input_text="Please investigate auth failures",
            task_template={"priority": "high"},
        )
    )

    assert ok is True
    assert result["request"]["linked_task_id"] == "task-001"
    task_payload = task_table.insert.call_args[0][0]
    assert task_payload["created_from"] == "telegram"
    assert task_payload["priority"] == "high"
    assert "input-modality:voice" in task_payload["tags"]
    assert "voice-to-task" in task_payload["tags"]
    assert task_payload["executed_by"]["input_modality"] == "voice"
    notifier.on_external_request_created.assert_awaited_once()
    notifier.on_external_request_materialized.assert_awaited_once()


def test_create_external_request_materializes_approval():
    client = MagicMock()
    external_table = MagicMock()
    approval_table = MagicMock()

    insert_external = MagicMock()
    insert_external.execute.return_value = MagicMock(
        data=[{
            "id": "ext-001",
            "source_channel": "telegram",
            "request_type": "approval-request",
            "status": "received",
            "materialize_as": "approval",
            "title": "Approve deploy",
            "summary": "Can we deploy?",
            "correlation_id": "corr-001",
            "project_id": "proj-001",
        }]
    )
    external_table.insert.return_value = insert_external

    update_external = MagicMock()
    update_external.eq.return_value = update_external
    update_external.execute.return_value = MagicMock(
        data=[{
            "id": "ext-001",
            "source_channel": "telegram",
            "request_type": "approval-request",
            "status": "materialized",
            "materialize_as": "approval",
            "title": "Approve deploy",
            "summary": "Can we deploy?",
            "correlation_id": "corr-001",
            "project_id": "proj-001",
            "linked_approval_request_id": "apr-001",
        }]
    )
    external_table.update.return_value = update_external

    insert_approval = MagicMock()
    insert_approval.execute.return_value = MagicMock(
        data=[{
            "id": "apr-001",
            "status": "pending",
            "title": "Approve deploy",
            "summary": "Can we deploy?",
            "requested_by": "Owner",
            "requested_channel": "telegram",
            "project_id": "proj-001",
            "external_request_id": "ext-001",
        }]
    )
    approval_table.insert.return_value = insert_approval

    def table_factory(name: str):
        if name == "archon_external_requests":
            return external_table
        if name == "archon_approval_requests":
            return approval_table
        raise AssertionError(f"Unexpected table {name}")

    client.table.side_effect = table_factory
    notifier = _make_notifier()
    service = ExternalRequestService(supabase_client=client, notifier=notifier)

    ok, result = __import__("asyncio").run(
        service.create_request(
            source_channel="telegram",
            request_type="approval-request",
            title="Approve deploy",
            summary="Can we deploy?",
            materialize_as="approval",
            project_id="proj-001",
            actor_display="Owner",
            approval_template={"context": {"scope": "production"}},
        )
    )

    assert ok is True
    assert result["request"]["linked_approval_request_id"] == "apr-001"
    approval_payload = approval_table.insert.call_args[0][0]
    assert approval_payload["external_request_id"] == "ext-001"
    assert approval_payload["context"]["scope"] == "production"
    notifier.on_external_request_created.assert_awaited_once()
    notifier.on_approval_requested.assert_awaited_once()
    notifier.on_external_request_materialized.assert_awaited_once()


def test_create_external_request_is_idempotent_by_correlation_id():
    client = MagicMock()
    external_table = MagicMock()

    select_existing = MagicMock()
    select_existing.eq.return_value = select_existing
    select_existing.limit.return_value = select_existing
    select_existing.execute.return_value = MagicMock(
        data=[{
            "id": "ext-001",
            "source_channel": "telegram",
            "request_type": "message",
            "status": "received",
            "materialize_as": "none",
            "title": "Need status",
            "summary": "Need status",
            "correlation_id": "corr-001",
        }]
    )
    external_table.select.return_value = select_existing

    def table_factory(name: str):
        if name == "archon_external_requests":
            return external_table
        raise AssertionError(f"Unexpected table {name}")

    client.table.side_effect = table_factory
    notifier = _make_notifier()
    service = ExternalRequestService(supabase_client=client, notifier=notifier)

    ok, result = __import__("asyncio").run(
        service.create_request(
            source_channel="telegram",
            request_type="message",
            title="Need status",
            summary="Need status",
            materialize_as="none",
            correlation_id="corr-001",
        )
    )

    assert ok is True
    assert result["request"]["id"] == "ext-001"
    assert external_table.insert.call_count == 0
    notifier.on_external_request_created.assert_not_called()
