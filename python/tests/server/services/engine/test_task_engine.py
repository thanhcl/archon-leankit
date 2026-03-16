"""Tests for TaskEngine — poll cycle, execution, notification, health."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.architect_reviewer import ArchitectReviewResult, ReviewAction
from src.server.services.engine.cc_spawner import CCExecutionResult
from src.server.services.engine.task_engine import TaskEngine


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "title": "Add auth endpoint",
        "description": "Implement login",
        "status": "assigned",
        "priority": "high",
        "assignee": "Agent",
        "source_app": "leankit",
        "acceptance_criteria": [],
        "retry_count": 0,
        "architect_review": None,
        "execution_result": None,
        "rejection_reason": None,
        "execution_prompt": None,
        "created_at": "2026-01-01T00:00:00",
    }
    base.update(overrides)
    return base


def _mock_reviewer_approve():
    return (
        ArchitectReviewResult(verdict="approve", confidence=0.95, summary="Good", mode="self-review"),
        ReviewAction(next_status="review", reason="Approved"),
    )


def _success_result():
    return CCExecutionResult(
        success=True,
        stdout="SELF_REVIEW: PASS\nRESULT: SUCCESS\nFILES_CHANGED: 2\nSUMMARY: Done\n",
        stderr="", exit_code=0, duration_seconds=30.0,
        parsed={"result": "SUCCESS", "files_changed": 2, "summary": "Done"},
    )


def _failure_result():
    return CCExecutionResult(
        success=False,
        stdout="RESULT: FAILURE\nSUMMARY: Build broke\n",
        stderr="error: test failed", exit_code=1, duration_seconds=15.0,
        parsed={"result": "FAILURE", "summary": "Build broke"},
    )


def _setup_engine():
    """Create a TaskEngine with all services mocked."""
    engine = TaskEngine(project_path="/tmp/test")
    engine.lifecycle_service = MagicMock()
    engine.lifecycle_service.execute_transition = AsyncMock(
        return_value=(True, {"task": _make_task(status="executing")}),
    )
    engine.task_service = MagicMock()
    engine.task_service.get_task.return_value = (True, {"task": _make_task()})
    engine.task_service.update_task = AsyncMock(return_value=(True, {}))
    engine.prompt_builder = MagicMock()
    engine.prompt_builder.build = AsyncMock(return_value="test prompt")
    engine.spawner = MagicMock()
    engine.spawner.has_capacity = True
    engine.architect_reviewer = MagicMock()
    engine.architect_reviewer.review = AsyncMock(return_value=_mock_reviewer_approve())
    engine.notifier = MagicMock()
    engine.notifier.on_task_started = AsyncMock()
    engine.notifier.on_task_completed = AsyncMock()
    engine.notifier.on_task_review_ready = AsyncMock()
    engine.notifier.on_task_escalated = AsyncMock()
    engine.notifier.on_task_failed = AsyncMock()
    engine.health_monitor = MagicMock()
    engine.health_monitor.start = AsyncMock()
    engine.health_monitor.stop = AsyncMock()
    return engine


class TestPollCycle:
    @pytest.mark.asyncio
    async def test_poll_picks_assigned_tasks(self):
        engine = _setup_engine()
        engine.task_service.list_tasks.return_value = (True, {"tasks": [_make_task(id="t1")]})
        engine._execute_task = AsyncMock(return_value=_success_result())
        await engine._poll_cycle()
        engine.task_service.list_tasks.assert_called_once()

    @pytest.mark.asyncio
    async def test_poll_sorts_by_priority(self):
        engine = _setup_engine()
        tasks = [
            _make_task(id="low", priority="low"),
            _make_task(id="critical", priority="critical"),
        ]
        engine.task_service.list_tasks.return_value = (True, {"tasks": tasks})

        launched = []

        async def mock_execute(task):
            launched.append(task["id"])
            return _success_result()

        engine._execute_task = mock_execute
        await engine._poll_cycle()
        if engine._execution_tasks:
            await asyncio.gather(*engine._execution_tasks.values(), return_exceptions=True)
        assert launched[0] == "critical"

    @pytest.mark.asyncio
    async def test_poll_handles_failure(self):
        engine = _setup_engine()
        engine.task_service.list_tasks.return_value = (False, {"error": "DB down"})
        await engine._poll_cycle()  # Should not raise


class TestExecuteTask:
    @pytest.mark.asyncio
    async def test_success_full_pipeline(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        result = await engine._execute_task(_make_task())

        assert result.success is True
        engine.notifier.on_task_started.assert_called_once()
        engine.notifier.on_task_completed.assert_called_once()
        engine.notifier.on_task_review_ready.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_notifies(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_failure_result())
        result = await engine._execute_task(_make_task())

        assert result.success is False
        engine.notifier.on_task_started.assert_called_once()
        engine.notifier.on_task_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_escalation_notifies(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        engine.architect_reviewer.review = AsyncMock(return_value=(
            ArchitectReviewResult(verdict="escalate", confidence=0.0, summary="Human needed"),
            ReviewAction(next_status="escalated", reason="Escalated", escalation_reason="reviewer_escalate"),
        ))
        await engine._execute_task(_make_task())
        engine.notifier.on_task_escalated.assert_called_once()

    @pytest.mark.asyncio
    async def test_stores_execution_result(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        await engine._execute_task(_make_task())

        first_update = engine.task_service.update_task.call_args_list[0]
        stored = first_update[1]["update_fields"]["execution_result"]
        assert stored["exit_code"] == 0
        assert stored["result"] == "SUCCESS"

    @pytest.mark.asyncio
    async def test_transition_failure_aborts(self):
        engine = _setup_engine()
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(False, {"error": "Already executing"}),
        )
        result = await engine._execute_task(_make_task())
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unified_prompt_no_include_self_review(self):
        """build() is called without include_self_review param (unified template)."""
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        await engine._execute_task(_make_task())

        call_kwargs = engine.prompt_builder.build.call_args[1]
        assert "include_self_review" not in call_kwargs


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        engine = _setup_engine()
        engine._poll_cycle = AsyncMock()
        await engine.start()
        assert engine._running is True
        engine.health_monitor.start.assert_called_once()
        await engine.stop()
        assert engine._running is False
        engine.health_monitor.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_reap_completed(self):
        engine = _setup_engine()
        done_task = MagicMock()
        done_task.done.return_value = True
        running_task = MagicMock()
        running_task.done.return_value = False
        engine._execution_tasks = {"done-1": done_task, "running-1": running_task}
        engine._reap_completed()
        assert "done-1" not in engine._execution_tasks
        assert "running-1" in engine._execution_tasks
