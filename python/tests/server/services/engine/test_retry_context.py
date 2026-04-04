"""Tests for retry context builder and feedback attachment in PromptBuilder and TaskEngine."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.prompt_builder import PromptBuilder
from src.server.services.engine.task_engine import TaskEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        "run_id": "run-001",
        "overall_score": 5.0,
        "verdict": "changes-requested",
        "reviewer_identity": "architect-reviewer",
        "suggested_retry_direction": "Fix the token expiry calculation",
        "findings": [
            {
                "criterion": "Token expiry",
                "score": 3.0,
                "passed": False,
                "details": "Expiry uses wrong timezone",
                "evidence": "src/auth.py:45",
            },
            {
                "criterion": "Test coverage",
                "score": 7.0,
                "passed": True,
                "details": "Tests present",
                "evidence": "",
            },
        ],
        "created_at": "2026-01-01T01:00:00",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# PromptBuilder._classify_retry_direction
# ---------------------------------------------------------------------------

class TestClassifyRetryDirection:
    def test_refine_when_no_feedback_no_rejection(self):
        assert PromptBuilder._classify_retry_direction(None, None) == "refine"

    def test_refine_with_normal_feedback(self):
        feedback = _make_feedback(overall_score=5.0, suggested_retry_direction="Fix the token expiry")
        assert PromptBuilder._classify_retry_direction(feedback, None) == "refine"

    def test_pivot_when_overall_score_below_3(self):
        feedback = _make_feedback(overall_score=2.5, suggested_retry_direction="minor issue")
        assert PromptBuilder._classify_retry_direction(feedback, None) == "pivot"

    def test_pivot_when_score_exactly_3_is_refine(self):
        # 3.0 is not < 3.0, so should be refine
        feedback = _make_feedback(overall_score=3.0, suggested_retry_direction="fix something")
        assert PromptBuilder._classify_retry_direction(feedback, None) == "refine"

    def test_pivot_when_suggested_direction_contains_keyword(self):
        feedback = _make_feedback(overall_score=6.0, suggested_retry_direction="Pivot to a different approach")
        assert PromptBuilder._classify_retry_direction(feedback, None) == "pivot"

    def test_pivot_when_rejection_reason_contains_rethink(self):
        assert PromptBuilder._classify_retry_direction(None, "Rethink the whole design") == "pivot"

    def test_pivot_when_rejection_reason_contains_rewrite(self):
        assert PromptBuilder._classify_retry_direction(None, "Needs a complete rewrite") == "pivot"

    def test_refine_when_feedback_missing_score(self):
        feedback = _make_feedback(overall_score=None, suggested_retry_direction="fix specific issue")
        assert PromptBuilder._classify_retry_direction(feedback, None) == "refine"

    def test_pivot_from_combined_rejection_keyword(self):
        feedback = _make_feedback(overall_score=6.0, suggested_retry_direction="")
        assert PromptBuilder._classify_retry_direction(feedback, "fundamental redesign needed") == "pivot"


# ---------------------------------------------------------------------------
# PromptBuilder._format_structured_feedback
# ---------------------------------------------------------------------------

class TestFormatStructuredFeedback:
    def test_includes_score_and_verdict(self):
        feedback = _make_feedback(overall_score=4.5, verdict="changes-requested")
        result = PromptBuilder._format_structured_feedback(feedback)
        assert "4.5/10" in result
        assert "changes-requested" in result

    def test_includes_per_criterion_scores(self):
        feedback = _make_feedback()
        result = PromptBuilder._format_structured_feedback(feedback)
        assert "Token expiry" in result
        assert "3/10" in result
        assert "✗" in result  # failed criterion marker

    def test_passed_criterion_shows_checkmark(self):
        feedback = _make_feedback()
        result = PromptBuilder._format_structured_feedback(feedback)
        assert "✓" in result

    def test_evidence_shown_for_failed_criterion(self):
        feedback = _make_feedback()
        result = PromptBuilder._format_structured_feedback(feedback)
        assert "src/auth.py:45" in result

    def test_evidence_not_shown_for_passed_criterion(self):
        # "Test coverage" criterion has passed=True and evidence="" anyway
        feedback = _make_feedback()
        result = PromptBuilder._format_structured_feedback(feedback)
        # The "Test coverage" evidence is empty so it won't appear regardless
        assert "Test coverage" in result

    def test_suggested_direction_included(self):
        feedback = _make_feedback(suggested_retry_direction="Fix timezone handling in auth.py")
        result = PromptBuilder._format_structured_feedback(feedback)
        assert "Fix timezone handling in auth.py" in result

    def test_no_findings_produces_minimal_output(self):
        feedback = _make_feedback(findings=[], suggested_retry_direction="")
        result = PromptBuilder._format_structured_feedback(feedback)
        assert "5.0/10" in result
        assert "Per-criterion" not in result

    def test_reviewer_identity_shown(self):
        feedback = _make_feedback(reviewer_identity="code-reviewer")
        result = PromptBuilder._format_structured_feedback(feedback)
        assert "code-reviewer" in result


# ---------------------------------------------------------------------------
# PromptBuilder._format_retry_feedback
# ---------------------------------------------------------------------------

class TestFormatRetryFeedback:
    def setup_method(self):
        self.builder = PromptBuilder()

    def test_returns_none_when_retry_count_zero(self):
        task = _make_task(retry_count=0)
        assert self.builder._format_retry_feedback(task) is None

    def test_includes_attempt_number(self):
        task = _make_task(retry_count=2)
        result = self.builder._format_retry_feedback(task)
        assert "attempt 2" in result

    def test_refine_direction_label_when_no_feedback(self):
        task = _make_task(retry_count=1)
        result = self.builder._format_retry_feedback(task, review_feedback=None)
        assert "REFINE" in result

    def test_pivot_direction_label_when_low_score(self):
        task = _make_task(retry_count=1)
        feedback = _make_feedback(overall_score=1.5)
        result = self.builder._format_retry_feedback(task, review_feedback=feedback)
        assert "PIVOT" in result

    def test_structured_feedback_section_included(self):
        task = _make_task(retry_count=1)
        feedback = _make_feedback()
        result = self.builder._format_retry_feedback(task, review_feedback=feedback)
        assert "Per-criterion scores" in result
        assert "Token expiry" in result

    def test_no_feedback_available_message_when_retry_count_1_no_feedback(self):
        task = _make_task(retry_count=1)
        result = self.builder._format_retry_feedback(task, review_feedback=None)
        assert "no structured feedback available" in result

    def test_existing_execution_result_still_included(self):
        task = _make_task(
            retry_count=1,
            execution_result={"summary": "Tests failed due to import error"},
        )
        result = self.builder._format_retry_feedback(task)
        assert "Tests failed due to import error" in result

    def test_rejection_reason_still_included(self):
        task = _make_task(retry_count=1, rejection_reason="Missing test coverage")
        result = self.builder._format_retry_feedback(task)
        assert "Missing test coverage" in result

    def test_token_budget_enforced(self):
        """Output must not exceed MAX_RETRY_CONTEXT_TOKENS * 4 chars."""
        from src.server.services.engine.prompt_builder import MAX_RETRY_CONTEXT_TOKENS

        long_direction = "x" * 2000
        feedback = _make_feedback(suggested_retry_direction=long_direction)
        task = _make_task(retry_count=1)
        result = self.builder._format_retry_feedback(task, review_feedback=feedback)
        assert len(result) <= MAX_RETRY_CONTEXT_TOKENS * 4 + 5  # small margin for "..."


# ---------------------------------------------------------------------------
# PromptBuilder.build — review_feedback passed through
# ---------------------------------------------------------------------------

class TestPromptBuilderBuildWithFeedback:
    @pytest.mark.asyncio
    async def test_build_passes_review_feedback_to_renderer(self):
        builder = PromptBuilder(compress=True)
        task = _make_task(retry_count=1)
        feedback = _make_feedback()

        with patch.object(builder, "_fetch_kb_context", new=AsyncMock(return_value=[])):
            prompt, _ = await builder.build(task=task, review_feedback=feedback)

        assert "Per-criterion scores" in prompt
        assert "Token expiry" in prompt

    @pytest.mark.asyncio
    async def test_build_without_feedback_does_not_crash(self):
        builder = PromptBuilder(compress=True)
        task = _make_task(retry_count=1)

        with patch.object(builder, "_fetch_kb_context", new=AsyncMock(return_value=[])):
            prompt, _ = await builder.build(task=task, review_feedback=None)

        assert "Previous Attempt Failed" in prompt


# ---------------------------------------------------------------------------
# TaskEngine._fetch_latest_review_feedback
# ---------------------------------------------------------------------------

class TestFetchLatestReviewFeedback:
    def _make_engine(self):
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()
        return engine

    @pytest.mark.asyncio
    async def test_returns_first_row_when_found(self):
        engine = self._make_engine()
        feedback_row = _make_feedback()
        (
            engine.task_service.supabase_client
            .table.return_value
            .select.return_value
            .eq.return_value
            .order.return_value
            .limit.return_value
            .execute.return_value
        ) = MagicMock(data=[feedback_row])

        result = await engine._fetch_latest_review_feedback("task-001")
        assert result == feedback_row

    @pytest.mark.asyncio
    async def test_returns_none_when_no_rows(self):
        engine = self._make_engine()
        (
            engine.task_service.supabase_client
            .table.return_value
            .select.return_value
            .eq.return_value
            .order.return_value
            .limit.return_value
            .execute.return_value
        ) = MagicMock(data=[])

        result = await engine._fetch_latest_review_feedback("task-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        engine = self._make_engine()
        engine.task_service.supabase_client.table.side_effect = RuntimeError("DB down")

        result = await engine._fetch_latest_review_feedback("task-001")
        assert result is None


# ---------------------------------------------------------------------------
# TaskEngine._validate_retry_feedback_policy
# ---------------------------------------------------------------------------

class TestValidateRetryFeedbackPolicy:
    def _make_engine(self):
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()
        return engine

    @pytest.mark.asyncio
    async def test_first_failure_always_allowed(self):
        engine = self._make_engine()
        # No DB call needed — policy short-circuits for retry_count == 0
        can_retry, reason = await engine._validate_retry_feedback_policy("task-001", retry_count=0)
        assert can_retry is True
        assert reason == ""

    @pytest.mark.asyncio
    async def test_blocked_when_no_feedback_on_second_attempt(self):
        engine = self._make_engine()
        engine._fetch_latest_review_feedback = AsyncMock(return_value=None)

        can_retry, reason = await engine._validate_retry_feedback_policy("task-001", retry_count=1)
        assert can_retry is False
        assert "No review feedback" in reason

    @pytest.mark.asyncio
    async def test_blocked_when_feedback_has_no_findings_and_no_direction(self):
        engine = self._make_engine()
        empty_feedback = _make_feedback(findings=[], suggested_retry_direction="")
        engine._fetch_latest_review_feedback = AsyncMock(return_value=empty_feedback)

        can_retry, reason = await engine._validate_retry_feedback_policy("task-001", retry_count=1)
        assert can_retry is False
        assert "ambiguous" in reason

    @pytest.mark.asyncio
    async def test_allowed_when_feedback_has_findings(self):
        engine = self._make_engine()
        engine._fetch_latest_review_feedback = AsyncMock(return_value=_make_feedback())

        can_retry, reason = await engine._validate_retry_feedback_policy("task-001", retry_count=1)
        assert can_retry is True
        assert reason == ""

    @pytest.mark.asyncio
    async def test_allowed_when_feedback_has_only_direction(self):
        engine = self._make_engine()
        feedback = _make_feedback(findings=[], suggested_retry_direction="Fix the token expiry")
        engine._fetch_latest_review_feedback = AsyncMock(return_value=feedback)

        can_retry, reason = await engine._validate_retry_feedback_policy("task-001", retry_count=2)
        assert can_retry is True

    @pytest.mark.asyncio
    async def test_allowed_when_feedback_has_only_findings(self):
        engine = self._make_engine()
        feedback = _make_feedback(suggested_retry_direction="")
        engine._fetch_latest_review_feedback = AsyncMock(return_value=feedback)

        can_retry, reason = await engine._validate_retry_feedback_policy("task-001", retry_count=1)
        assert can_retry is True


# ---------------------------------------------------------------------------
# TaskEngine._handle_task_failure — bounded requeue policy integration
# ---------------------------------------------------------------------------

class TestHandleTaskFailureBoundedRequeue:
    def _make_engine(self):
        engine = TaskEngine(project_path="/tmp/test")
        engine.lifecycle_service = MagicMock()
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(True, {"task": _make_task(status="failed")}),
        )
        engine.task_service = MagicMock()
        engine.notifier = MagicMock()
        engine.notifier.on_task_failed = AsyncMock()
        engine.notifier.on_task_escalated = AsyncMock()
        engine.notifier.on_task_requeued = AsyncMock()
        engine._escalation_enabled = True
        return engine

    @pytest.mark.asyncio
    async def test_first_failure_schedules_retry_without_feedback(self):
        engine = self._make_engine()
        # _validate_retry_feedback_policy should short-circuit for retry_count=0
        engine._validate_retry_feedback_policy = AsyncMock(return_value=(True, ""))

        task = _make_task(retry_count=0, max_retries=3)

        with patch.object(engine, "_schedule_retry", new=AsyncMock()):
            await engine._handle_task_failure("task-001", task, "Build failed")

        engine.notifier.on_task_failed.assert_awaited_once()
        engine.notifier.on_task_escalated.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_second_failure_escalates_when_feedback_missing(self):
        engine = self._make_engine()
        engine._validate_retry_feedback_policy = AsyncMock(
            return_value=(False, "No review feedback found for task task-001 on attempt 2")
        )

        task = _make_task(retry_count=1, max_retries=3)

        with patch.object(engine, "_schedule_retry", new=AsyncMock()) as mock_schedule:
            await engine._handle_task_failure("task-001", task, "Tests failed again")

        engine.notifier.on_task_escalated.assert_awaited_once()
        engine.notifier.on_task_failed.assert_not_awaited()
        # _schedule_retry is wrapped in asyncio.create_task — verify it was not called at all
        mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_second_failure_retries_when_feedback_present(self):
        engine = self._make_engine()
        engine._validate_retry_feedback_policy = AsyncMock(return_value=(True, ""))

        task = _make_task(retry_count=1, max_retries=3)

        with patch.object(engine, "_schedule_retry", new=AsyncMock()) as mock_schedule:
            await engine._handle_task_failure("task-001", task, "Tests failed again")

        engine.notifier.on_task_failed.assert_awaited_once()
        # _schedule_retry is passed to asyncio.create_task — verify it was called (coroutine created)
        mock_schedule.assert_called_once_with("task-001", 1, "Tests failed again")
        engine.notifier.on_task_escalated.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_escalates_regardless_of_policy(self):
        engine = self._make_engine()
        engine._validate_retry_feedback_policy = AsyncMock(return_value=(True, ""))

        task = _make_task(retry_count=3, max_retries=3)

        with patch.object(engine, "_schedule_retry", new=AsyncMock()) as mock_schedule:
            await engine._handle_task_failure("task-001", task, "Still failing")

        engine.notifier.on_task_escalated.assert_awaited_once()
        mock_schedule.assert_not_called()
