"""Tests for structured retry context injection into execution runs (B-P4-01).

Verifies that _stage_guidance_injection fetches review feedback for retry runs
and stores retry_context in run metadata.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.task_engine import TaskEngine, TaskExecutionState


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
        "title": "Fix auth bug",
        "description": "Auth tokens expire incorrectly",
        "status": "assigned",
        "priority": "medium",
        "assignee": "Agent",
        "source_app": "leankit",
        "acceptance_criteria": [],
        "retry_count": 0,
        "max_retries": 3,
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
    }
    base.update(overrides)
    return base


def _make_feedback(**overrides):
    base = {
        "id": "fb-001",
        "task_id": "task-001",
        "run_id": "run-prev",
        "contract_revision": 1,
        "overall_score": 4.0,
        "verdict": "changes-requested",
        "reviewer_identity": "code-review",
        "suggested_retry_direction": "Fix the timezone handling in token expiry",
        "findings": [
            {"criterion": "Token expiry", "score": 2.0, "passed": False, "details": "Wrong timezone"},
            {"criterion": "Error handling", "score": 8.0, "passed": True, "details": "OK"},
        ],
    }
    base.update(overrides)
    return base


def _make_engine():
    with patch("src.server.services.engine.task_engine.TaskService"), \
         patch("src.server.services.engine.task_engine.TaskLifecycleService"), \
         patch("src.server.services.engine.task_engine.ArchitectReviewer"), \
         patch("src.server.services.engine.task_engine.Notifier"), \
         patch("src.server.services.engine.task_engine.LearningProcessor"), \
         patch("src.server.services.engine.task_engine.PromptBuilder") as MockPB, \
         patch("src.server.services.engine.task_engine.HealthMonitor"), \
         patch("src.server.services.engine.task_engine.CCSpawner"):
        engine = TaskEngine(project_path="/tmp/test", project_id="proj-001")
        engine.prompt_builder.build = AsyncMock(return_value=("prompt text", {
            "learnings": 0, "patterns": 0, "kb_chunks": 0, "tokens": 100,
        }))
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


class TestGuidanceInjectionRetryContext:

    @pytest.mark.asyncio
    async def test_first_attempt_no_feedback_fetched(self):
        """retry_count=0 should NOT fetch review feedback."""
        engine = _make_engine()
        engine._fetch_latest_review_feedback = AsyncMock(return_value=None)

        state = _make_state(task=_make_task(retry_count=0))
        result = await engine._stage_guidance_injection(state)

        engine._fetch_latest_review_feedback.assert_not_called()
        assert result.run_metadata is None

    @pytest.mark.asyncio
    async def test_retry_fetches_feedback(self):
        """retry_count>0 should fetch and inject review feedback."""
        engine = _make_engine()
        feedback = _make_feedback()
        engine._fetch_latest_review_feedback = AsyncMock(return_value=feedback)

        state = _make_state(task=_make_task(retry_count=1))
        result = await engine._stage_guidance_injection(state)

        engine._fetch_latest_review_feedback.assert_called_once_with("task-001")
        # review_feedback should be passed to prompt_builder.build
        build_call = engine.prompt_builder.build.call_args
        assert build_call[1]["review_feedback"] == feedback

    @pytest.mark.asyncio
    async def test_retry_context_stored_in_metadata(self):
        """retry_context summary should be stored in run_metadata."""
        engine = _make_engine()
        feedback = _make_feedback()
        engine._fetch_latest_review_feedback = AsyncMock(return_value=feedback)

        state = _make_state(task=_make_task(retry_count=2))
        result = await engine._stage_guidance_injection(state)

        assert result.run_metadata is not None
        ctx = result.run_metadata["retry_context"]
        assert ctx["source_run_id"] == "run-prev"
        assert ctx["verdict"] == "changes-requested"
        assert ctx["reviewer_identity"] == "code-review"
        assert ctx["findings_count"] == 2
        assert "Token expiry" in ctx["failed_criteria"]
        assert "Error handling" not in ctx["failed_criteria"]  # passed=True
        assert ctx["suggested_direction"] == "Fix the timezone handling in token expiry"
        assert ctx["contract_revision"] == 1

    @pytest.mark.asyncio
    async def test_retry_no_feedback_available(self):
        """retry_count>0 but no feedback should still proceed without crash."""
        engine = _make_engine()
        engine._fetch_latest_review_feedback = AsyncMock(return_value=None)

        state = _make_state(task=_make_task(retry_count=1))
        result = await engine._stage_guidance_injection(state)

        # Should pass None as review_feedback to prompt builder
        build_call = engine.prompt_builder.build.call_args
        assert build_call[1]["review_feedback"] is None
        # No retry_context in metadata
        assert result.run_metadata is None

    @pytest.mark.asyncio
    async def test_metadata_preserved_in_artifact_capture(self):
        """retry_context set in guidance stage must survive artifact capture."""
        engine = _make_engine()
        feedback = _make_feedback()
        engine._fetch_latest_review_feedback = AsyncMock(return_value=feedback)

        state = _make_state(task=_make_task(retry_count=1))
        result = await engine._stage_guidance_injection(state)

        # Simulate what _stage_artifact_capture does: merging prior metadata
        prior = result.run_metadata or {}
        merged = {
            **prior,
            "result": "SUCCESS",
            "runner_key": "claude-code-cli",
        }

        assert "retry_context" in merged
        assert merged["retry_context"]["source_run_id"] == "run-prev"
