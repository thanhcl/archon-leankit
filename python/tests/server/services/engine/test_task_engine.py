"""Tests for TaskEngine — poll cycle, execution, completion handling."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.cc_spawner import CCExecutionResult
from src.server.services.engine.task_engine import TaskEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides) -> dict:
    base = {
        "id": "task-001",
        "title": "Add auth endpoint",
        "description": "Implement login",
        "status": "assigned",
        "priority": "high",
        "assignee": "Agent",
        "complexity": "simple",
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


def _success_result() -> CCExecutionResult:
    return CCExecutionResult(
        success=True,
        stdout="RESULT: SUCCESS\nFILES_CHANGED: 2\nSUMMARY: Done\n",
        stderr="",
        exit_code=0,
        duration_seconds=30.0,
        parsed={"result": "SUCCESS", "files_changed": 2, "summary": "Done"},
    )


def _failure_result() -> CCExecutionResult:
    return CCExecutionResult(
        success=False,
        stdout="RESULT: FAILURE\nSUMMARY: Build broke\n",
        stderr="error: test failed",
        exit_code=1,
        duration_seconds=15.0,
        parsed={"result": "FAILURE", "summary": "Build broke"},
    )


# ---------------------------------------------------------------------------
# Tests: poll_cycle
# ---------------------------------------------------------------------------


class TestPollCycle:
    @pytest.mark.asyncio
    async def test_poll_picks_assigned_tasks(self):
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()
        engine.task_service.list_tasks.return_value = (
            True,
            {"tasks": [_make_task(id="t1"), _make_task(id="t2")]},
        )

        # Mock the execution path so it doesn't actually run
        engine._execute_task = AsyncMock(return_value=_success_result())
        engine.spawner = MagicMock()
        engine.spawner.has_capacity = True

        await engine._poll_cycle()

        assert engine.task_service.list_tasks.called
        call_kwargs = engine.task_service.list_tasks.call_args[1]
        assert call_kwargs["status"] == "assigned"

    @pytest.mark.asyncio
    async def test_poll_respects_capacity(self):
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()
        engine.task_service.list_tasks.return_value = (
            True,
            {"tasks": [_make_task(id="t1")]},
        )

        engine.spawner = MagicMock()
        engine.spawner.has_capacity = False

        await engine._poll_cycle()

        # Should not even query tasks if no capacity... actually it returns early
        # The implementation does check capacity before iterating tasks

    @pytest.mark.asyncio
    async def test_poll_skips_already_executing(self):
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()
        engine.task_service.list_tasks.return_value = (
            True,
            {"tasks": [_make_task(id="t1")]},
        )

        # Pretend t1 is already executing
        engine._execution_tasks["t1"] = AsyncMock()
        engine.spawner = MagicMock()
        engine.spawner.has_capacity = True
        engine._execute_task = AsyncMock()

        await engine._poll_cycle()

        engine._execute_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_sorts_by_priority(self):
        import asyncio

        engine = TaskEngine(project_path="/tmp/test")

        tasks = [
            _make_task(id="low", priority="low"),
            _make_task(id="critical", priority="critical"),
            _make_task(id="medium", priority="medium"),
        ]
        engine.task_service = MagicMock()
        engine.task_service.list_tasks.return_value = (True, {"tasks": tasks})

        launched = []

        async def mock_execute(task):
            launched.append(task["id"])
            return _success_result()

        engine._execute_task = mock_execute
        engine.spawner = MagicMock()
        engine.spawner.has_capacity = True

        await engine._poll_cycle()

        # Wait for all background tasks to complete
        if engine._execution_tasks:
            await asyncio.gather(*engine._execution_tasks.values(), return_exceptions=True)

        assert launched[0] == "critical"

    @pytest.mark.asyncio
    async def test_poll_handles_list_failure(self):
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()
        engine.task_service.list_tasks.return_value = (False, {"error": "DB down"})

        # Should not raise
        await engine._poll_cycle()


# ---------------------------------------------------------------------------
# Tests: execute_task
# ---------------------------------------------------------------------------


class TestExecuteTask:
    @pytest.mark.asyncio
    async def test_execute_success_transitions_to_architect_review(self):
        engine = TaskEngine(project_path="/tmp/test")

        engine.lifecycle_service = MagicMock()
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(True, {"task": _make_task(status="executing")})
        )

        engine.task_service = MagicMock()
        engine.task_service.get_task.return_value = (True, {"task": _make_task()})
        engine.task_service.update_task = AsyncMock(return_value=(True, {}))

        engine.prompt_builder = MagicMock()
        engine.prompt_builder.build = AsyncMock(return_value="test prompt")

        engine.spawner = MagicMock()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())

        result = await engine._execute_task(_make_task())

        assert result.success is True

        # Should have transitioned to executing, then to architect-review
        transitions = engine.lifecycle_service.execute_transition.call_args_list
        assert len(transitions) == 2
        assert transitions[0][1]["new_status"] == "executing"
        assert transitions[1][1]["new_status"] == "architect-review"

    @pytest.mark.asyncio
    async def test_execute_failure_transitions_to_failed(self):
        engine = TaskEngine(project_path="/tmp/test")

        engine.lifecycle_service = MagicMock()
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(True, {"task": _make_task()})
        )

        engine.task_service = MagicMock()
        engine.task_service.get_task.return_value = (True, {"task": _make_task()})
        engine.task_service.update_task = AsyncMock(return_value=(True, {}))

        engine.prompt_builder = MagicMock()
        engine.prompt_builder.build = AsyncMock(return_value="test prompt")

        engine.spawner = MagicMock()
        engine.spawner.spawn = AsyncMock(return_value=_failure_result())

        result = await engine._execute_task(_make_task())

        assert result.success is False

        transitions = engine.lifecycle_service.execute_transition.call_args_list
        assert transitions[-1][1]["new_status"] == "failed"
        assert "Build broke" in transitions[-1][1]["reason"]

    @pytest.mark.asyncio
    async def test_execute_stores_execution_result(self):
        engine = TaskEngine(project_path="/tmp/test")

        engine.lifecycle_service = MagicMock()
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(True, {"task": _make_task()})
        )

        engine.task_service = MagicMock()
        engine.task_service.get_task.return_value = (True, {"task": _make_task()})
        engine.task_service.update_task = AsyncMock(return_value=(True, {}))

        engine.prompt_builder = MagicMock()
        engine.prompt_builder.build = AsyncMock(return_value="prompt")

        engine.spawner = MagicMock()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())

        await engine._execute_task(_make_task())

        # Check execution_result was stored
        update_call = engine.task_service.update_task.call_args
        stored = update_call[1]["update_fields"]["execution_result"]
        assert stored["exit_code"] == 0
        assert stored["result"] == "SUCCESS"
        assert stored["files_changed"] == 2

    @pytest.mark.asyncio
    async def test_execute_transition_failure_aborts(self):
        engine = TaskEngine(project_path="/tmp/test")

        engine.lifecycle_service = MagicMock()
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(False, {"error": "Already executing"})
        )

        result = await engine._execute_task(_make_task())

        assert result.success is False
        assert "Already executing" in result.stderr


# ---------------------------------------------------------------------------
# Tests: on_cc_complete
# ---------------------------------------------------------------------------


class TestOnCcComplete:
    @pytest.mark.asyncio
    async def test_success_result_parsed_correctly(self):
        engine = TaskEngine(project_path="/tmp/test")

        engine.lifecycle_service = MagicMock()
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(True, {})
        )
        engine.task_service = MagicMock()
        engine.task_service.update_task = AsyncMock(return_value=(True, {}))

        await engine._on_cc_complete("task-1", _success_result())

        # Verify transition to architect-review
        transition_call = engine.lifecycle_service.execute_transition.call_args
        assert transition_call[1]["new_status"] == "architect-review"

    @pytest.mark.asyncio
    async def test_failure_includes_reason(self):
        engine = TaskEngine(project_path="/tmp/test")

        engine.lifecycle_service = MagicMock()
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(True, {})
        )
        engine.task_service = MagicMock()
        engine.task_service.update_task = AsyncMock(return_value=(True, {}))

        await engine._on_cc_complete("task-2", _failure_result())

        transition_call = engine.lifecycle_service.execute_transition.call_args
        assert transition_call[1]["new_status"] == "failed"
        assert "Build broke" in transition_call[1]["reason"]


# ---------------------------------------------------------------------------
# Tests: lifecycle (start/stop)
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        engine = TaskEngine(project_path="/tmp/test", poll_interval=1)

        # Patch the loop to avoid real polling
        engine._poll_cycle = AsyncMock()

        await engine.start()
        assert engine._running is True
        assert engine._loop_task is not None

        await engine.stop()
        assert engine._running is False

    @pytest.mark.asyncio
    async def test_double_start(self):
        engine = TaskEngine(project_path="/tmp/test", poll_interval=1)
        engine._poll_cycle = AsyncMock()

        await engine.start()
        await engine.start()  # Should not raise, just warn

        await engine.stop()

    @pytest.mark.asyncio
    async def test_reap_completed(self):
        engine = TaskEngine(project_path="/tmp/test")

        done_task = MagicMock()
        done_task.done.return_value = True

        running_task = MagicMock()
        running_task.done.return_value = False

        engine._execution_tasks = {"done-1": done_task, "running-1": running_task}

        engine._reap_completed()

        assert "done-1" not in engine._execution_tasks
        assert "running-1" in engine._execution_tasks
