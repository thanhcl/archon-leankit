"""Tests for dependency auto-unblock reconciliation (C-P5-03)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.task_engine import TaskEngine


def _make_task(task_id: str, blocked_by: list[str] | None = None, status: str = "assigned", **overrides):
    base = {
        "id": task_id,
        "project_id": "proj-001",
        "title": f"Task {task_id}",
        "description": "",
        "status": status,
        "assignee": "Agent",
        "task_order": 0,
        "priority": "medium",
        "blocked_by": blocked_by or [],
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    base.update(overrides)
    return base


def _make_engine():
    with patch("src.server.services.engine.task_engine.TaskService") as MockTS, \
         patch("src.server.services.engine.task_engine.TaskLifecycleService"), \
         patch("src.server.services.engine.task_engine.ArchitectReviewer"), \
         patch("src.server.services.engine.task_engine.Notifier") as MockNotifier, \
         patch("src.server.services.engine.task_engine.LearningProcessor"), \
         patch("src.server.services.engine.task_engine.PromptBuilder"), \
         patch("src.server.services.engine.task_engine.HealthMonitor"), \
         patch("src.server.services.engine.task_engine.CCSpawner"):
        engine = TaskEngine(project_path="/tmp/test", project_id="proj-001")
        engine.notifier = MockNotifier.return_value
        engine.notifier.emit = AsyncMock()
        return engine


def _setup_supabase_mock(engine, all_tasks, blocker_data):
    """Configure task_service and supabase mocks for auto-unblock tests."""
    # Mock list_tasks to return all_tasks
    engine.task_service.list_tasks.return_value = (True, {"tasks": all_tasks})

    # Mock supabase for blocker status queries
    mock_client = MagicMock()
    table = MagicMock()
    select = MagicMock()
    in_mock = MagicMock()
    execute_result = MagicMock()
    execute_result.data = blocker_data

    mock_client.table.return_value = table
    table.select.return_value = select
    select.in_.return_value = in_mock
    in_mock.execute.return_value = execute_result

    # Also mock update for clearing blocked_by
    update_mock = MagicMock()
    eq_mock = MagicMock()
    update_execute = MagicMock()
    table.update.return_value = update_mock
    update_mock.eq.return_value = eq_mock
    eq_mock.execute.return_value = update_execute

    engine.task_service.supabase_client = mock_client
    return mock_client


class TestReconcileBlockedDependencies:

    @pytest.mark.asyncio
    async def test_no_blocked_tasks_does_nothing(self):
        engine = _make_engine()
        all_tasks = [_make_task("t1"), _make_task("t2")]
        _setup_supabase_mock(engine, all_tasks, [])

        await engine._reconcile_blocked_dependencies()

        # No update calls should have been made
        engine.task_service.supabase_client.table.return_value.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocker_done_clears_blocked_by(self):
        engine = _make_engine()
        all_tasks = [
            _make_task("parent", status="done"),
            _make_task("child", blocked_by=["parent"]),
        ]
        _setup_supabase_mock(engine, all_tasks, [{"id": "parent", "status": "done"}])

        await engine._reconcile_blocked_dependencies()

        # Should have called update to clear blocked_by
        update_call = engine.task_service.supabase_client.table.return_value.update
        update_call.assert_called_once()
        updated_data = update_call.call_args[0][0]
        assert updated_data["blocked_by"] == []

    @pytest.mark.asyncio
    async def test_blocker_cancelled_clears_blocked_by(self):
        engine = _make_engine()
        all_tasks = [
            _make_task("parent", status="cancelled"),
            _make_task("child", blocked_by=["parent"]),
        ]
        _setup_supabase_mock(engine, all_tasks, [{"id": "parent", "status": "cancelled"}])

        await engine._reconcile_blocked_dependencies()

        update_call = engine.task_service.supabase_client.table.return_value.update
        update_call.assert_called_once()
        updated_data = update_call.call_args[0][0]
        assert updated_data["blocked_by"] == []

    @pytest.mark.asyncio
    async def test_blocker_failed_clears_blocked_by(self):
        engine = _make_engine()
        all_tasks = [
            _make_task("parent", status="failed"),
            _make_task("child", blocked_by=["parent"]),
        ]
        _setup_supabase_mock(engine, all_tasks, [{"id": "parent", "status": "failed"}])

        await engine._reconcile_blocked_dependencies()

        update_call = engine.task_service.supabase_client.table.return_value.update
        update_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocker_still_active_not_cleared(self):
        engine = _make_engine()
        all_tasks = [
            _make_task("parent", status="executing"),
            _make_task("child", blocked_by=["parent"]),
        ]
        _setup_supabase_mock(engine, all_tasks, [{"id": "parent", "status": "executing"}])

        await engine._reconcile_blocked_dependencies()

        # No update because parent is still executing
        engine.task_service.supabase_client.table.return_value.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_unblock_keeps_remaining(self):
        engine = _make_engine()
        all_tasks = [
            _make_task("p1", status="done"),
            _make_task("p2", status="executing"),
            _make_task("child", blocked_by=["p1", "p2"]),
        ]
        _setup_supabase_mock(engine, all_tasks, [
            {"id": "p1", "status": "done"},
            {"id": "p2", "status": "executing"},
        ])

        await engine._reconcile_blocked_dependencies()

        update_call = engine.task_service.supabase_client.table.return_value.update
        update_call.assert_called_once()
        updated_data = update_call.call_args[0][0]
        assert updated_data["blocked_by"] == ["p2"]

    @pytest.mark.asyncio
    async def test_nonexistent_blocker_detected_as_invalid(self):
        engine = _make_engine()
        all_tasks = [
            _make_task("child", blocked_by=["nonexistent"]),
        ]
        _setup_supabase_mock(engine, all_tasks, [])  # blocker not found in DB

        await engine._reconcile_blocked_dependencies()

        update_call = engine.task_service.supabase_client.table.return_value.update
        update_call.assert_called_once()
        updated_data = update_call.call_args[0][0]
        assert updated_data["blocked_by"] == []

    @pytest.mark.asyncio
    async def test_emits_auto_unblocked_event(self):
        engine = _make_engine()
        all_tasks = [
            _make_task("parent", status="done"),
            _make_task("child", blocked_by=["parent"]),
        ]
        _setup_supabase_mock(engine, all_tasks, [{"id": "parent", "status": "done"}])

        await engine._reconcile_blocked_dependencies()

        engine.notifier.emit.assert_called_once()
        call_args = engine.notifier.emit.call_args
        assert call_args[0][0] == "task.auto_unblocked"
        assert call_args[1]["task_id"] == "child"
        assert "parent" in call_args[1]["data"]["cleared_blockers"]

    @pytest.mark.asyncio
    async def test_terminal_tasks_not_checked(self):
        engine = _make_engine()
        all_tasks = [
            _make_task("done_task", status="done", blocked_by=["old_blocker"]),
        ]
        _setup_supabase_mock(engine, all_tasks, [])

        await engine._reconcile_blocked_dependencies()

        # Terminal task should not be processed
        engine.task_service.supabase_client.table.return_value.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_project_id_returns_early(self):
        engine = _make_engine()
        engine.project_id = None

        await engine._reconcile_blocked_dependencies()

        engine.task_service.list_tasks.assert_not_called()
