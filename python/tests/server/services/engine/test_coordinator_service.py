"""Tests for coordinator service — parent-child task orchestration (C-P7-01, C-P7-03, C-P7-04)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.coordinator_service import CoordinatorService


def _make_task(task_id="t1", **overrides):
    base = {
        "id": task_id,
        "project_id": "p1",
        "title": "Parent Task",
        "priority": "medium",
        "status": "executing",
        "decomposition_mode": "none",
        "plan_item_id": "plan1",
        "sprint_batch_id": "batch1",
        "allowed_paths": ["src/"],
        "forbidden_paths": [],
        "tags": ["backend"],
        "task_type": "feature",
    }
    base.update(overrides)
    return base


def _make_child(task_id="c1", status="done", **overrides):
    base = {"id": task_id, "status": status, "parent_task_id": "t1"}
    base.update(overrides)
    return base


def _make_service():
    task_service = MagicMock()
    lifecycle_service = MagicMock()
    lifecycle_service.execute_transition = AsyncMock(return_value=(True, {}))
    notifier = MagicMock()
    notifier.emit = AsyncMock()

    service = CoordinatorService(task_service, lifecycle_service, notifier)
    return service


class TestCreateCoordinatorChildren:

    @pytest.mark.asyncio
    async def test_creates_children_with_inherited_fields(self):
        service = _make_service()
        parent = _make_task()
        service.task_service.get_task.return_value = (True, {"task": parent})
        service.task_service.update_task = AsyncMock()
        service.task_service.create_task = AsyncMock(return_value=(True, {"task": {"id": "c1"}}))

        specs = [{"title": "Child A", "description": "Do A"}]
        result = await service.create_coordinator_children("t1", specs)

        assert len(result) == 1
        create_call = service.task_service.create_task.call_args
        assert create_call[1]["parent_task_id"] == "t1"
        assert create_call[1]["project_id"] == "p1"
        assert create_call[1]["allowed_paths"] == ["src/"]

    @pytest.mark.asyncio
    async def test_marks_parent_as_coordinator(self):
        service = _make_service()
        service.task_service.get_task.return_value = (True, {"task": _make_task()})
        service.task_service.update_task = AsyncMock()
        service.task_service.create_task = AsyncMock(return_value=(True, {"task": {"id": "c1"}}))

        await service.create_coordinator_children("t1", [{"title": "Child"}])

        service.task_service.update_task.assert_called_with(
            task_id="t1",
            update_fields={"decomposition_mode": "coordinator"},
        )

    @pytest.mark.asyncio
    async def test_emits_children_created_event(self):
        service = _make_service()
        service.task_service.get_task.return_value = (True, {"task": _make_task()})
        service.task_service.update_task = AsyncMock()
        service.task_service.create_task = AsyncMock(return_value=(True, {"task": {"id": "c1"}}))

        await service.create_coordinator_children("t1", [{"title": "Child"}])

        events = [c[0][0] for c in service.notifier.emit.call_args_list]
        assert "task.coordinator_children_created" in events

    @pytest.mark.asyncio
    async def test_emits_agent_spawned_events(self):
        """C-P7-04: sub-agent lineage events."""
        service = _make_service()
        service.task_service.get_task.return_value = (True, {"task": _make_task()})
        service.task_service.update_task = AsyncMock()
        service.task_service.create_task = AsyncMock(
            side_effect=[
                (True, {"task": {"id": "c1", "title": "A"}}),
                (True, {"task": {"id": "c2", "title": "B"}}),
            ]
        )

        await service.create_coordinator_children("t1", [
            {"title": "A"}, {"title": "B"},
        ])

        spawned_events = [
            c for c in service.notifier.emit.call_args_list
            if c[0][0] == "agent.spawned"
        ]
        assert len(spawned_events) == 2
        assert spawned_events[0][1]["data"]["parent_agent_id"] == "t1"

    @pytest.mark.asyncio
    async def test_handles_create_failure(self):
        service = _make_service()
        service.task_service.get_task.return_value = (True, {"task": _make_task()})
        service.task_service.update_task = AsyncMock()
        service.task_service.create_task = AsyncMock(return_value=(False, {"error": "DB error"}))

        result = await service.create_coordinator_children("t1", [{"title": "Child"}])
        assert len(result) == 0


class TestEvaluateParentStatus:

    @pytest.mark.asyncio
    async def test_all_children_done(self):
        service = _make_service()
        service.task_service.get_subtasks.return_value = (True, {"tasks": [
            _make_child("c1", "done"),
            _make_child("c2", "done"),
        ]})

        should, details = await service.evaluate_parent_status("t1")
        assert should is True
        assert details["next_status"] == "review"
        assert details["summary"]["done"] == 2

    @pytest.mark.asyncio
    async def test_children_still_running(self):
        service = _make_service()
        service.task_service.get_subtasks.return_value = (True, {"tasks": [
            _make_child("c1", "done"),
            _make_child("c2", "executing"),
        ]})

        should, details = await service.evaluate_parent_status("t1")
        assert should is False
        assert details["reason"] == "children_still_running"

    @pytest.mark.asyncio
    async def test_all_children_failed(self):
        service = _make_service()
        service.task_service.get_subtasks.return_value = (True, {"tasks": [
            _make_child("c1", "failed"),
            _make_child("c2", "failed"),
        ]})

        should, details = await service.evaluate_parent_status("t1")
        assert should is True
        assert details["next_status"] == "failed"

    @pytest.mark.asyncio
    async def test_mixed_done_failed(self):
        service = _make_service()
        service.task_service.get_subtasks.return_value = (True, {"tasks": [
            _make_child("c1", "done"),
            _make_child("c2", "failed"),
        ]})

        should, details = await service.evaluate_parent_status("t1")
        assert should is True
        assert details["next_status"] == "review"  # mixed goes to review
        assert details["reason"] == "children_mixed_results"

    @pytest.mark.asyncio
    async def test_no_children(self):
        service = _make_service()
        service.task_service.get_subtasks.return_value = (True, {"tasks": []})

        should, details = await service.evaluate_parent_status("t1")
        assert should is False

    @pytest.mark.asyncio
    async def test_done_plus_cancelled(self):
        service = _make_service()
        service.task_service.get_subtasks.return_value = (True, {"tasks": [
            _make_child("c1", "done"),
            _make_child("c2", "cancelled"),
        ]})

        should, details = await service.evaluate_parent_status("t1")
        assert should is True
        assert details["next_status"] == "review"


class TestAdvanceParentIfReady:

    @pytest.mark.asyncio
    async def test_advances_when_ready(self):
        service = _make_service()
        service.task_service.get_subtasks.return_value = (True, {"tasks": [
            _make_child("c1", "done"),
        ]})

        result = await service.advance_parent_if_ready("t1")
        assert result is True
        service.lifecycle_service.execute_transition.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_advance_when_running(self):
        service = _make_service()
        service.task_service.get_subtasks.return_value = (True, {"tasks": [
            _make_child("c1", "executing"),
        ]})

        result = await service.advance_parent_if_ready("t1")
        assert result is False

    @pytest.mark.asyncio
    async def test_emits_coordinator_aggregated(self):
        service = _make_service()
        service.task_service.get_subtasks.return_value = (True, {"tasks": [
            _make_child("c1", "done"),
        ]})

        await service.advance_parent_if_ready("t1")

        events = [c[0][0] for c in service.notifier.emit.call_args_list]
        assert "task.coordinator_aggregated" in events
