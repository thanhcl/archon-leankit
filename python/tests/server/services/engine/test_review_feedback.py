"""Tests for review feedback artifact schema and event emission.

Verifies that:
- _map_findings_to_feedback converts raw findings to structured records
- _write_review_feedback persists to DB and emits observability event
- architect-review rejection triggers feedback write
- code-review REQUEST_CHANGES triggers feedback write
- DB errors are non-fatal (feedback write never blocks the review pipeline)
"""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.server.models.api_contracts import (
    ReviewFeedbackFinding,
    ReviewFeedbackListResponse,
    ReviewFeedbackResponse,
)
from src.server.services.engine.architect_reviewer import (
    ArchitectReviewResult,
    ReviewAction,
)
from src.server.services.engine.cc_spawner import CCExecutionResult
from src.server.services.engine.notifier import EVENT_REVIEW_FEEDBACK_WRITTEN
from src.server.services.engine.task_engine import TaskEngine


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
        "title": "Add auth endpoint",
        "description": "Implement login",
        "status": "assigned",
        "priority": "high",
        "assignee": "Agent",
        "source_app": "leankit",
        "acceptance_criteria": [],
        "retry_count": 0,
        "review_cycle": 0,
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
        "current_contract": None,
    }
    base.update(overrides)
    return base


def _setup_engine() -> TaskEngine:
    """Create a TaskEngine with all services mocked."""
    engine = TaskEngine(project_path="/tmp/test")
    engine.engine_policy_service = MagicMock()
    engine.engine_policy_service.get_active_policy.return_value = None
    engine.lifecycle_service = MagicMock()
    engine.lifecycle_service.execute_transition = AsyncMock(
        return_value=(True, {"task": _make_task(status="assigned")}),
    )
    engine.task_service = MagicMock()
    engine.task_service.get_task.return_value = (True, {"task": _make_task()})
    engine.task_service.update_task = AsyncMock(return_value=(True, {}))
    engine.task_service.supabase_client = MagicMock()
    engine.task_service.supabase_client.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": "fb-001"}])
    engine.task_service.supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(data=[])
    engine.execution_run_service = MagicMock()
    engine.execution_run_service.create_run = AsyncMock(
        side_effect=[
            (True, {"run": {"id": "run-execute-001"}}),
            (True, {"run": {"id": "run-architect-001"}}),
            (True, {"run": {"id": "run-code-review-001"}}),
        ]
    )
    engine.execution_run_service.update_run = AsyncMock(return_value=(True, {}))
    engine.prompt_builder = MagicMock()
    engine.prompt_builder.build = AsyncMock(
        return_value=("test prompt", {"learnings": 0, "patterns": 0, "kb_chunks": 0, "tokens": 100}),
    )
    engine.spawner = MagicMock()
    engine.spawner.has_capacity = True
    engine.spawner.source_app = "claude-code-cli"
    engine.spawner.review_model = "claude-sonnet-4-6"
    engine.spawner.select_model.return_value = "claude-sonnet-4-6"
    engine.notifier = MagicMock()
    engine.notifier.emit = AsyncMock()
    engine.notifier.on_task_review_ready = AsyncMock()
    engine.notifier.on_task_requeued = AsyncMock()
    engine.notifier.on_task_escalated = AsyncMock()
    engine.notifier.on_task_done = AsyncMock()
    engine.notifier.on_task_failed = AsyncMock()
    engine.notifier.on_task_started = AsyncMock()
    engine.notifier.on_task_completed = AsyncMock()
    engine.notifier.on_code_review_changes_requested = AsyncMock()
    engine.notifier.on_review_feedback_written = AsyncMock()
    engine.architect_reviewer = MagicMock()
    engine.cost_budget_service = MagicMock()
    engine.cost_budget_service.check_budget = MagicMock(return_value=(True, ""))
    engine.bug_task_creator = MagicMock()
    engine.bug_task_creator.create_bug_tasks_from_findings = AsyncMock(return_value=[])
    engine.learning_processor = MagicMock()
    engine.learning_processor.process = AsyncMock()
    return engine


def _rejection_review():
    """Architect review that rejects (changes-requested → assigned)."""
    return (
        ArchitectReviewResult(
            verdict="changes-requested",
            confidence=0.4,
            summary="Needs rework",
            mode="self-review",
            findings=[
                {"type": "correctness", "severity": "high", "description": "Missing validation"},
                {"type": "security", "severity": "critical", "description": "SQL injection risk"},
            ],
        ),
        ReviewAction(next_status="assigned", reason="Fix validation and SQL injection", changed_by="architect-reviewer"),
    )


def _escalate_review():
    """Architect review that escalates."""
    return (
        ArchitectReviewResult(
            verdict="escalate",
            confidence=0.1,
            summary="Cannot proceed",
            mode="self-review",
            findings=[
                {"type": "critical", "severity": "critical", "description": "Data corruption risk"},
            ],
        ),
        ReviewAction(next_status="escalated", reason="Data corruption risk", changed_by="architect-reviewer"),
    )


# ── Unit tests: _map_findings_to_feedback ───────────────────────────────


class TestMapFindingsToFeedback:
    def test_empty_findings(self):
        result = TaskEngine._map_findings_to_feedback([])
        assert result == []

    def test_basic_finding(self):
        findings = [{"type": "correctness", "severity": "high", "description": "Missing check"}]
        result = TaskEngine._map_findings_to_feedback(findings)
        assert len(result) == 1
        assert result[0]["criterion"] == "correctness"
        assert result[0]["details"] == "Missing check"
        assert result[0]["passed"] is False
        assert 0.0 <= result[0]["score"] <= 10.0

    def test_severity_score_mapping(self):
        findings = [
            {"severity": "critical", "description": "critical issue"},
            {"severity": "high", "description": "high issue"},
            {"severity": "medium", "description": "medium issue"},
            {"severity": "low", "description": "low issue"},
        ]
        result = TaskEngine._map_findings_to_feedback(findings)
        scores = [r["score"] for r in result]
        # Critical should have lowest score (worst), low should be higher
        assert scores[0] < scores[1] < scores[2] < scores[3]

    def test_explicit_score_preserved(self):
        findings = [{"type": "x", "score": 7.5, "description": "ok"}]
        result = TaskEngine._map_findings_to_feedback(findings)
        assert result[0]["score"] == 7.5

    def test_score_clamped_to_range(self):
        findings = [
            {"type": "x", "score": -5.0, "description": "negative"},
            {"type": "y", "score": 15.0, "description": "too high"},
        ]
        result = TaskEngine._map_findings_to_feedback(findings)
        assert result[0]["score"] == 0.0
        assert result[1]["score"] == 10.0

    def test_non_dict_findings_skipped(self):
        findings = ["string finding", None, 42, {"type": "valid", "description": "ok"}]
        result = TaskEngine._map_findings_to_feedback(findings)
        assert len(result) == 1
        assert result[0]["criterion"] == "valid"

    def test_evidence_field_included(self):
        findings = [{"type": "x", "description": "d", "evidence": "test output line 42"}]
        result = TaskEngine._map_findings_to_feedback(findings)
        assert result[0]["evidence"] == "test output line 42"

    def test_criterion_fallback_chain(self):
        # Uses category when type is absent
        findings = [{"category": "security", "description": "XSS"}]
        result = TaskEngine._map_findings_to_feedback(findings)
        assert result[0]["criterion"] == "security"

        # Falls back to "general" when neither type nor category present
        findings2 = [{"description": "Unknown issue"}]
        result2 = TaskEngine._map_findings_to_feedback(findings2)
        assert result2[0]["criterion"] == "general"


# ── Unit tests: _write_review_feedback ──────────────────────────────────


class TestWriteReviewFeedback:
    @pytest.mark.asyncio
    async def test_inserts_row_to_db(self):
        engine = _setup_engine()
        task = _make_task()
        findings = [{"type": "correctness", "severity": "high", "description": "Fix this"}]

        await engine._write_review_feedback(
            task_id="task-001",
            run_id="run-001",
            task=task,
            findings=findings,
            overall_score=0.4,
            verdict="changes-requested",
            suggested_retry_direction="Focus on validation",
            reviewer_identity="architect-reviewer",
            stage="architect-review",
        )

        engine.task_service.supabase_client.table.assert_any_call("archon_review_feedback")
        insert_call = engine.task_service.supabase_client.table.return_value.insert
        insert_call.assert_called_once()
        row = insert_call.call_args[0][0]
        assert row["task_id"] == "task-001"
        assert row["run_id"] == "run-001"
        assert row["verdict"] == "changes-requested"
        assert row["reviewer_identity"] == "architect-reviewer"
        assert row["overall_score"] == 0.4
        assert row["suggested_retry_direction"] == "Focus on validation"
        assert len(row["findings"]) == 1

    @pytest.mark.asyncio
    async def test_emits_observability_event(self):
        engine = _setup_engine()
        task = _make_task()

        await engine._write_review_feedback(
            task_id="task-001",
            run_id="run-001",
            task=task,
            findings=[],
            overall_score=0.6,
            verdict="escalate",
            suggested_retry_direction="Cannot retry",
            reviewer_identity="code-reviewer",
            stage="code-review",
        )

        engine.notifier.on_review_feedback_written.assert_awaited_once()
        call_kwargs = engine.notifier.on_review_feedback_written.call_args
        assert call_kwargs[0][0] == "task-001"
        payload = call_kwargs[0][1]
        assert payload["verdict"] == "escalate"
        assert payload["reviewer_identity"] == "code-reviewer"

    @pytest.mark.asyncio
    async def test_contract_revision_extracted_from_task(self):
        engine = _setup_engine()
        task = _make_task(current_contract={"version": 3, "id": "contract-001"})

        await engine._write_review_feedback(
            task_id="task-001",
            run_id="run-001",
            task=task,
            findings=[],
            overall_score=0.5,
            verdict="changes-requested",
            suggested_retry_direction=None,
            reviewer_identity="architect-reviewer",
            stage="architect-review",
        )

        row = engine.task_service.supabase_client.table.return_value.insert.call_args[0][0]
        assert row["contract_revision"] == 3

    @pytest.mark.asyncio
    async def test_db_error_is_non_fatal(self):
        """A DB failure must not raise — review pipeline must continue."""
        engine = _setup_engine()
        engine.task_service.supabase_client.table.return_value.insert.return_value.execute.side_effect = Exception(
            "Connection refused"
        )
        task = _make_task()

        # Should not raise
        await engine._write_review_feedback(
            task_id="task-001",
            run_id="run-001",
            task=task,
            findings=[],
            overall_score=0.3,
            verdict="changes-requested",
            suggested_retry_direction="Fix it",
            reviewer_identity="architect-reviewer",
            stage="architect-review",
        )

    @pytest.mark.asyncio
    async def test_null_run_id_accepted(self):
        engine = _setup_engine()
        task = _make_task()

        await engine._write_review_feedback(
            task_id="task-001",
            run_id=None,
            task=task,
            findings=[],
            overall_score=None,
            verdict="changes-requested",
            suggested_retry_direction=None,
            reviewer_identity="architect-reviewer",
            stage="architect-review",
        )

        row = engine.task_service.supabase_client.table.return_value.insert.call_args[0][0]
        assert row["run_id"] is None
        assert row["overall_score"] is None


# ── Integration: architect-review rejection triggers feedback ────────────


class TestArchitectReviewFeedbackIntegration:
    @pytest.mark.asyncio
    async def test_rejection_writes_feedback(self):
        engine = _setup_engine()
        engine.architect_reviewer.review = AsyncMock(return_value=_rejection_review())
        engine.task_service.get_task.return_value = (True, {"task": _make_task()})

        with patch.object(engine, "_create_execution_run", AsyncMock(return_value="run-arch-001")):
            with patch.object(engine, "_update_execution_run", AsyncMock()):
                with patch.object(engine, "_write_review_feedback", AsyncMock()) as mock_write:
                    await engine._run_architect_review("task-001", {})

        mock_write.assert_awaited_once()
        call_kw = mock_write.call_args[1]
        assert call_kw["verdict"] == "changes-requested"
        assert call_kw["reviewer_identity"] in {"architect-reviewer", "self-review", None, ""}
        assert call_kw["stage"] == "architect-review"
        assert call_kw["task_id"] == "task-001"

    @pytest.mark.asyncio
    async def test_escalate_writes_feedback(self):
        engine = _setup_engine()
        engine.architect_reviewer.review = AsyncMock(return_value=_escalate_review())
        engine.task_service.get_task.return_value = (True, {"task": _make_task()})

        with patch.object(engine, "_create_execution_run", AsyncMock(return_value="run-arch-001")):
            with patch.object(engine, "_update_execution_run", AsyncMock()):
                with patch.object(engine, "_write_review_feedback", AsyncMock()) as mock_write:
                    await engine._run_architect_review("task-001", {})

        mock_write.assert_awaited_once()
        call_kw = mock_write.call_args[1]
        assert call_kw["verdict"] == "escalate"

    @pytest.mark.asyncio
    async def test_approval_does_not_write_feedback(self):
        """Approved reviews should NOT write feedback artifacts."""
        engine = _setup_engine()
        engine.architect_reviewer.review = AsyncMock(return_value=(
            ArchitectReviewResult(verdict="approve", confidence=0.95, summary="Good", mode="self-review"),
            ReviewAction(next_status="review", reason="Approved", changed_by="architect-reviewer"),
        ))
        engine.review_config = MagicMock()
        engine.review_config.review_mode = "self-review"
        engine.review_config.independent_review_enabled = False
        engine.task_service.get_task.return_value = (True, {"task": _make_task()})

        with patch.object(engine, "_create_execution_run", AsyncMock(return_value="run-arch-001")):
            with patch.object(engine, "_update_execution_run", AsyncMock()):
                with patch.object(engine, "_write_review_feedback", AsyncMock()) as mock_write:
                    with patch.object(engine, "_run_code_review", AsyncMock()):
                        await engine._run_architect_review("task-001", {})

        mock_write.assert_not_awaited()


# ── Integration: code-review rejection triggers feedback ────────────────


class TestCodeReviewFeedbackIntegration:
    def _code_review_engine(self):
        engine = _setup_engine()
        engine.review_config = MagicMock()
        engine.review_config.review_mode = "self-review"
        engine.review_config.independent_review_enabled = True
        return engine

    def _request_changes_result(self, findings=None):
        return CCExecutionResult(
            success=True,
            stdout="CODE_REVIEW_VERDICT: REQUEST_CHANGES\n",
            stderr="",
            exit_code=0,
            duration_seconds=10.0,
            parsed={
                "code_review_verdict": "REQUEST_CHANGES",
                "code_review_findings": findings or [
                    {"type": "style", "severity": "medium", "description": "Missing docstring"}
                ],
                "model_used": "claude-sonnet-4-6",
            },
        )

    @pytest.mark.asyncio
    async def test_request_changes_writes_feedback(self):
        engine = self._code_review_engine()
        engine.task_service.get_task.return_value = (True, {"task": _make_task(review_cycle=0)})
        engine.spawner.spawn = AsyncMock(return_value=self._request_changes_result())

        with patch.object(engine, "_create_execution_run", AsyncMock(return_value="run-cr-001")):
            with patch.object(engine, "_update_execution_run", AsyncMock()):
                with patch.object(engine, "_get_git_diff", AsyncMock(return_value="")):
                    with patch.object(engine, "_write_review_feedback", AsyncMock()) as mock_write:
                        await engine._run_code_review("task-001", {})

        mock_write.assert_awaited_once()
        call_kw = mock_write.call_args[1]
        assert call_kw["verdict"] == "changes-requested"
        assert call_kw["reviewer_identity"] == "code-reviewer"
        assert call_kw["stage"] == "code-review"

    @pytest.mark.asyncio
    async def test_approve_does_not_write_feedback(self):
        engine = self._code_review_engine()
        engine.task_service.get_task.return_value = (True, {"task": _make_task(review_cycle=0)})
        approve_result = CCExecutionResult(
            success=True,
            stdout="CODE_REVIEW_VERDICT: APPROVE\n",
            stderr="",
            exit_code=0,
            duration_seconds=10.0,
            parsed={"code_review_verdict": "APPROVE", "code_review_findings": [], "model_used": "claude-sonnet-4-6"},
        )
        engine.spawner.spawn = AsyncMock(return_value=approve_result)

        with patch.object(engine, "_create_execution_run", AsyncMock(return_value="run-cr-001")):
            with patch.object(engine, "_update_execution_run", AsyncMock()):
                with patch.object(engine, "_get_git_diff", AsyncMock(return_value="")):
                    with patch.object(engine, "_write_review_feedback", AsyncMock()) as mock_write:
                        await engine._run_code_review("task-001", {})

        mock_write.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_critical_findings_escalation_writes_feedback(self):
        engine = self._code_review_engine()
        critical_findings = [{"type": "security", "severity": "critical", "description": "SQL injection"}]
        # review_cycle=1 so the CC session runs; new_cycle becomes 2 == MAX_CODE_REVIEW_CYCLES,
        # triggering the critical+max escalation branch
        engine.task_service.get_task.return_value = (
            True,
            {"task": _make_task(review_cycle=TaskEngine.MAX_CODE_REVIEW_CYCLES - 1)},
        )
        engine.spawner.spawn = AsyncMock(return_value=self._request_changes_result(critical_findings))

        with patch.object(engine, "_create_execution_run", AsyncMock(return_value="run-cr-001")):
            with patch.object(engine, "_update_execution_run", AsyncMock()):
                with patch.object(engine, "_get_git_diff", AsyncMock(return_value="")):
                    with patch.object(engine, "_write_review_feedback", AsyncMock()) as mock_write:
                        await engine._run_code_review("task-001", {})

        mock_write.assert_awaited_once()
        call_kw = mock_write.call_args[1]
        assert call_kw["verdict"] == "escalate"


# ── Pydantic model validation ────────────────────────────────────────────


class TestReviewFeedbackModels:
    def test_finding_score_bounds(self):
        f = ReviewFeedbackFinding(criterion="x", score=5.0, passed=False)
        assert f.score == 5.0

    def test_finding_score_rejects_out_of_range(self):
        with pytest.raises(Exception):
            ReviewFeedbackFinding(criterion="x", score=11.0, passed=False)

    def test_list_response_shape(self):
        resp = ReviewFeedbackListResponse(
            feedback=[
                ReviewFeedbackResponse(
                    id="fb-001",
                    task_id="task-001",
                    verdict="changes-requested",
                    reviewer_identity="code-reviewer",
                    created_at="2026-01-01T00:00:00",
                )
            ],
            total_count=1,
        )
        assert resp.total_count == 1
        assert resp.feedback[0].verdict == "changes-requested"

    def test_event_constant_defined(self):
        assert EVENT_REVIEW_FEEDBACK_WRITTEN == "review_feedback_written"
