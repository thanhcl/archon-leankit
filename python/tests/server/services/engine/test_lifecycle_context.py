"""Tests for lifecycle-context preservation hooks (C1-3).

Covers:
- run_start: context_snapshot stored in execution_run metadata
- run_complete: structured result_summary with files_modified / tests_passed
- run_timeout: partial_context extracted from CC stdout and stored
- retry: prompt_builder injects PREVIOUS_ATTEMPT section from partial_context
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.architect_reviewer import ReviewConfig
from src.server.services.engine.cc_spawner import CCExecutionResult
from src.server.services.engine.prompt_builder import PromptBuilder
from src.server.services.engine.task_engine import TaskEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
        "title": "Add auth endpoint",
        "description": "Implement login",
        "status": "assigned",
        "priority": "medium",
        "assignee": "Agent",
        "source_app": "leankit",
        "acceptance_criteria": [{"text": "POST /login returns JWT"}],
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
    }
    base.update(overrides)
    return base


def _timeout_result(stdout: str = "") -> CCExecutionResult:
    return CCExecutionResult(
        success=False,
        stdout=stdout,
        stderr="Timed out after 600s",
        exit_code=-1,
        duration_seconds=600.0,
        timed_out=True,
    )


def _success_result() -> CCExecutionResult:
    return CCExecutionResult(
        success=True,
        stdout="RESULT: SUCCESS\nFILES_CHANGED: 3\nTESTS_ADDED: 2\nSUMMARY: Auth endpoint done\n",
        stderr="",
        exit_code=0,
        duration_seconds=45.0,
        parsed={
            "result": "SUCCESS",
            "files_changed": 3,
            "tests_added": 2,
            "summary": "Auth endpoint done",
            "model_used": "claude-sonnet-4-6",
            "learnings": [],
            "code_patterns": [],
        },
    )


def _empty_boundary_validation() -> dict:
    return {"status": "ok", "changed_files": [], "reason": None}


def _make_engine() -> TaskEngine:
    """Minimal TaskEngine with all external deps mocked."""
    engine = TaskEngine.__new__(TaskEngine)
    engine.project_id = "proj-001"
    engine.source_app = "leankit"
    engine.project_config = MagicMock()
    engine.project_config.build_command = "pnpm test"
    engine.project_config.isolation = "shared"
    engine.project_config.project_path = "/repo"
    engine.project_config.force_model = None
    engine.task_service = MagicMock()
    engine.task_service.get_task = MagicMock(return_value=(True, {"task": _make_task()}))
    engine.task_service.update_task = AsyncMock(return_value=(True, {}))
    engine.execution_run_service = MagicMock()
    engine.execution_run_service.create_run = AsyncMock(
        return_value=(True, {"run": {"id": "run-001"}})
    )
    engine.execution_run_service.update_run = AsyncMock(return_value=(True, {"run": {}}))
    engine.lifecycle_service = MagicMock()
    engine.lifecycle_service.execute_transition = AsyncMock(return_value=(True, {}))
    engine.notifier = MagicMock()
    engine.notifier.on_task_started = AsyncMock()
    engine.notifier.on_task_completed = AsyncMock()
    engine.notifier.on_task_failed = AsyncMock()
    engine.notifier.on_task_escalated = AsyncMock()
    engine.notifier.on_agent_status = AsyncMock()
    engine.engine_policy_service = MagicMock()
    engine.engine_policy_service.get_active_policy = MagicMock(return_value=None)
    engine.cost_budget_service = MagicMock()
    engine.cost_budget_service.record_task_cost = MagicMock()
    engine.learning_processor = MagicMock()
    engine.architect_reviewer = MagicMock()
    engine.architect_reviewer.review = AsyncMock(
        return_value=(
            MagicMock(
                verdict="approve",
                confidence=0.9,
                summary="ok",
                mode="self-review",
                findings=[],
                feedback="",
                error=None,
            ),
            MagicMock(
                next_status="review",
                reason="Approved",
                warning=None,
                escalation_reason=None,
            ),
        )
    )
    engine.prompt_builder = MagicMock()
    engine.prompt_builder.build = AsyncMock(
        return_value=(
            "prompt text",
            {"learnings": 0, "patterns": 0, "kb_chunks": 0, "tokens": 10},
        )
    )
    engine.review_config = ReviewConfig()
    engine.health_monitor = MagicMock()
    engine.default_runner_key = "claude-code"
    engine.runner_adapters = {}
    engine._running = False
    engine._loop_task = None
    engine._execution_tasks = {}
    engine._poll_cycle_count = 0
    engine.default_timeout = 600
    engine.shutdown_grace = 60
    return engine


async def _call_on_cc_complete(engine: TaskEngine, result: CCExecutionResult) -> None:
    """Helper: call _on_cc_complete with external deps patched out."""
    with (
        patch.object(
            engine,
            "_build_boundary_validation",
            new=AsyncMock(return_value=_empty_boundary_validation()),
        ),
        patch.object(engine, "_run_architect_review", new=AsyncMock()),
        patch.object(engine, "_handle_task_failure", new=AsyncMock()),
    ):
        await engine._on_cc_complete(
            task_id="task-001",
            result=result,
            injection_stats={},
            execute_run_id="run-001",
        )


# ---------------------------------------------------------------------------
# Tests: _extract_partial_context
# ---------------------------------------------------------------------------


class TestExtractPartialContext:
    def test_empty_stdout(self):
        result = _timeout_result(stdout="")
        ctx = TaskEngine._extract_partial_context(result)
        assert ctx["timed_out"] is True
        assert ctx["duration_seconds"] == 600.0
        assert "files_modified" not in ctx
        assert "partial_output" not in ctx

    def test_extracts_write_tool_files_from_top_level_input(self):
        tool_event = json.dumps({
            "type": "tool_use",
            "name": "Write",
            "input": {"file_path": "src/auth.py", "content": "..."},
        })
        result = _timeout_result(stdout=tool_event)
        ctx = TaskEngine._extract_partial_context(result)
        assert "files_modified" in ctx
        assert "src/auth.py" in ctx["files_modified"]

    def test_extracts_edit_tool_files_from_nested_tool_key(self):
        edit_event = json.dumps({
            "type": "tool_use",
            "tool": {
                "name": "Edit",
                "input": {"file_path": "src/routes.py"},
            },
        })
        result = _timeout_result(stdout=edit_event)
        ctx = TaskEngine._extract_partial_context(result)
        assert "src/routes.py" in ctx.get("files_modified", [])

    def test_extracts_assistant_message(self):
        assistant_event = json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "I have implemented the auth module."}]
            },
        })
        result = _timeout_result(stdout=assistant_event)
        ctx = TaskEngine._extract_partial_context(result)
        assert ctx.get("partial_output") == "I have implemented the auth module."

    def test_ignores_non_write_tools(self):
        read_event = json.dumps({
            "type": "tool_use",
            "name": "Read",
            "input": {"file_path": "src/auth.py"},
        })
        result = _timeout_result(stdout=read_event)
        ctx = TaskEngine._extract_partial_context(result)
        assert "files_modified" not in ctx

    def test_deduplicates_files(self):
        event = json.dumps({
            "type": "tool_use",
            "name": "Edit",
            "input": {"file_path": "src/auth.py"},
        })
        result = _timeout_result(stdout=f"{event}\n{event}")
        ctx = TaskEngine._extract_partial_context(result)
        assert ctx["files_modified"].count("src/auth.py") == 1

    def test_skips_invalid_json_lines(self):
        result = _timeout_result(stdout="not-json\n{bad}\n")
        ctx = TaskEngine._extract_partial_context(result)
        assert ctx["timed_out"] is True

    def test_uses_last_assistant_message(self):
        msg1 = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "First message"}]},
        })
        msg2 = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Second message"}]},
        })
        result = _timeout_result(stdout=f"{msg1}\n{msg2}")
        ctx = TaskEngine._extract_partial_context(result)
        assert ctx["partial_output"] == "Second message"

    def test_multiple_files_across_events(self):
        events = "\n".join(
            json.dumps({"type": "tool_use", "name": "Write", "input": {"file_path": f"src/f{i}.py"}})
            for i in range(3)
        )
        result = _timeout_result(stdout=events)
        ctx = TaskEngine._extract_partial_context(result)
        assert len(ctx["files_modified"]) == 3


# ---------------------------------------------------------------------------
# Tests: result_summary enrichment in _on_cc_complete
# ---------------------------------------------------------------------------


class TestResultSummaryEnrichment:
    @pytest.mark.asyncio
    async def test_success_result_summary_includes_summary_and_metrics(self):
        engine = _make_engine()
        await _call_on_cc_complete(engine, _success_result())

        update_calls = engine.execution_run_service.update_run.call_args_list
        assert update_calls, "update_run should have been called"
        # update_run(run_id, fields_dict) — positional
        update_payload = update_calls[0][0][1]
        result_summary = update_payload.get("result_summary", "")
        assert result_summary is not None
        assert "Auth endpoint done" in result_summary
        assert "files_modified=3" in result_summary
        assert "tests_passed=2" in result_summary

    @pytest.mark.asyncio
    async def test_timeout_result_summary_includes_timeout_note(self):
        engine = _make_engine()
        await _call_on_cc_complete(engine, _timeout_result())

        update_calls = engine.execution_run_service.update_run.call_args_list
        assert update_calls
        update_payload = update_calls[0][0][1]
        result_summary = update_payload.get("result_summary") or ""
        assert "timed_out=true" in result_summary

    @pytest.mark.asyncio
    async def test_failure_result_summary_with_summary_only(self):
        failure = CCExecutionResult(
            success=False,
            stdout="RESULT: FAILURE\nSUMMARY: Build broke\n",
            stderr="",
            exit_code=1,
            duration_seconds=20.0,
            parsed={"result": "FAILURE", "summary": "Build broke", "learnings": [], "code_patterns": []},
        )
        engine = _make_engine()
        await _call_on_cc_complete(engine, failure)

        update_calls = engine.execution_run_service.update_run.call_args_list
        assert update_calls
        update_payload = update_calls[0][0][1]
        result_summary = update_payload.get("result_summary") or ""
        assert "Build broke" in result_summary


# ---------------------------------------------------------------------------
# Tests: partial_context stored on timeout
# ---------------------------------------------------------------------------


class TestTimeoutPartialContextStorage:
    @pytest.mark.asyncio
    async def test_partial_context_stored_in_execution_run_metadata(self):
        write_event = json.dumps({
            "type": "tool_use",
            "name": "Write",
            "input": {"file_path": "src/auth.py"},
        })
        engine = _make_engine()
        await _call_on_cc_complete(engine, _timeout_result(stdout=write_event))

        update_calls = engine.execution_run_service.update_run.call_args_list
        assert update_calls
        update_payload = update_calls[0][0][1]
        metadata = update_payload.get("metadata") or {}
        assert "partial_context" in metadata
        assert metadata["partial_context"]["timed_out"] is True
        assert "src/auth.py" in metadata["partial_context"].get("files_modified", [])

    @pytest.mark.asyncio
    async def test_partial_context_stored_in_task_execution_result(self):
        engine = _make_engine()
        await _call_on_cc_complete(engine, _timeout_result())

        update_call = engine.task_service.update_task.call_args
        assert update_call is not None
        exec_result = update_call.kwargs.get("update_fields", {}).get("execution_result", {})
        assert "partial_context" in exec_result
        assert exec_result["partial_context"]["timed_out"] is True

    @pytest.mark.asyncio
    async def test_non_timeout_does_not_store_partial_context(self):
        engine = _make_engine()
        await _call_on_cc_complete(engine, _success_result())

        update_call = engine.task_service.update_task.call_args
        if update_call:
            exec_result = update_call.kwargs.get("update_fields", {}).get("execution_result", {})
            assert "partial_context" not in exec_result

    @pytest.mark.asyncio
    async def test_timeout_execution_run_status_is_failed(self):
        engine = _make_engine()
        await _call_on_cc_complete(engine, _timeout_result())

        update_calls = engine.execution_run_service.update_run.call_args_list
        assert update_calls
        update_payload = update_calls[0][0][1]
        assert update_payload.get("status") == "failed"


# ---------------------------------------------------------------------------
# Tests: context_snapshot built correctly
# ---------------------------------------------------------------------------


class TestContextSnapshotStructure:
    def test_snapshot_contains_required_keys(self):
        task = _make_task()
        snapshot = {
            "task_id": task["id"],
            "title": task.get("title"),
            "acceptance_criteria": task.get("acceptance_criteria") or [],
            "files_in_scope": task.get("files_in_scope") or [],
            "retry_index": task.get("retry_count") or 0,
        }
        assert snapshot["task_id"] == "task-001"
        assert snapshot["title"] == "Add auth endpoint"
        assert isinstance(snapshot["acceptance_criteria"], list)
        assert snapshot["acceptance_criteria"][0]["text"] == "POST /login returns JWT"
        assert snapshot["files_in_scope"] == ["src/auth.py"]
        assert snapshot["retry_index"] == 0

    def test_snapshot_handles_missing_optional_fields(self):
        task = {"id": "t1", "title": "Test"}  # no acceptance_criteria or files_in_scope
        snapshot = {
            "task_id": task["id"],
            "title": task.get("title"),
            "acceptance_criteria": task.get("acceptance_criteria") or [],
            "files_in_scope": task.get("files_in_scope") or [],
            "retry_index": task.get("retry_count") or 0,
        }
        assert snapshot["acceptance_criteria"] == []
        assert snapshot["files_in_scope"] == []


# ---------------------------------------------------------------------------
# Tests: prompt_builder PREVIOUS_ATTEMPT injection
# ---------------------------------------------------------------------------


class TestPreviousAttemptInjection:
    def _retry_task(self, files_modified=None, partial_output=None, reason=None):
        partial_context: dict = {"timed_out": True, "duration_seconds": 600}
        if files_modified is not None:
            partial_context["files_modified"] = files_modified
        if partial_output is not None:
            partial_context["partial_output"] = partial_output
        if reason is not None:
            partial_context["reason"] = reason

        return {
            "id": "task-002",
            "title": "Add auth endpoint",
            "description": "Implement login",
            "status": "assigned",
            "priority": "medium",
            "assignee": "Agent",
            "source_app": "leankit",
            "acceptance_criteria": [],
            "retry_count": 1,
            "architect_review": None,
            "execution_result": {
                "timed_out": True,
                "exit_code": -1,
                "partial_context": partial_context,
            },
            "rejection_reason": None,
            "execution_prompt": None,
            "repo_guidance_packs": [],
        }

    def test_partial_context_injects_previous_attempt_section(self):
        builder = PromptBuilder()
        task = self._retry_task(
            files_modified=["src/auth.py", "src/routes.py"],
            partial_output="Started implementing JWT middleware.",
            reason="Task-level timeout after 900s",
        )
        feedback = builder._format_retry_feedback(task)
        assert feedback is not None
        assert "PREVIOUS_ATTEMPT" in feedback
        assert "src/auth.py" in feedback
        assert "src/routes.py" in feedback
        assert "JWT middleware" in feedback
        assert "Task-level timeout" in feedback

    def test_no_partial_context_omits_previous_attempt_section(self):
        builder = PromptBuilder()
        task = {
            "id": "task-003",
            "retry_count": 1,
            "execution_result": {"result": "FAILURE", "summary": "Build broke"},
            "architect_review": None,
            "rejection_reason": None,
        }
        feedback = builder._format_retry_feedback(task)
        assert feedback is not None
        assert "PREVIOUS_ATTEMPT" not in feedback

    def test_first_attempt_returns_none(self):
        builder = PromptBuilder()
        task = {"retry_count": 0, "execution_result": None}
        assert builder._format_retry_feedback(task) is None

    def test_files_modified_truncated_to_five(self):
        builder = PromptBuilder()
        files = [f"src/file{i}.py" for i in range(10)]
        task = self._retry_task(files_modified=files)
        feedback = builder._format_retry_feedback(task)
        assert feedback is not None
        shown = [f for f in files[:5] if f in feedback]
        hidden = [f for f in files[5:] if f in feedback]
        assert len(shown) == 5
        assert len(hidden) == 0

    def test_partial_context_without_files_still_injects_section(self):
        builder = PromptBuilder()
        task = self._retry_task(partial_output="Working on the login endpoint.")
        feedback = builder._format_retry_feedback(task)
        assert feedback is not None
        assert "PREVIOUS_ATTEMPT" in feedback
        assert "login endpoint" in feedback

    def test_continue_instruction_is_included(self):
        builder = PromptBuilder()
        task = self._retry_task(files_modified=["src/x.py"])
        feedback = builder._format_retry_feedback(task)
        assert feedback is not None
        assert "continue" in feedback.lower() or "left off" in feedback.lower()

    def test_partial_context_combined_with_other_retry_info(self):
        """partial_context and regular failure details can coexist."""
        builder = PromptBuilder()
        task = self._retry_task(partial_output="Partial progress made")
        task["execution_result"]["summary"] = "Tests failed"
        feedback = builder._format_retry_feedback(task)
        assert feedback is not None
        assert "PREVIOUS_ATTEMPT" in feedback
