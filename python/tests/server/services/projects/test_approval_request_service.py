"""Tests for approval request control-plane service."""

from unittest.mock import AsyncMock, MagicMock

from src.server.services.projects.approval_request_service import ApprovalRequestService


def _make_notifier():
    notifier = MagicMock()
    notifier.on_approval_requested = AsyncMock()
    notifier.on_approval_decided = AsyncMock()
    return notifier


def test_create_approval_request_persists_pending_record():
    client = MagicMock()
    table = MagicMock()

    insert = MagicMock()
    insert.execute.return_value = MagicMock(
        data=[{
            "id": "apr-001",
            "status": "pending",
            "title": "Approve migration",
            "summary": "Need go-live approval",
            "requested_by": "Owner",
            "requested_channel": "telegram",
            "project_id": "proj-001",
        }]
    )
    table.insert.return_value = insert
    client.table.return_value = table

    notifier = _make_notifier()
    service = ApprovalRequestService(supabase_client=client, notifier=notifier)

    ok, result = __import__("asyncio").run(
        service.create_request(
            title="Approve migration",
            summary="Need go-live approval",
            requested_by="Owner",
            requested_channel="telegram",
            project_id="proj-001",
        )
    )

    assert ok is True
    assert result["approval"]["status"] == "pending"
    notifier.on_approval_requested.assert_awaited_once()


def test_decide_approval_request_updates_status():
    client = MagicMock()
    table = MagicMock()

    select = MagicMock()
    select.eq.return_value = select
    select.execute.return_value = MagicMock(
        data=[{
            "id": "apr-001",
            "status": "pending",
            "title": "Approve migration",
            "summary": "Need go-live approval",
            "requested_by": "Owner",
            "requested_channel": "telegram",
            "project_id": "proj-001",
        }]
    )
    table.select.return_value = select

    update = MagicMock()
    update.eq.return_value = update
    update.execute.return_value = MagicMock(
        data=[{
            "id": "apr-001",
            "status": "approved",
            "title": "Approve migration",
            "summary": "Need go-live approval",
            "requested_by": "Owner",
            "requested_channel": "telegram",
            "project_id": "proj-001",
            "decided_by": "mobile-owner",
            "decision_comment": "Ship it",
        }]
    )
    table.update.return_value = update
    client.table.return_value = table

    notifier = _make_notifier()
    service = ApprovalRequestService(supabase_client=client, notifier=notifier)

    ok, result = __import__("asyncio").run(
        service.decide_request(
            "apr-001",
            decision="approve",
            decided_by="mobile-owner",
            decision_comment="Ship it",
        )
    )

    assert ok is True
    assert result["approval"]["status"] == "approved"
    update_payload = table.update.call_args[0][0]
    assert update_payload["status"] == "approved"
    assert update_payload["decided_by"] == "mobile-owner"
    notifier.on_approval_decided.assert_awaited_once()


def test_batch_decide_approval_requests_updates_multiple_records():
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table

    select_first = MagicMock()
    select_first.eq.return_value = select_first
    select_first.execute.return_value = MagicMock(
        data=[{
            "id": "apr-001",
            "status": "pending",
            "title": "Approve task one",
            "summary": "Need owner approval",
            "requested_by": "Owner",
            "requested_channel": "telegram",
        }]
    )
    select_second = MagicMock()
    select_second.eq.return_value = select_second
    select_second.execute.return_value = MagicMock(
        data=[{
            "id": "apr-002",
            "status": "pending",
            "title": "Approve task two",
            "summary": "Need owner approval",
            "requested_by": "Owner",
            "requested_channel": "telegram",
        }]
    )
    table.select.side_effect = [select_first, select_second]

    update_first = MagicMock()
    update_first.eq.return_value = update_first
    update_first.execute.return_value = MagicMock(
        data=[{
            "id": "apr-001",
            "status": "approved",
            "decided_by": "mobile-owner",
        }]
    )
    update_second = MagicMock()
    update_second.eq.return_value = update_second
    update_second.execute.return_value = MagicMock(
        data=[{
            "id": "apr-002",
            "status": "approved",
            "decided_by": "mobile-owner",
        }]
    )
    table.update.side_effect = [update_first, update_second]

    notifier = _make_notifier()
    service = ApprovalRequestService(supabase_client=client, notifier=notifier)

    ok, result = __import__("asyncio").run(
        service.decide_requests(
            ["apr-001", "apr-002", "apr-001"],
            decision="approve",
            decided_by="mobile-owner",
            decision_comment="Batch approved",
        )
    )

    assert ok is True
    assert result["processed_count"] == 2
    assert result["failed_count"] == 0
    assert [item["id"] for item in result["approvals"]] == ["apr-001", "apr-002"]
    assert notifier.on_approval_decided.await_count == 2


def test_batch_decide_approval_requests_supports_threshold_bundle_metadata():
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table

    select_first = MagicMock()
    select_first.eq.return_value = select_first
    select_first.execute.return_value = MagicMock(
        data=[{"id": "apr-101", "status": "pending", "title": "Approve one", "summary": "", "requested_by": "Owner", "requested_channel": "telegram"}]
    )
    select_second = MagicMock()
    select_second.eq.return_value = select_second
    select_second.execute.return_value = MagicMock(
        data=[{"id": "apr-102", "status": "pending", "title": "Approve two", "summary": "", "requested_by": "Owner", "requested_channel": "telegram"}]
    )
    table.select.side_effect = [select_first, select_second]

    update_first = MagicMock()
    update_first.eq.return_value = update_first
    update_first.execute.return_value = MagicMock(data=[{"id": "apr-101", "status": "approved"}])
    update_second = MagicMock()
    update_second.eq.return_value = update_second
    update_second.execute.return_value = MagicMock(data=[{"id": "apr-102", "status": "approved"}])
    table.update.side_effect = [update_first, update_second]

    service = ApprovalRequestService(supabase_client=client, notifier=_make_notifier())
    ok, result = __import__("asyncio").run(
        service.decide_requests(
            ["apr-101", "apr-102"],
            decision="approve",
            decided_by="telegram:owner",
            bundle_label="release-bundle",
            minimum_required=2,
        )
    )

    assert ok is True
    assert result["bundle_label"] == "release-bundle"
    assert result["minimum_required"] == 2
    assert result["threshold_met"] is True


def test_batch_decide_approval_requests_fails_when_threshold_not_met():
    service = ApprovalRequestService(supabase_client=MagicMock(), notifier=_make_notifier())
    ok, result = __import__("asyncio").run(
        service.decide_requests(
            ["apr-201"],
            decision="approve",
            decided_by="telegram:owner",
            bundle_label="release-bundle",
            minimum_required=2,
        )
    )

    assert ok is False
    assert result["bundle_label"] == "release-bundle"
    assert result["minimum_required"] == 2
    assert result["threshold_met"] is False
