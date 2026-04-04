"""Tests for pre-execution scope validation gate (B-P4-02)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.task_engine import TaskEngine, TaskExecutionState


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
        "title": "Fix auth bug",
        "status": "assigned",
        "priority": "medium",
        "blocked_by": [],
        "allowed_paths": [],
        "forbidden_paths": [],
        "current_contract": None,
        "created_at": "2026-01-01T00:00:00",
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
        return engine


def _make_state(task=None, **overrides):
    t = task or _make_task()
    defaults = {
        "task": t,
        "task_id": t["id"],
        "full_task": t,
    }
    defaults.update(overrides)
    return TaskExecutionState(**defaults)


class TestPreValidationGate:

    @pytest.mark.asyncio
    async def test_passes_when_no_issues(self):
        engine = _make_engine()
        state = _make_state()

        result = await engine._stage_pre_validation(state)

        assert result.final_result is None  # no early exit

    @pytest.mark.asyncio
    async def test_blocks_unlocked_contract(self):
        engine = _make_engine()
        task = _make_task(current_contract={
            "negotiation_status": "draft",
            "version": 1,
        })
        state = _make_state(task=task)

        result = await engine._stage_pre_validation(state)

        assert result.final_result is not None
        assert result.final_result.success is False
        assert "Contract not locked" in result.final_result.stderr

    @pytest.mark.asyncio
    async def test_passes_locked_contract(self):
        engine = _make_engine()
        task = _make_task(current_contract={
            "negotiation_status": "locked",
            "version": 1,
        })
        state = _make_state(task=task)

        result = await engine._stage_pre_validation(state)

        assert result.final_result is None

    @pytest.mark.asyncio
    async def test_blocks_invalid_allowed_path(self):
        engine = _make_engine()
        task = _make_task(allowed_paths=["src/", "", "tests/"])
        state = _make_state(task=task)

        result = await engine._stage_pre_validation(state)

        assert result.final_result is not None
        assert "Invalid allowed_path" in result.final_result.stderr

    @pytest.mark.asyncio
    async def test_passes_valid_allowed_paths(self):
        engine = _make_engine()
        task = _make_task(allowed_paths=["src/", "tests/"])
        state = _make_state(task=task)

        result = await engine._stage_pre_validation(state)

        assert result.final_result is None

    @pytest.mark.asyncio
    async def test_blocks_unresolved_dependencies(self):
        engine = _make_engine()
        task = _make_task(blocked_by=["blocker-1"])
        state = _make_state(task=task)

        # Mock _recheck_blocked_by to return unresolved
        engine._recheck_blocked_by = MagicMock(return_value=["blocker-1"])

        result = await engine._stage_pre_validation(state)

        assert result.final_result is not None
        assert "Unresolved blockers" in result.final_result.stderr

    @pytest.mark.asyncio
    async def test_passes_resolved_dependencies(self):
        engine = _make_engine()
        task = _make_task(blocked_by=["blocker-1"])
        state = _make_state(task=task)

        engine._recheck_blocked_by = MagicMock(return_value=[])

        result = await engine._stage_pre_validation(state)

        assert result.final_result is None

    @pytest.mark.asyncio
    async def test_emits_pre_validation_failed_event(self):
        engine = _make_engine()
        task = _make_task(current_contract={
            "negotiation_status": "negotiating",
            "version": 1,
        })
        state = _make_state(task=task)

        await engine._stage_pre_validation(state)

        engine.notifier.emit.assert_called_once()
        call_args = engine.notifier.emit.call_args
        assert call_args[0][0] == "engine.pre_validation_failed"

    @pytest.mark.asyncio
    async def test_no_full_task_passes_through(self):
        engine = _make_engine()
        state = _make_state()
        state.full_task = None

        result = await engine._stage_pre_validation(state)

        assert result.final_result is None

    @pytest.mark.asyncio
    async def test_no_contract_passes(self):
        engine = _make_engine()
        task = _make_task(current_contract=None)
        state = _make_state(task=task)

        result = await engine._stage_pre_validation(state)

        assert result.final_result is None

    @pytest.mark.asyncio
    async def test_multiple_issues_combined(self):
        engine = _make_engine()
        task = _make_task(
            current_contract={"negotiation_status": "draft"},
            allowed_paths=["", "src/"],
        )
        state = _make_state(task=task)

        result = await engine._stage_pre_validation(state)

        assert result.final_result is not None
        assert "Contract not locked" in result.final_result.stderr
        assert "Invalid allowed_path" in result.final_result.stderr
