"""Tests for TaskEngine PID watchdog, timeout enforcement, and startup orphan recovery."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.cc_spawner import CCExecutionResult
from src.server.services.engine.task_engine import TaskEngine


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
        "title": "Test task",
        "status": "executing",
        "priority": "medium",
        "assignee": "Agent",
        "retry_count": 0,
        "max_retries": 3,
        "acceptance_criteria": [],
    }
    base.update(overrides)
    return base


def _setup_engine(default_timeout: int = 1800) -> TaskEngine:
    engine = TaskEngine(project_path="/tmp/test", default_timeout=default_timeout)
    engine.lifecycle_service = MagicMock()
    engine.lifecycle_service.execute_transition = AsyncMock(return_value=(True, {"task": _make_task()}))
    engine.task_service = MagicMock()
    engine.task_service.get_task.return_value = (True, {"task": _make_task()})
    engine.task_service.update_task = AsyncMock(return_value=(True, {}))
    engine.task_service.list_tasks.return_value = (True, {"tasks": []})
    engine.execution_run_service = MagicMock()
    engine.execution_run_service.create_run = AsyncMock(return_value=(True, {"run": {"id": "run-001"}}))
    engine.execution_run_service.update_run = AsyncMock(return_value=(True, {}))
    engine.execution_run_service.list_runs.return_value = (True, {"runs": []})
    engine.notifier = MagicMock()
    engine.notifier.on_task_failed = AsyncMock()
    engine.notifier.on_task_escalated = AsyncMock()
    engine.notifier.on_budget_exceeded = AsyncMock()
    engine.notifier.on_budget_warning = AsyncMock()
    engine.health_monitor = MagicMock()
    engine.health_monitor.start = AsyncMock()
    engine.health_monitor.stop = AsyncMock()
    engine.prompt_builder = MagicMock()
    engine.prompt_builder.build = AsyncMock(
        return_value=("prompt", {"learnings": 0, "patterns": 0, "kb_chunks": 0, "tokens": 100})
    )
    engine.spawner = MagicMock()
    engine.spawner.has_capacity = True
    engine.spawner.running_count = 0
    engine.spawner.max_parallel = 5
    engine.spawner.source_app = "claude-code-cli"
    engine.spawner.review_model = "claude-sonnet-4-6"
    engine.spawner.select_model.return_value = "claude-sonnet-4-6"
    engine.spawner.spawn = AsyncMock(return_value=CCExecutionResult(
        success=True, stdout="RESULT: SUCCESS\n", stderr="", exit_code=0, duration_seconds=10.0,
        parsed={"result": "SUCCESS"},
    ))
    engine.spawner.kill = AsyncMock(return_value=True)
    engine.spawner.get_process = MagicMock(return_value=None)
    return engine


class TestPidWatchdog:
    """Tests for _check_pid_liveness — watchdog that detects dead CC processes."""

    @pytest.mark.asyncio
    async def test_watchdog_no_action_when_no_execution_tasks(self):
        engine = _setup_engine()
        engine._execution_tasks = {}
        await engine._check_pid_liveness()
        engine.execution_run_service.list_runs.assert_not_called()

    @pytest.mark.asyncio
    async def test_watchdog_no_action_when_task_already_done(self):
        engine = _setup_engine()
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task  # ensure it's done
        engine._execution_tasks = {"task-001": done_task}
        await engine._check_pid_liveness()
        engine.execution_run_service.list_runs.assert_not_called()

    @pytest.mark.asyncio
    async def test_watchdog_no_action_when_process_not_tracked(self):
        """When get_process returns None the process may be in a review phase — skip it."""
        engine = _setup_engine()
        engine.spawner.get_process.return_value = None
        running_task = asyncio.create_task(asyncio.sleep(9999))
        engine._execution_tasks = {"task-001": running_task}
        try:
            await engine._check_pid_liveness()
            engine.execution_run_service.update_run.assert_not_called()
        finally:
            running_task.cancel()
            try:
                await running_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_watchdog_no_action_when_process_still_alive(self):
        """When returncode is None the process is still running — skip it."""
        engine = _setup_engine()
        mock_process = MagicMock()
        mock_process.returncode = None  # still running
        mock_process.pid = 12345
        engine.spawner.get_process.return_value = mock_process
        running_task = asyncio.create_task(asyncio.sleep(9999))
        engine._execution_tasks = {"task-001": running_task}
        try:
            await engine._check_pid_liveness()
            engine.execution_run_service.list_runs.assert_not_called()
        finally:
            running_task.cancel()
            try:
                await running_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_watchdog_force_fails_dead_process(self):
        """When returncode is non-None, process has died — engine must force-fail."""
        engine = _setup_engine()
        mock_process = MagicMock()
        mock_process.returncode = -9  # killed
        mock_process.pid = 42
        engine.spawner.get_process.return_value = mock_process

        # Set up a running execution task that will hang until cancelled
        hang_event = asyncio.Event()

        async def hanging_task():
            await hang_event.wait()

        exec_task = asyncio.create_task(hanging_task())
        engine._execution_tasks = {"task-001": exec_task}

        # Mock list_runs to return a running run
        engine.execution_run_service.list_runs.return_value = (
            True,
            {"runs": [{"id": "run-001"}]},
        )

        await engine._check_pid_liveness()

        # Task should be removed from _execution_tasks
        assert "task-001" not in engine._execution_tasks

        # Execution run should be failed
        engine.execution_run_service.update_run.assert_called()
        update_call = engine.execution_run_service.update_run.call_args
        assert update_call[0][1]["status"] == "failed"

        # Task lifecycle transition should be triggered (via _handle_task_failure)
        engine.lifecycle_service.execute_transition.assert_called()

        # Clean up
        hang_event.set()
        try:
            await exec_task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_watchdog_releases_global_capacity_slot(self):
        """When force-failing a dead process, the global capacity slot must be released."""
        engine = _setup_engine()
        engine.project_id = "proj-001"
        engine.global_tracker = MagicMock()
        engine.global_tracker.release = MagicMock()

        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.pid = 99
        engine.spawner.get_process.return_value = mock_process

        hang_event = asyncio.Event()

        async def hanging_task():
            await hang_event.wait()

        exec_task = asyncio.create_task(hanging_task())
        engine._execution_tasks = {"task-001": exec_task}

        await engine._check_pid_liveness()

        engine.global_tracker.release.assert_called_once_with("proj-001")

        hang_event.set()
        try:
            await exec_task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_watchdog_falls_back_to_inner_spawner(self):
        """Watchdog uses _spawner.get_process if spawner itself lacks get_process."""
        engine = _setup_engine()
        # Remove get_process from outer spawner
        del engine.spawner.get_process

        inner_spawner = MagicMock()
        mock_process = MagicMock()
        mock_process.returncode = None  # alive — no action expected
        mock_process.pid = 5555
        inner_spawner.get_process = MagicMock(return_value=mock_process)
        engine.spawner._spawner = inner_spawner

        running_task = asyncio.create_task(asyncio.sleep(9999))
        engine._execution_tasks = {"task-001": running_task}
        try:
            await engine._check_pid_liveness()
            engine.execution_run_service.list_runs.assert_not_called()
        finally:
            running_task.cancel()
            try:
                await running_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_watchdog_resolves_dead_process_from_codex_runner(self):
        """The watchdog must inspect the actual runner adapter, not just the legacy default spawner."""
        engine = _setup_engine()
        engine.spawner.get_process.return_value = None

        codex_runner = MagicMock()
        mock_process = MagicMock()
        mock_process.returncode = 137
        mock_process.pid = 777
        codex_runner.get_process = MagicMock(return_value=mock_process)
        engine.runner_adapters["codex-cli"] = codex_runner

        hang_event = asyncio.Event()

        async def hanging_task():
            await hang_event.wait()

        exec_task = asyncio.create_task(hanging_task())
        engine._execution_tasks = {"task-001": exec_task}
        engine.execution_run_service.list_runs.return_value = (
            True,
            {"runs": [{"id": "run-001"}]},
        )

        await engine._check_pid_liveness()

        assert "task-001" not in engine._execution_tasks
        engine.execution_run_service.update_run.assert_called()
        hang_event.set()
        try:
            await exec_task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_watchdog_reconciles_stale_untracked_execute_run(self):
        """Running execute runs that fall out of local tracking must still be force-failed."""
        engine = _setup_engine(default_timeout=300)
        engine.project_id = "proj-001"
        stale_heartbeat = (datetime.now() - timedelta(minutes=10)).isoformat() + "Z"
        engine.execution_run_service.list_runs.return_value = (
            True,
            {
                "runs": [
                    {
                        "id": "run-orphan",
                        "task_id": "task-001",
                        "stage": "execute",
                        "status": "running",
                        "heartbeat_at": stale_heartbeat,
                        "started_at": stale_heartbeat,
                    }
                ]
            },
        )

        await engine._check_pid_liveness()

        engine.execution_run_service.update_run.assert_called()
        update_call = engine.execution_run_service.update_run.call_args
        assert update_call[0][0] == "run-orphan"
        assert update_call[0][1]["status"] == "failed"


class TestStartupOrphanRecovery:
    """Tests for _startup_orphan_recovery — fails old running runs at startup."""

    @pytest.mark.asyncio
    async def test_recovery_no_runs(self):
        engine = _setup_engine()
        engine.execution_run_service.list_runs.return_value = (True, {"runs": []})
        await engine._startup_orphan_recovery()
        engine.execution_run_service.update_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_recovery_skips_recent_runs(self):
        """Runs started within the timeout threshold are NOT orphaned."""
        engine = _setup_engine(default_timeout=1800)
        # Started 10 minutes ago — well within threshold (1800 + 600 = 2400s)
        recent_started = (datetime.now() - timedelta(seconds=600)).isoformat() + "Z"
        engine.execution_run_service.list_runs.return_value = (
            True,
            {"runs": [{"id": "run-recent", "task_id": "task-001", "started_at": recent_started}]},
        )
        await engine._startup_orphan_recovery()
        engine.execution_run_service.update_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_recovery_fails_old_orphaned_runs(self):
        """Runs started beyond the timeout threshold are force-failed."""
        engine = _setup_engine(default_timeout=1800)
        # Started 10 hours ago — definitely orphaned
        old_started = (datetime.now() - timedelta(hours=10)).isoformat() + "Z"
        engine.execution_run_service.list_runs.return_value = (
            True,
            {"runs": [{"id": "run-orphan", "task_id": "task-001", "started_at": old_started}]},
        )
        await engine._startup_orphan_recovery()

        engine.execution_run_service.update_run.assert_called()
        update_call = engine.execution_run_service.update_run.call_args
        assert update_call[0][0] == "run-orphan"
        assert update_call[0][1]["status"] == "failed"
        assert "Orphaned" in update_call[0][1]["error_summary"]

    @pytest.mark.asyncio
    async def test_recovery_transitions_task_to_failed(self):
        """Orphan recovery must also transition the task via lifecycle service."""
        engine = _setup_engine(default_timeout=1800)
        old_started = (datetime.now() - timedelta(hours=8)).isoformat() + "Z"
        engine.execution_run_service.list_runs.return_value = (
            True,
            {"runs": [{"id": "run-orphan", "task_id": "task-001", "started_at": old_started}]},
        )
        await engine._startup_orphan_recovery()
        engine.lifecycle_service.execute_transition.assert_called()

    @pytest.mark.asyncio
    async def test_recovery_handles_list_runs_failure_gracefully(self):
        """If list_runs fails at startup, recovery logs and returns without crashing."""
        engine = _setup_engine()
        engine.execution_run_service.list_runs.return_value = (False, {"error": "DB connection failed"})
        # Should not raise
        await engine._startup_orphan_recovery()
        engine.execution_run_service.update_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_recovery_handles_multiple_orphaned_runs(self):
        """All orphaned runs are failed, not just the first one."""
        engine = _setup_engine(default_timeout=1800)
        old_started = (datetime.now() - timedelta(hours=12)).isoformat() + "Z"
        engine.execution_run_service.list_runs.return_value = (
            True,
            {
                "runs": [
                    {"id": "run-a", "task_id": "task-001", "started_at": old_started},
                    {"id": "run-b", "task_id": "task-002", "started_at": old_started},
                ]
            },
        )
        await engine._startup_orphan_recovery()
        # Both runs should be failed
        assert engine.execution_run_service.update_run.call_count == 2
        failed_run_ids = {call[0][0] for call in engine.execution_run_service.update_run.call_args_list}
        assert "run-a" in failed_run_ids
        assert "run-b" in failed_run_ids


class TestWatchdogLoop:
    """Tests for the _pid_watchdog_loop background task."""

    @pytest.mark.asyncio
    async def test_watchdog_loop_stops_when_engine_not_running(self):
        engine = _setup_engine()
        engine._pid_check_interval = 0  # no wait
        engine._running = False
        # Should return immediately without checking anything
        await asyncio.wait_for(engine._pid_watchdog_loop(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_watchdog_loop_calls_check_pid_liveness(self):
        engine = _setup_engine()
        engine._pid_check_interval = 0
        engine._running = True
        call_count = 0

        async def mock_check():
            nonlocal call_count
            call_count += 1
            engine._running = False  # stop after first check

        engine._check_pid_liveness = mock_check
        await asyncio.wait_for(engine._pid_watchdog_loop(), timeout=1.0)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_watchdog_loop_continues_on_check_error(self):
        """A single check failure must not crash the watchdog loop."""
        engine = _setup_engine()
        engine._pid_check_interval = 0
        engine._running = True
        call_count = 0

        async def mock_check():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated error")
            engine._running = False

        engine._check_pid_liveness = mock_check
        await asyncio.wait_for(engine._pid_watchdog_loop(), timeout=1.0)
        assert call_count == 2  # ran twice: once erroring, once stopping
