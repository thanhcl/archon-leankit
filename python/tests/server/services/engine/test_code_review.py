"""Tests for independent code review stage (Stage 2) in TaskEngine."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.architect_reviewer import ArchitectReviewResult, ReviewAction
from src.server.services.engine.cc_spawner import CCExecutionResult, MODEL_SONNET
from src.server.services.engine.task_engine import TaskEngine


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
        "title": "Add auth endpoint",
        "description": "Implement login",
        "status": "code-review",
        "priority": "high",
        "assignee": "Agent",
        "source_app": "leankit",
        "acceptance_criteria": [{"text": "Login works"}],
        "retry_count": 0,
        "review_cycle": 0,
        "architect_review": None,
        "execution_result": None,
        "rejection_reason": None,
        "execution_prompt": None,
        "created_at": "2026-01-01T00:00:00",
    }
    base.update(overrides)
    return base


def _success_result():
    return CCExecutionResult(
        success=True,
        stdout="SELF_REVIEW: PASS\nRESULT: SUCCESS\nFILES_CHANGED: 2\nSUMMARY: Done\n",
        stderr="", exit_code=0, duration_seconds=30.0,
        parsed={"result": "SUCCESS", "files_changed": 2, "summary": "Done"},
    )


def _code_review_approve_result():
    return CCExecutionResult(
        success=True,
        stdout="CODE_REVIEW_VERDICT: APPROVE\nCODE_REVIEW_FINDINGS: []\n",
        stderr="", exit_code=0, duration_seconds=20.0,
        parsed={
            "code_review_verdict": "APPROVE",
            "code_review_findings": [],
        },
    )


def _code_review_changes_result():
    return CCExecutionResult(
        success=True,
        stdout=(
            'CODE_REVIEW_VERDICT: REQUEST_CHANGES\n'
            'CODE_REVIEW_FINDINGS: [{"severity":"warning","category":"security",'
            '"description":"Missing input validation","file":"auth.py","line":"42"}]\n'
        ),
        stderr="", exit_code=0, duration_seconds=25.0,
        parsed={
            "code_review_verdict": "REQUEST_CHANGES",
            "code_review_findings": [
                {"severity": "warning", "category": "security",
                 "description": "Missing input validation", "file": "auth.py", "line": "42"},
            ],
        },
    )


def _code_review_unknown_result():
    return CCExecutionResult(
        success=True,
        stdout="Some output without verdict\n",
        stderr="", exit_code=0, duration_seconds=15.0,
        parsed={},
    )


def _mock_reviewer_approve():
    return (
        ArchitectReviewResult(verdict="approve", confidence=0.95, summary="Good", mode="self-review"),
        ReviewAction(next_status="review", reason="Approved"),
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
    engine.execution_run_service = MagicMock()
    engine.execution_run_service.create_run = AsyncMock(return_value=(True, {"run": {"id": "run-code-review-001"}}))
    engine.execution_run_service.update_run = AsyncMock(return_value=(True, {}))
    engine.prompt_builder = MagicMock()
    engine.prompt_builder.build = AsyncMock(return_value=("test prompt", {"learnings": 0, "patterns": 0, "kb_chunks": 0, "tokens": 100}))
    engine.spawner = MagicMock()
    engine.spawner.has_capacity = True
    engine.spawner.spawn = AsyncMock(return_value=_success_result())
    engine.architect_reviewer = MagicMock()
    engine.architect_reviewer.review = AsyncMock(return_value=_mock_reviewer_approve())
    engine.notifier = MagicMock()
    engine.notifier.on_task_started = AsyncMock()
    engine.notifier.on_task_completed = AsyncMock()
    engine.notifier.on_task_review_ready = AsyncMock()
    engine.notifier.on_task_escalated = AsyncMock()
    engine.notifier.on_task_failed = AsyncMock()
    engine.notifier.on_code_review_changes_requested = AsyncMock()
    engine.health_monitor = MagicMock()
    engine.health_monitor.start = AsyncMock()
    engine.health_monitor.stop = AsyncMock()
    engine.cost_budget_service = MagicMock()
    engine.cost_budget_service.record_task_cost = MagicMock()
    engine.bug_task_creator = MagicMock()
    engine.bug_task_creator.create_bug_tasks_from_findings = AsyncMock(return_value=[])
    engine.learning_processor = MagicMock()
    engine.learning_processor.process = AsyncMock()
    return engine


class TestCodeReviewApprove:
    """Code review approves → task transitions to 'review' (Owner)."""

    @pytest.mark.asyncio
    async def test_approve_transitions_to_review(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_code_review_approve_result())
        engine._get_git_diff = AsyncMock(return_value="diff --git a/auth.py\n+login code")

        await engine._run_code_review("task-001", {"result": "SUCCESS"})

        # Should transition to 'review'
        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        review_call = [c for c in transition_calls if c[1].get("new_status") == "review"]
        assert len(review_call) == 1
        assert review_call[0][1]["changed_by"] == "code-reviewer"

    @pytest.mark.asyncio
    async def test_approve_notifies_review_ready(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_code_review_approve_result())
        engine._get_git_diff = AsyncMock(return_value="diff")

        await engine._run_code_review("task-001", {})

        engine.notifier.on_task_review_ready.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_stores_code_review_data(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_code_review_approve_result())
        engine._get_git_diff = AsyncMock(return_value="diff")

        await engine._run_code_review("task-001", {})

        update_call = engine.task_service.update_task.call_args
        code_review = update_call[1]["update_fields"]["code_review"]
        assert code_review["verdict"] == "APPROVE"
        assert code_review["model"] == MODEL_SONNET
        engine.execution_run_service.create_run.assert_awaited_once()
        engine.execution_run_service.update_run.assert_awaited_once()


class TestCodeReviewRequestChanges:
    """Code review requests changes → task sent back to 'assigned'."""

    @pytest.mark.asyncio
    async def test_request_changes_sends_back_to_assigned(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_code_review_changes_result())
        engine._get_git_diff = AsyncMock(return_value="diff")

        await engine._run_code_review("task-001", {})

        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        assigned_call = [c for c in transition_calls if c[1].get("new_status") == "assigned"]
        assert len(assigned_call) == 1
        assert assigned_call[0][1]["changed_by"] == "code-reviewer"
        assert "Missing input validation" in assigned_call[0][1]["reason"]

    @pytest.mark.asyncio
    async def test_request_changes_notifies(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_code_review_changes_result())
        engine._get_git_diff = AsyncMock(return_value="diff")

        await engine._run_code_review("task-001", {})

        engine.notifier.on_code_review_changes_requested.assert_called_once()

    @pytest.mark.asyncio
    async def test_request_changes_stores_findings(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_code_review_changes_result())
        engine._get_git_diff = AsyncMock(return_value="diff")

        await engine._run_code_review("task-001", {})

        update_call = engine.task_service.update_task.call_args
        code_review = update_call[1]["update_fields"]["code_review"]
        assert code_review["verdict"] == "REQUEST_CHANGES"
        assert len(code_review["findings"]) == 1
        assert code_review["findings"][0]["category"] == "security"


class TestCodeReviewMaxCycles:
    """After MAX_CODE_REVIEW_CYCLES, task should escalate."""

    @pytest.mark.asyncio
    async def test_escalates_after_max_cycles(self):
        engine = _setup_engine()
        engine.task_service.get_task.return_value = (
            True, {"task": _make_task(review_cycle=2)}
        )

        await engine._run_code_review("task-001", {})

        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        escalated_call = [c for c in transition_calls if c[1].get("new_status") == "escalated"]
        assert len(escalated_call) == 1
        assert "Max code review cycles" in escalated_call[0][1]["reason"]

    @pytest.mark.asyncio
    async def test_escalation_notifies(self):
        engine = _setup_engine()
        engine.task_service.get_task.return_value = (
            True, {"task": _make_task(review_cycle=2)}
        )

        await engine._run_code_review("task-001", {})

        engine.notifier.on_task_escalated.assert_called_once()

    @pytest.mark.asyncio
    async def test_cycle_1_does_not_escalate(self):
        engine = _setup_engine()
        engine.task_service.get_task.return_value = (
            True, {"task": _make_task(review_cycle=1)}
        )
        engine.spawner.spawn = AsyncMock(return_value=_code_review_approve_result())
        engine._get_git_diff = AsyncMock(return_value="diff")

        await engine._run_code_review("task-001", {})

        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        escalated_calls = [c for c in transition_calls if c[1].get("new_status") == "escalated"]
        assert len(escalated_calls) == 0


class TestCodeReviewUnknownVerdict:
    """Unknown or missing verdict → escalate."""

    @pytest.mark.asyncio
    async def test_unknown_verdict_escalates(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_code_review_unknown_result())
        engine._get_git_diff = AsyncMock(return_value="diff")

        await engine._run_code_review("task-001", {})

        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        escalated_call = [c for c in transition_calls if c[1].get("new_status") == "escalated"]
        assert len(escalated_call) == 1


class TestCodeReviewUseSonnetModel:
    """Reviewer CC session must use Sonnet model for cost saving."""

    @pytest.mark.asyncio
    async def test_uses_sonnet_model(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_code_review_approve_result())
        engine._get_git_diff = AsyncMock(return_value="diff")

        await engine._run_code_review("task-001", {})

        spawn_call = engine.spawner.spawn.call_args
        config = spawn_call[1]["config"]
        assert config.force_model == MODEL_SONNET

    @pytest.mark.asyncio
    async def test_uses_review_task_id_suffix(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_code_review_approve_result())
        engine._get_git_diff = AsyncMock(return_value="diff")

        await engine._run_code_review("task-001", {})

        spawn_call = engine.spawner.spawn.call_args
        assert spawn_call[1]["task_id"] == "task-001-review"


class TestCodeReviewPrompt:
    """The reviewer prompt should be different from the coder prompt."""

    def test_prompt_contains_review_instructions(self):
        task = _make_task()
        prompt = TaskEngine._build_code_review_prompt(task, "diff content")

        assert "Independent Code Review" in prompt
        assert "You are NOT the author" in prompt
        assert "CODE_REVIEW_VERDICT" in prompt
        assert "APPROVE|REQUEST_CHANGES" in prompt

    def test_prompt_includes_task_info(self):
        task = _make_task(title="Fix auth bug", description="Fix the login endpoint")
        prompt = TaskEngine._build_code_review_prompt(task, "diff content")

        assert "Fix auth bug" in prompt
        assert "Fix the login endpoint" in prompt
        assert "Login works" in prompt  # acceptance criteria

    def test_prompt_includes_diff(self):
        task = _make_task()
        diff = "+def login():\n+    return True"
        prompt = TaskEngine._build_code_review_prompt(task, diff)

        assert diff in prompt

    def test_prompt_truncates_large_diff(self):
        task = _make_task()
        large_diff = "x" * 100000
        prompt = TaskEngine._build_code_review_prompt(task, large_diff)

        # Should truncate to 50000 chars
        assert len(prompt) < 60000


class TestArchitectReviewRoutesToCodeReview:
    """When architect review approves, flow should go through code-review."""

    @pytest.mark.asyncio
    async def test_approve_routes_to_code_review(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        engine._run_code_review = AsyncMock()

        await engine._execute_task(_make_task(status="assigned"))

        engine._run_code_review.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_transitions_to_code_review_status(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        engine._run_code_review = AsyncMock()

        await engine._execute_task(_make_task(status="assigned"))

        # Check that architect-review → code-review transition was made
        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        code_review_call = [c for c in transition_calls if c[1].get("new_status") == "code-review"]
        assert len(code_review_call) == 1

    @pytest.mark.asyncio
    async def test_escalation_skips_code_review(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        engine.architect_reviewer.review = AsyncMock(return_value=(
            ArchitectReviewResult(verdict="escalate", confidence=0.0, summary="Human needed"),
            ReviewAction(next_status="escalated", reason="Escalated", escalation_reason="reviewer_escalate"),
        ))
        engine._run_code_review = AsyncMock()

        await engine._execute_task(_make_task(status="assigned"))

        engine._run_code_review.assert_not_called()
        engine.notifier.on_task_escalated.assert_called_once()
