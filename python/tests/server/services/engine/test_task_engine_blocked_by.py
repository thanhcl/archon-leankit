"""Tests for TaskEngine blocked_by enforcement."""

from unittest.mock import MagicMock, patch

import pytest

from src.server.services.engine.task_engine import TaskEngine


def _make_task(task_id: str, blocked_by: list[str] | None = None, **overrides):
    base = {
        "id": task_id,
        "project_id": "proj-001",
        "title": f"Task {task_id}",
        "description": "",
        "status": "assigned",
        "assignee": "Agent",
        "task_order": 0,
        "priority": "medium",
        "blocked_by": blocked_by or [],
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    base.update(overrides)
    return base


def _mock_supabase_for_blocker_query(blocker_data: list[dict]):
    """Create a mock supabase client that returns blocker_data for .in_() queries."""
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

    return mock_client


class TestFilterBlockedTasks:
    """Tests for _filter_blocked_tasks method."""

    def _make_engine(self, supabase_client=None):
        with patch("src.server.services.engine.task_engine.TaskService"), \
             patch("src.server.services.engine.task_engine.TaskLifecycleService"), \
             patch("src.server.services.engine.task_engine.ArchitectReviewer"), \
             patch("src.server.services.engine.task_engine.Notifier"), \
             patch("src.server.services.engine.task_engine.LearningProcessor"), \
             patch("src.server.services.engine.task_engine.PromptBuilder"), \
             patch("src.server.services.engine.task_engine.HealthMonitor"), \
             patch("src.server.services.engine.task_engine.CCSpawner"):
            engine = TaskEngine(project_path="/tmp/test", project_id="proj-001")
            if supabase_client:
                engine.task_service.supabase_client = supabase_client
            return engine

    def test_no_blocked_by_passes_all(self):
        """Tasks without blocked_by should all pass through."""
        engine = self._make_engine()
        tasks = [
            _make_task("t-1"),
            _make_task("t-2"),
            _make_task("t-3"),
        ]
        result = engine._filter_blocked_tasks(tasks)
        assert len(result) == 3

    def test_empty_blocked_by_passes(self):
        """Tasks with empty blocked_by array should pass."""
        engine = self._make_engine()
        tasks = [_make_task("t-1", blocked_by=[])]
        result = engine._filter_blocked_tasks(tasks)
        assert len(result) == 1

    def test_all_blockers_done_passes(self):
        """Task should execute when all blockers are done."""
        mock_client = _mock_supabase_for_blocker_query([
            {"id": "blocker-1", "status": "done"},
            {"id": "blocker-2", "status": "done"},
        ])
        engine = self._make_engine(supabase_client=mock_client)

        tasks = [_make_task("t-1", blocked_by=["blocker-1", "blocker-2"])]
        result = engine._filter_blocked_tasks(tasks)
        assert len(result) == 1
        assert result[0]["id"] == "t-1"

    def test_blocker_not_done_blocks(self):
        """Task should be skipped when a blocker is not done."""
        mock_client = _mock_supabase_for_blocker_query([
            {"id": "blocker-1", "status": "done"},
            {"id": "blocker-2", "status": "assigned"},
        ])
        engine = self._make_engine(supabase_client=mock_client)

        tasks = [_make_task("t-1", blocked_by=["blocker-1", "blocker-2"])]
        result = engine._filter_blocked_tasks(tasks)
        assert len(result) == 0

    def test_mixed_blocked_and_unblocked(self):
        """Only unblocked tasks should pass through."""
        mock_client = _mock_supabase_for_blocker_query([
            {"id": "blocker-1", "status": "assigned"},  # not done
        ])
        engine = self._make_engine(supabase_client=mock_client)

        tasks = [
            _make_task("t-1"),  # no blockers
            _make_task("t-2", blocked_by=["blocker-1"]),  # blocked
            _make_task("t-3"),  # no blockers
        ]
        result = engine._filter_blocked_tasks(tasks)
        assert len(result) == 2
        assert [t["id"] for t in result] == ["t-1", "t-3"]

    def test_unknown_blocker_blocks(self):
        """If a blocker ID is not found in DB, task should be blocked."""
        mock_client = _mock_supabase_for_blocker_query([])  # blocker not found
        engine = self._make_engine(supabase_client=mock_client)

        tasks = [_make_task("t-1", blocked_by=["nonexistent-id"])]
        result = engine._filter_blocked_tasks(tasks)
        assert len(result) == 0

    def test_db_error_returns_empty(self):
        """On DB error, fail safe by returning empty list."""
        mock_client = MagicMock()
        mock_client.table.side_effect = Exception("DB connection failed")
        engine = self._make_engine(supabase_client=mock_client)

        tasks = [_make_task("t-1", blocked_by=["blocker-1"])]
        result = engine._filter_blocked_tasks(tasks)
        assert len(result) == 0

    def test_blocker_in_executing_blocks(self):
        """A blocker in 'executing' state should block the dependent task."""
        mock_client = _mock_supabase_for_blocker_query([
            {"id": "blocker-1", "status": "executing"},
        ])
        engine = self._make_engine(supabase_client=mock_client)

        tasks = [_make_task("t-1", blocked_by=["blocker-1"])]
        result = engine._filter_blocked_tasks(tasks)
        assert len(result) == 0

    def test_blocker_cancelled_blocks(self):
        """A cancelled blocker should still block (it's not done)."""
        mock_client = _mock_supabase_for_blocker_query([
            {"id": "blocker-1", "status": "cancelled"},
        ])
        engine = self._make_engine(supabase_client=mock_client)

        tasks = [_make_task("t-1", blocked_by=["blocker-1"])]
        result = engine._filter_blocked_tasks(tasks)
        assert len(result) == 0

    def test_logs_blocked_reason(self, caplog):
        """Should log which blockers are preventing execution."""
        mock_client = _mock_supabase_for_blocker_query([
            {"id": "blocker-1", "status": "assigned"},
        ])
        engine = self._make_engine(supabase_client=mock_client)

        tasks = [_make_task("t-1", blocked_by=["blocker-1"])]

        import logging
        with caplog.at_level(logging.INFO):
            engine._filter_blocked_tasks(tasks)

        assert any("Task blocked" in record.message and "blocker-1" in record.message
                    for record in caplog.records)


class TestBlockedByInCreateTask:
    """Tests for blocked_by field in task creation."""

    @pytest.mark.asyncio
    async def test_create_task_with_blocked_by(self):
        """Should pass blocked_by to database when creating a task."""
        from src.server.services.projects.task_service import TaskService

        mock_client = MagicMock()
        insert_mock = MagicMock()
        execute_result = MagicMock()
        execute_result.data = [{
            "id": "new-task",
            "project_id": "proj-001",
            "title": "Test",
            "description": "",
            "status": "draft",
            "assignee": "User",
            "task_order": 0,
            "priority": "medium",
            "complexity": "simple",
            "owner": None,
            "source_app": None,
            "blocked_by": ["blocker-1", "blocker-2"],
            "created_at": "2026-01-01T00:00:00",
        }]
        insert_mock.execute.return_value = execute_result
        mock_client.table.return_value.insert.return_value = insert_mock

        service = TaskService(supabase_client=mock_client)
        ok, result = await service.create_task(
            project_id="proj-001",
            title="Test",
            blocked_by=["blocker-1", "blocker-2"],
        )

        assert ok is True
        assert result["task"]["blocked_by"] == ["blocker-1", "blocker-2"]

        # Verify blocked_by was included in the insert call
        insert_call_data = mock_client.table.return_value.insert.call_args[0][0]
        assert insert_call_data["blocked_by"] == ["blocker-1", "blocker-2"]

    @pytest.mark.asyncio
    async def test_create_task_without_blocked_by(self):
        """Should not include blocked_by in insert when not provided."""
        from src.server.services.projects.task_service import TaskService

        mock_client = MagicMock()
        insert_mock = MagicMock()
        execute_result = MagicMock()
        execute_result.data = [{
            "id": "new-task",
            "project_id": "proj-001",
            "title": "Test",
            "description": "",
            "status": "draft",
            "assignee": "User",
            "task_order": 0,
            "priority": "medium",
            "complexity": "simple",
            "owner": None,
            "source_app": None,
            "blocked_by": [],
            "created_at": "2026-01-01T00:00:00",
        }]
        insert_mock.execute.return_value = execute_result
        mock_client.table.return_value.insert.return_value = insert_mock

        service = TaskService(supabase_client=mock_client)
        ok, result = await service.create_task(
            project_id="proj-001",
            title="Test",
        )

        assert ok is True
        # blocked_by should not be in the insert payload when not provided
        insert_call_data = mock_client.table.return_value.insert.call_args[0][0]
        assert "blocked_by" not in insert_call_data


class TestBlockedByInUpdateTask:
    """Tests for blocked_by field in task updates."""

    @pytest.mark.asyncio
    async def test_update_task_blocked_by(self):
        """Should update blocked_by field."""
        from src.server.services.projects.task_service import TaskService

        mock_client = MagicMock()
        update_mock = MagicMock()
        update_mock.eq.return_value = update_mock
        execute_result = MagicMock()
        execute_result.data = [{"id": "t-1", "blocked_by": ["blocker-1"]}]
        update_mock.execute.return_value = execute_result
        mock_client.table.return_value.update.return_value = update_mock

        service = TaskService(supabase_client=mock_client)
        ok, result = await service.update_task(
            task_id="t-1",
            update_fields={"blocked_by": ["blocker-1"]},
        )

        assert ok is True
        update_call_data = mock_client.table.return_value.update.call_args[0][0]
        assert update_call_data["blocked_by"] == ["blocker-1"]
