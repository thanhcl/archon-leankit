"""Tests for lifecycle reconciliation watchdog (D-P2-04)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.task_engine import TaskEngine


def _make_task(task_id: str = "t1", status: str = "executing", **overrides):
    base = {
        "id": task_id,
        "project_id": "proj-001",
        "title": f"Task {task_id}",
        "status": status,
        "priority": "medium",
        "blocked_by": [],
        "created_at": "2026-01-01T00:00:00",
    }
    base.update(overrides)
    return base


def _make_run(run_id: str = "r1", status: str = "completed", **overrides):
    base = {
        "id": run_id,
        "task_id": "t1",
        "status": status,
        "stage": "execute",
        "started_at": "2026-01-01T00:00:00",
    }
    base.update(overrides)
    return base


def _make_engine():
    with patch("src.server.services.engine.task_engine.TaskService"), \
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
        engine.lifecycle_service.execute_transition = AsyncMock(return_value=(True, {}))
        # Replace execution_run_service with a full MagicMock
        engine.execution_run_service = MagicMock()
        return engine


class TestReconcileTaskLifecycle:

    @pytest.mark.asyncio
    async def test_no_stuck_tasks_does_nothing(self):
        engine = _make_engine()
        # No tasks in any active state
        engine.task_service.list_tasks.return_value = (True, {"tasks": []})
        engine.execution_run_service.list_runs = MagicMock()

        await engine._reconcile_task_lifecycle()

        engine.lifecycle_service.execute_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_executing_task_with_completed_run_advances(self):
        engine = _make_engine()
        task = _make_task("t1", status="executing")

        def list_tasks_side_effect(**kwargs):
            if kwargs.get("status") == "executing":
                return (True, {"tasks": [task]})
            return (True, {"tasks": []})

        engine.task_service.list_tasks.side_effect = list_tasks_side_effect
        engine.execution_run_service.list_runs.return_value = (
            True, {"runs": [_make_run("r1", status="completed")]}
        )

        await engine._reconcile_task_lifecycle()

        engine.lifecycle_service.execute_transition.assert_called_once()
        call_kwargs = engine.lifecycle_service.execute_transition.call_args[1]
        assert call_kwargs["task_id"] == "t1"
        assert call_kwargs["new_status"] == "architect-review"
        assert call_kwargs["changed_by"] == "lifecycle-watchdog"

    @pytest.mark.asyncio
    async def test_executing_task_with_failed_run_fails_task(self):
        engine = _make_engine()
        task = _make_task("t1", status="executing")

        def list_tasks_side_effect(**kwargs):
            if kwargs.get("status") == "executing":
                return (True, {"tasks": [task]})
            return (True, {"tasks": []})

        engine.task_service.list_tasks.side_effect = list_tasks_side_effect
        engine.execution_run_service.list_runs.return_value = (
            True, {"runs": [_make_run("r1", status="failed")]}
        )

        await engine._reconcile_task_lifecycle()

        call_kwargs = engine.lifecycle_service.execute_transition.call_args[1]
        assert call_kwargs["new_status"] == "failed"

    @pytest.mark.asyncio
    async def test_task_with_active_run_not_touched(self):
        engine = _make_engine()
        task = _make_task("t1", status="executing")

        def list_tasks_side_effect(**kwargs):
            if kwargs.get("status") == "executing":
                return (True, {"tasks": [task]})
            return (True, {"tasks": []})

        engine.task_service.list_tasks.side_effect = list_tasks_side_effect
        engine.execution_run_service.list_runs.return_value = (
            True, {"runs": [_make_run("r1", status="running")]}
        )

        await engine._reconcile_task_lifecycle()

        engine.lifecycle_service.execute_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_task_in_own_execution_map_skipped(self):
        engine = _make_engine()
        task = _make_task("t1", status="executing")

        def list_tasks_side_effect(**kwargs):
            if kwargs.get("status") == "executing":
                return (True, {"tasks": [task]})
            return (True, {"tasks": []})

        engine.task_service.list_tasks.side_effect = list_tasks_side_effect
        engine._execution_tasks["t1"] = MagicMock()  # actively executing

        await engine._reconcile_task_lifecycle()

        engine.lifecycle_service.execute_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_code_review_advances_to_review(self):
        engine = _make_engine()
        task = _make_task("t1", status="code-review")

        def list_tasks_side_effect(**kwargs):
            if kwargs.get("status") == "code-review":
                return (True, {"tasks": [task]})
            return (True, {"tasks": []})

        engine.task_service.list_tasks.side_effect = list_tasks_side_effect
        engine.execution_run_service.list_runs.return_value = (
            True, {"runs": [_make_run("r1", status="completed")]}
        )

        await engine._reconcile_task_lifecycle()

        call_kwargs = engine.lifecycle_service.execute_transition.call_args[1]
        assert call_kwargs["new_status"] == "review"

    @pytest.mark.asyncio
    async def test_emits_reconciliation_event(self):
        engine = _make_engine()
        task = _make_task("t1", status="executing")

        def list_tasks_side_effect(**kwargs):
            if kwargs.get("status") == "executing":
                return (True, {"tasks": [task]})
            return (True, {"tasks": []})

        engine.task_service.list_tasks.side_effect = list_tasks_side_effect
        engine.execution_run_service.list_runs.return_value = (
            True, {"runs": [_make_run("r1", status="completed")]}
        )

        await engine._reconcile_task_lifecycle()

        engine.notifier.emit.assert_called_once()
        call_args = engine.notifier.emit.call_args
        assert call_args[0][0] == "task.lifecycle_reconciled"

    @pytest.mark.asyncio
    async def test_no_project_id_returns_early(self):
        engine = _make_engine()
        engine.project_id = None

        await engine._reconcile_task_lifecycle()

        engine.task_service.list_tasks.assert_not_called()

    @pytest.mark.asyncio
    async def test_task_with_no_runs_fails(self):
        engine = _make_engine()
        task = _make_task("t1", status="executing")

        def list_tasks_side_effect(**kwargs):
            if kwargs.get("status") == "executing":
                return (True, {"tasks": [task]})
            return (True, {"tasks": []})

        engine.task_service.list_tasks.side_effect = list_tasks_side_effect
        engine.execution_run_service.list_runs.return_value = (True, {"runs": []})

        await engine._reconcile_task_lifecycle()

        call_kwargs = engine.lifecycle_service.execute_transition.call_args[1]
        assert call_kwargs["new_status"] == "failed"
        assert "no execution runs" in call_kwargs["reason"]


class TestNextStatusAfterCompletedRun:

    def test_executing_goes_to_architect_review(self):
        assert TaskEngine._next_status_after_completed_run("executing") == "architect-review"

    def test_architect_review_goes_to_code_review(self):
        assert TaskEngine._next_status_after_completed_run("architect-review") == "code-review"

    def test_code_review_goes_to_review(self):
        assert TaskEngine._next_status_after_completed_run("code-review") == "review"

    def test_unknown_defaults_to_review(self):
        assert TaskEngine._next_status_after_completed_run("unknown") == "review"
