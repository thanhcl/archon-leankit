"""Tests for lifecycle-context preservation hooks."""

import pytest

from src.server.services.engine.lifecycle_hooks import LifecycleHooks


def _make_task(**overrides):
    base = {
        "id": "t1",
        "title": "Fix auth bug",
        "task_type": "bug",
        "priority": "high",
        "complexity": "simple",
        "retry_count": 0,
        "allowed_paths": ["src/auth/"],
        "forbidden_paths": [".env"],
        "blocked_by": ["t0"],
    }
    base.update(overrides)
    return base


class TestCaptureRunStart:

    def test_captures_basic_fields(self):
        ctx = LifecycleHooks.capture_run_start(
            _make_task(), model="sonnet", token_profile="standard_feature",
            runner_key="claude-code-cli",
        )
        assert ctx["task_id"] == "t1"
        assert ctx["model"] == "sonnet"
        assert ctx["token_profile"] == "standard_feature"
        assert ctx["runner_key"] == "claude-code-cli"
        assert "timestamp" in ctx

    def test_captures_contract(self):
        contract = {"version": 2, "negotiation_status": "locked", "acceptance_criteria": ["AC1", "AC2"]}
        ctx = LifecycleHooks.capture_run_start(_make_task(), contract=contract)
        assert ctx["contract"]["version"] == 2
        assert ctx["contract"]["criteria_count"] == 2

    def test_captures_boundaries(self):
        ctx = LifecycleHooks.capture_run_start(_make_task())
        assert ctx["boundaries"]["allowed_paths"] == ["src/auth/"]
        assert ctx["boundaries"]["forbidden_paths"] == [".env"]

    def test_captures_dependencies(self):
        ctx = LifecycleHooks.capture_run_start(_make_task())
        assert ctx["dependencies"] == ["t0"]

    def test_no_boundaries_when_empty(self):
        task = _make_task(allowed_paths=[], forbidden_paths=[])
        ctx = LifecycleHooks.capture_run_start(task)
        assert "boundaries" not in ctx


class TestCaptureRunStop:

    def test_captures_result(self):
        result = {"result": "SUCCESS", "files_changed": 3, "summary": "Fixed auth token expiry"}
        ctx = LifecycleHooks.capture_run_stop(result=result)
        assert ctx["result_status"] == "SUCCESS"
        assert ctx["files_changed"] == 3

    def test_captures_runner_result(self):
        class MockResult:
            success = True
            exit_code = 0
            duration_seconds = 45.0
            timed_out = False

        ctx = LifecycleHooks.capture_run_stop(runner_result=MockResult())
        assert ctx["success"] is True
        assert ctx["exit_code"] == 0
        assert ctx["duration_seconds"] == 45.0

    def test_captures_metrics(self):
        ctx = LifecycleHooks.capture_run_stop(metrics={
            "total_tool_calls": 15, "cost_usd": 0.42,
        })
        assert ctx["total_tool_calls"] == 15
        assert ctx["cost_usd"] == 0.42


class TestCaptureRunTimeout:

    def test_captures_timeout(self):
        ctx = LifecycleHooks.capture_run_timeout(elapsed_seconds=600)
        assert ctx["reason"] == "timeout"
        assert ctx["elapsed_seconds"] == 600

    def test_captures_partial_files(self):
        ctx = LifecycleHooks.capture_run_timeout(
            partial_files=["src/a.py", "src/b.py"],
            partial_output="Last message: working on...",
        )
        assert ctx["files_modified"] == ["src/a.py", "src/b.py"]
        assert "working on" in ctx["last_output"]


class TestCaptureCompactionEvent:

    def test_captures_compaction(self):
        ctx = LifecycleHooks.capture_compaction_event(
            task_id="t1", stage="execute",
            token_count_before=5000, token_count_after=3000,
        )
        assert ctx["task_id"] == "t1"
        assert ctx["reduction_pct"] == 40.0

    def test_no_reduction_when_no_counts(self):
        ctx = LifecycleHooks.capture_compaction_event(task_id="t1", stage="execute")
        assert ctx["reduction_pct"] is None
