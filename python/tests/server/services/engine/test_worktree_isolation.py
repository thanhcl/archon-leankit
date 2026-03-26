"""Tests for git-worktree isolation and session affinity features (C-P2-01).

Covers:
- Worktree creation when overlap detected with "on-conflict-worktree" isolation mode
- Worktree cleanup in spawn() finally block after all attempts
- "queue" isolation mode maps to same behaviour as "shared"
- isolation_override propagated through _execute_task_with_timeout → _execute_task
- run_result_summary stored in execution_result for retry session affinity
- _format_retry_feedback includes run_result_summary
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.architect_reviewer import ReviewConfig
from src.server.services.engine.cc_spawner import CCExecutionResult, CCSpawner, ProjectConfig
from src.server.services.engine.sandbox_provider import SandboxContext
from src.server.services.engine.task_engine import TaskEngine
from src.server.services.engine.prompt_builder import PromptBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides: object) -> dict:
    base: dict = {
        "id": "task-wt-01",
        "project_id": "proj-wt",
        "title": "Add auth feature",
        "description": "Implement login",
        "status": "assigned",
        "priority": "medium",
        "assignee": "Agent",
        "source_app": "leankit",
        "acceptance_criteria": [],
        "files_in_scope": ["src/auth.py"],
        "retry_count": 0,
        "architect_review": None,
        "execution_result": None,
        "rejection_reason": None,
        "execution_prompt": None,
        "allowed_paths": [],
        "forbidden_paths": [],
        "repo_guidance_packs": [],
        "created_at": "2026-01-01T00:00:00",
        "reviewed_by": [],
        "review_history": [],
        "blocked_by": [],
    }
    base.update(overrides)
    return base


def _make_engine(isolation: str = "shared") -> TaskEngine:
    engine = TaskEngine(
        project_path="/repo",
        project_id="proj-wt",
        isolation=isolation,
    )
    return engine


def _ok_result(**kwargs: object) -> CCExecutionResult:
    return CCExecutionResult(
        success=True,
        stdout='{"type":"result","subtype":"success"}\nRESULT: SUCCESS\nSUMMARY: done\nFILES_CHANGED: 2',
        stderr="",
        exit_code=0,
        duration_seconds=10.0,
        parsed={"result": "SUCCESS", "summary": "done", "files_changed": 2},
    )


def _fail_result(**kwargs: object) -> CCExecutionResult:
    return CCExecutionResult(
        success=False,
        stdout='{"type":"assistant","message":{"content":[{"type":"text","text":"Working on it"}]}}',
        stderr="exit 1",
        exit_code=1,
        duration_seconds=5.0,
        parsed={"result": "FAILURE"},
    )


# ---------------------------------------------------------------------------
# CCSpawner worktree cleanup
# ---------------------------------------------------------------------------


def _make_mock_provider(workspace_path: str) -> MagicMock:
    """Create a mock SandboxProvider that returns a fixed workspace_path."""
    mock_provider = MagicMock()
    mock_provider.acquire.return_value = SandboxContext(workspace_path=workspace_path, provider_name="git-worktree")
    return mock_provider


class TestWorktreeCleanup:
    """Verify that spawn() cleans up the worktree via provider.release() in its finally block."""

    @pytest.mark.asyncio
    async def test_cleanup_called_on_success(self) -> None:
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/repo", isolation="git-worktree")
        task_id = "task-01"
        mock_provider = _make_mock_provider("/repo/.leankit-worktrees/task-01")

        with (
            patch("src.server.services.engine.cc_spawner.get_provider_for_isolation", return_value=mock_provider),
            patch.object(spawner, "_spawn_with_model", new_callable=AsyncMock, return_value=_ok_result()),
        ):
            await spawner.spawn(task_id=task_id, prompt="do it", config=config)

        mock_provider.release.assert_called_once_with(task_id, "/repo")

    @pytest.mark.asyncio
    async def test_cleanup_called_on_failure(self) -> None:
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/repo", isolation="git-worktree")
        task_id = "task-02"
        mock_provider = _make_mock_provider("/repo/.leankit-worktrees/task-02")

        with (
            patch("src.server.services.engine.cc_spawner.get_provider_for_isolation", return_value=mock_provider),
            patch.object(spawner, "_spawn_with_model", new_callable=AsyncMock, return_value=_fail_result()),
        ):
            # Opus fallback also returns failure, but cleanup still happens
            await spawner.spawn(task_id=task_id, prompt="do it", config=config)

        mock_provider.release.assert_called_once_with(task_id, "/repo")

    @pytest.mark.asyncio
    async def test_no_cleanup_for_shared_isolation(self) -> None:
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/repo", isolation="shared")
        task_id = "task-03"
        mock_provider = MagicMock()
        mock_provider.acquire.return_value = SandboxContext(workspace_path="/repo", provider_name="local-directory")

        with (
            patch("src.server.services.engine.cc_spawner.get_provider_for_isolation", return_value=mock_provider),
            patch.object(spawner, "_spawn_with_model", new_callable=AsyncMock, return_value=_ok_result()),
        ):
            await spawner.spawn(task_id=task_id, prompt="do it", config=config)

        # release is still called (it's a no-op for LocalDirectoryProvider), but
        # the key assertion is that the provider used is the local-directory one.
        mock_provider.release.assert_called_once_with(task_id, "/repo")

    @pytest.mark.asyncio
    async def test_cleanup_called_even_when_spawn_raises(self) -> None:
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/repo", isolation="git-worktree")
        task_id = "task-04"
        mock_provider = _make_mock_provider("/repo/.leankit-worktrees/task-04")

        with (
            patch("src.server.services.engine.cc_spawner.get_provider_for_isolation", return_value=mock_provider),
            patch.object(spawner, "_spawn_with_model", new_callable=AsyncMock, side_effect=RuntimeError("crash")),
        ):
            with pytest.raises(RuntimeError):
                await spawner.spawn(task_id=task_id, prompt="do it", config=config)

        mock_provider.release.assert_called_once_with(task_id, "/repo")


# ---------------------------------------------------------------------------
# Isolation mode mapping
# ---------------------------------------------------------------------------


class TestIsolationModeMapping:
    """Verify _map_worktree_mode_to_isolation handles new policy values."""

    def test_worktree_maps_to_on_conflict_worktree(self) -> None:
        assert TaskEngine._map_worktree_mode_to_isolation("worktree") == "on-conflict-worktree"

    def test_queue_maps_to_shared(self) -> None:
        assert TaskEngine._map_worktree_mode_to_isolation("queue") == "shared"

    def test_shared_unchanged(self) -> None:
        assert TaskEngine._map_worktree_mode_to_isolation("shared") == "shared"

    def test_isolated_maps_to_git_worktree(self) -> None:
        assert TaskEngine._map_worktree_mode_to_isolation("isolated") == "git-worktree"

    def test_unknown_returns_none(self) -> None:
        assert TaskEngine._map_worktree_mode_to_isolation("unknown") is None


# ---------------------------------------------------------------------------
# Conflict detection with on-conflict-worktree mode
# ---------------------------------------------------------------------------


class TestSharedConflictDetection:
    """Verify conflict detection is enabled for on-conflict-worktree mode."""

    def test_shared_mode_enables_conflict_detection(self) -> None:
        engine = _make_engine("shared")
        assert engine._shared_checkout_conflicts_enabled() is True

    def test_on_conflict_worktree_enables_conflict_detection(self) -> None:
        engine = _make_engine("on-conflict-worktree")
        assert engine._shared_checkout_conflicts_enabled() is True

    def test_git_worktree_disables_conflict_detection(self) -> None:
        engine = _make_engine("git-worktree")
        assert engine._shared_checkout_conflicts_enabled() is False


# ---------------------------------------------------------------------------
# Poll cycle: isolation_override passed on conflict
# ---------------------------------------------------------------------------


class TestPollCycleWorktreeOverride:
    """Verify poll cycle passes isolation_override=git-worktree when conflict detected."""

    @pytest.mark.asyncio
    async def test_conflict_triggers_worktree_override(self) -> None:
        """Conflict detected + on-conflict-worktree → task is spawned with isolation_override=git-worktree."""
        engine = _make_engine("on-conflict-worktree")
        active_task = _make_task(id="task-active", allowed_paths=["src/auth.py"])
        candidate_task = _make_task(id="task-candidate", allowed_paths=["src/auth.py"])

        async def _noop_execute(*a: object, **kw: object) -> CCExecutionResult:
            return _ok_result()

        mock_execute = MagicMock(side_effect=_noop_execute)

        with (
            patch.object(engine.task_service, "list_tasks", return_value=(True, {"tasks": [candidate_task]})),
            patch.object(engine, "_fetch_active_conflict_tasks", return_value=[active_task]),
            patch.object(engine, "_filter_blocked_tasks", return_value=[candidate_task]),
            patch.object(engine, "_has_capacity", return_value=True),
            patch.object(engine, "_assign_approved_tasks", new_callable=AsyncMock),
            patch.object(engine.cost_budget_service, "check_budget", return_value=(True, {})),
            patch.object(engine, "_execute_task_with_timeout", mock_execute),
        ):
            engine._poll_cycle_count = 0
            await engine._poll_cycle()
            # Drain scheduled tasks so create_task coroutines don't leak
            await asyncio.sleep(0)

        mock_execute.assert_called_once()
        _, call_kwargs = mock_execute.call_args
        assert call_kwargs.get("isolation_override") == "git-worktree"

    @pytest.mark.asyncio
    async def test_no_conflict_no_override(self) -> None:
        """No conflict → task is spawned with isolation_override=None."""
        engine = _make_engine("on-conflict-worktree")
        candidate_task = _make_task(id="task-candidate", allowed_paths=["src/auth.py"])

        async def _noop_execute(*a: object, **kw: object) -> CCExecutionResult:
            return _ok_result()

        mock_execute = MagicMock(side_effect=_noop_execute)

        with (
            patch.object(engine.task_service, "list_tasks", return_value=(True, {"tasks": [candidate_task]})),
            patch.object(engine, "_fetch_active_conflict_tasks", return_value=[]),
            patch.object(engine, "_filter_blocked_tasks", return_value=[candidate_task]),
            patch.object(engine, "_has_capacity", return_value=True),
            patch.object(engine, "_assign_approved_tasks", new_callable=AsyncMock),
            patch.object(engine.cost_budget_service, "check_budget", return_value=(True, {})),
            patch.object(engine, "_execute_task_with_timeout", mock_execute),
        ):
            engine._poll_cycle_count = 0
            await engine._poll_cycle()
            await asyncio.sleep(0)

        mock_execute.assert_called_once()
        _, call_kwargs = mock_execute.call_args
        assert call_kwargs.get("isolation_override") is None

    @pytest.mark.asyncio
    async def test_shared_mode_skips_conflicting_task(self) -> None:
        """In shared mode, conflicting task is queued (skipped this cycle)."""
        engine = _make_engine("shared")
        active_task = _make_task(id="task-active", allowed_paths=["src/auth.py"])
        candidate_task = _make_task(id="task-candidate", allowed_paths=["src/auth.py"])

        async def _noop_execute(*a: object, **kw: object) -> CCExecutionResult:
            return _ok_result()

        mock_execute = MagicMock(side_effect=_noop_execute)

        with (
            patch.object(engine.task_service, "list_tasks", return_value=(True, {"tasks": [candidate_task]})),
            patch.object(engine, "_fetch_active_conflict_tasks", return_value=[active_task]),
            patch.object(engine, "_filter_blocked_tasks", return_value=[candidate_task]),
            patch.object(engine, "_has_capacity", return_value=True),
            patch.object(engine, "_assign_approved_tasks", new_callable=AsyncMock),
            patch.object(engine.cost_budget_service, "check_budget", return_value=(True, {})),
            patch.object(engine, "_execute_task_with_timeout", mock_execute),
        ):
            engine._poll_cycle_count = 0
            await engine._poll_cycle()
            await asyncio.sleep(0)

        # Task should be skipped (queued), not executed
        mock_execute.assert_not_called()


# ---------------------------------------------------------------------------
# Session affinity: run_result_summary in retry prompt
# ---------------------------------------------------------------------------


class TestSessionAffinityRetryPrompt:
    """Verify _format_retry_feedback includes run_result_summary for session affinity."""

    def _make_pb(self) -> PromptBuilder:
        return PromptBuilder(learning_processor=None)

    def test_run_result_summary_included_in_retry_feedback(self) -> None:
        pb = self._make_pb()
        task = {
            "id": "task-001",
            "retry_count": 1,
            "execution_result": {
                "result": "FAILURE",
                "summary": "Tests failed",
                "run_result_summary": "Tests failed | files_modified=3 | tests_passed=0",
            },
            "architect_review": None,
            "rejection_reason": None,
        }
        feedback = pb._format_retry_feedback(task)
        assert feedback is not None
        assert "files_modified=3" in feedback
        assert "Previous run:" in feedback

    def test_run_result_summary_not_duplicated_when_same_as_summary(self) -> None:
        pb = self._make_pb()
        task = {
            "id": "task-002",
            "retry_count": 1,
            "execution_result": {
                "result": "FAILURE",
                "summary": "Tests failed",
                "run_result_summary": "Tests failed",  # same content
            },
            "architect_review": None,
            "rejection_reason": None,
        }
        feedback = pb._format_retry_feedback(task)
        assert feedback is not None
        # "Previous run:" label should NOT appear when run_result_summary == error_msg
        assert "Previous run:" not in feedback

    def test_no_run_result_summary_gracefully_handled(self) -> None:
        pb = self._make_pb()
        task = {
            "id": "task-003",
            "retry_count": 1,
            "execution_result": {
                "result": "FAILURE",
                "summary": "Build error",
            },
            "architect_review": None,
            "rejection_reason": None,
        }
        feedback = pb._format_retry_feedback(task)
        assert feedback is not None
        assert "Build error" in feedback

    def test_zero_retry_count_returns_none(self) -> None:
        pb = self._make_pb()
        task = {
            "id": "task-004",
            "retry_count": 0,
            "execution_result": {"run_result_summary": "done"},
        }
        assert pb._format_retry_feedback(task) is None


# ---------------------------------------------------------------------------
# run_result_summary stored in execution_result
# ---------------------------------------------------------------------------


class TestRunResultSummaryStorage:
    """Verify task_engine stores run_result_summary in execution_result."""

    @pytest.mark.asyncio
    async def test_run_result_summary_stored_on_success(self) -> None:
        engine = _make_engine()
        stored_execution_result: list[dict] = []

        full_task = _make_task(
            id="task-rrs",
            project_id="proj-wt",
            status="executing",
        )

        result = CCExecutionResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=5.0,
            parsed={
                "result": "SUCCESS",
                "summary": "Auth implemented",
                "files_changed": 3,
                "tests_added": 2,
            },
        )

        async def fake_update_task(task_id: str, update_fields: dict) -> tuple:
            if "execution_result" in update_fields:
                stored_execution_result.append(update_fields["execution_result"])
            return (True, {})

        with (
            patch.object(engine.task_service, "get_task", return_value=(True, {"task": full_task})),
            patch.object(engine.task_service, "update_task", side_effect=fake_update_task),
            patch.object(engine, "_update_execution_run", new_callable=AsyncMock),
            patch.object(engine, "_build_boundary_validation", new_callable=AsyncMock, return_value={"status": "not-configured", "violations": []}),
            patch.object(engine, "_run_architect_review", new_callable=AsyncMock),
            patch.object(engine.lifecycle_service, "execute_transition", new_callable=AsyncMock, return_value=(True, {})),
            patch.object(engine.notifier, "on_task_completed", new_callable=AsyncMock),
            patch.object(engine.cost_budget_service, "record_task_cost"),
        ):
            await engine._on_cc_complete("task-rrs", result)

        assert stored_execution_result, "execution_result should have been stored"
        exec_result = stored_execution_result[0]
        assert exec_result.get("run_result_summary") == "Auth implemented | files_modified=3 | tests_passed=2"

    @pytest.mark.asyncio
    async def test_partial_context_stored_for_non_timeout_failure(self) -> None:
        """Partial context should be extracted even for non-timeout failures if CC wrote files."""
        engine = _make_engine()
        stored_execution_result: list[dict] = []

        full_task = _make_task(id="task-partial", project_id="proj-wt", status="executing")

        # CC output with a Write tool call but no timeout
        cc_stdout = '{"type":"tool_use","name":"Write","input":{"file_path":"src/auth.py"}}\n'
        result = CCExecutionResult(
            success=False,
            stdout=cc_stdout,
            stderr="exit 1",
            exit_code=1,
            duration_seconds=3.0,
            timed_out=False,
            parsed={"result": "FAILURE"},
        )

        async def fake_update_task(task_id: str, update_fields: dict) -> tuple:
            if "execution_result" in update_fields:
                stored_execution_result.append(update_fields["execution_result"])
            return (True, {})

        with (
            patch.object(engine.task_service, "get_task", return_value=(True, {"task": full_task})),
            patch.object(engine.task_service, "update_task", side_effect=fake_update_task),
            patch.object(engine, "_update_execution_run", new_callable=AsyncMock),
            patch.object(engine, "_build_boundary_validation", new_callable=AsyncMock, return_value={"status": "not-configured", "violations": []}),
            patch.object(engine, "_handle_task_failure", new_callable=AsyncMock),
            patch.object(engine.lifecycle_service, "execute_transition", new_callable=AsyncMock, return_value=(True, {})),
            patch.object(engine.notifier, "on_task_completed", new_callable=AsyncMock),
            patch.object(engine.cost_budget_service, "record_task_cost"),
        ):
            await engine._on_cc_complete("task-partial", result)

        assert stored_execution_result, "execution_result should have been stored"
        exec_result = stored_execution_result[0]
        partial_ctx = exec_result.get("partial_context")
        assert partial_ctx is not None, "partial_context should be stored for non-timeout failures with file writes"
        assert "src/auth.py" in partial_ctx.get("files_modified", [])
