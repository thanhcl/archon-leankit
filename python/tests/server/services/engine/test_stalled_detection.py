"""Tests for stalled run detection and auto-recovery (D-P2-02)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.task_engine import TaskEngine


def _make_run(run_id="r1", task_id="t1", heartbeat_age_seconds=0, **overrides):
    heartbeat_at = (datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_seconds)).isoformat()
    base = {
        "id": run_id,
        "task_id": task_id,
        "status": "running",
        "stage": "execute",
        "started_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "heartbeat_at": heartbeat_at,
        "updated_at": heartbeat_at,
    }
    base.update(overrides)
    return base


def _make_engine(stalled_warning=300, stalled_kill=600):
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
        engine.notifier.on_task_failed = AsyncMock()
        engine.notifier.on_task_escalated = AsyncMock()
        engine.lifecycle_service.execute_transition = AsyncMock(return_value=(True, {}))
        engine.execution_run_service = MagicMock()
        engine.task_service.get_task = MagicMock(return_value=(True, {"task": {
            "id": "t1", "retry_count": 0, "max_retries": 3,
        }}))
        engine._kill_task_process = AsyncMock(return_value=True)
        engine._update_execution_run = AsyncMock()
        engine.engine_policy_service.get_active_policy = MagicMock(return_value={
            "capacity_policy": {
                "stalled_warning_seconds": stalled_warning,
                "stalled_kill_seconds": stalled_kill,
            },
        })
        return engine


class TestDetectStalledRuns:

    @pytest.mark.asyncio
    async def test_healthy_run_not_flagged(self):
        engine = _make_engine()
        engine.execution_run_service.list_runs.return_value = (
            True, {"runs": [_make_run(heartbeat_age_seconds=30)]}
        )

        await engine._detect_stalled_runs()

        engine.notifier.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_warning_emitted_at_threshold(self):
        engine = _make_engine(stalled_warning=100, stalled_kill=600)
        engine.execution_run_service.list_runs.return_value = (
            True, {"runs": [_make_run(heartbeat_age_seconds=150)]}
        )

        await engine._detect_stalled_runs()

        engine.notifier.emit.assert_called_once()
        event_name = engine.notifier.emit.call_args[0][0]
        assert event_name == "health.stalled_run_warning"

    @pytest.mark.asyncio
    async def test_kill_at_threshold(self):
        engine = _make_engine(stalled_warning=100, stalled_kill=200)
        engine.execution_run_service.list_runs.return_value = (
            True, {"runs": [_make_run(heartbeat_age_seconds=250)]}
        )

        await engine._detect_stalled_runs()

        engine._kill_task_process.assert_called_once_with("t1")
        engine._update_execution_run.assert_called_once()
        # Check emitted event is kill, not just warning
        calls = engine.notifier.emit.call_args_list
        kill_events = [c for c in calls if c[0][0] == "health.stalled_run_killed"]
        assert len(kill_events) == 1

    @pytest.mark.asyncio
    async def test_no_project_id_skips(self):
        engine = _make_engine()
        engine.project_id = None

        await engine._detect_stalled_runs()

        engine.execution_run_service.list_runs.assert_not_called()

    @pytest.mark.asyncio
    async def test_kill_triggers_retry_policy(self):
        engine = _make_engine(stalled_warning=100, stalled_kill=200)
        engine.execution_run_service.list_runs.return_value = (
            True, {"runs": [_make_run(heartbeat_age_seconds=250)]}
        )

        await engine._detect_stalled_runs()

        # Should have called _handle_task_failure (via lifecycle transition)
        engine.lifecycle_service.execute_transition.assert_called()

    @pytest.mark.asyncio
    async def test_multiple_runs_processed(self):
        engine = _make_engine(stalled_warning=100, stalled_kill=200)
        engine.execution_run_service.list_runs.return_value = (
            True, {"runs": [
                _make_run("r1", "t1", heartbeat_age_seconds=150),  # warning
                _make_run("r2", "t2", heartbeat_age_seconds=250),  # kill
            ]}
        )
        engine.task_service.get_task = MagicMock(return_value=(True, {"task": {
            "id": "t2", "retry_count": 0, "max_retries": 3,
        }}))

        await engine._detect_stalled_runs()

        # Both warning and kill events
        event_names = [c[0][0] for c in engine.notifier.emit.call_args_list]
        assert "health.stalled_run_warning" in event_names
        assert "health.stalled_run_killed" in event_names
