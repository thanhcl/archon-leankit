"""
Tests for Sprint Stats Service

Tests the SprintStatsService business logic with mocked Supabase client.
"""

import json

import pytest
from unittest.mock import MagicMock

from src.server.services.sprint_stats_service import SprintStatsService


def _make_task(
    task_id="t1",
    status="done",
    retry_count=0,
    created_at="2026-03-15T10:00:00+00:00",
    updated_at="2026-03-15T12:00:00+00:00",
    state_changed_at="2026-03-15T12:00:00+00:00",
    execution_result=None,
    complexity="simple",
    priority="medium",
):
    return {
        "id": task_id,
        "title": f"Task {task_id}",
        "status": status,
        "retry_count": retry_count,
        "created_at": created_at,
        "updated_at": updated_at,
        "state_changed_at": state_changed_at,
        "execution_result": execution_result,
        "complexity": complexity,
        "priority": priority,
    }


def _make_service(tasks):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.data = tasks
    mock_client.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
        mock_response
    )
    return SprintStatsService(supabase_client=mock_client)


class TestSprintStatsService:
    """Unit tests for SprintStatsService."""

    def test_empty_project(self):
        service = _make_service([])
        success, result = service.get_sprint_stats("proj-1")
        assert success
        assert result["summary"]["total_tasks"] == 0
        assert result["summary"]["done"] == 0
        assert result["summary"]["first_pass_rate"] == 0.0
        assert result["sprints"] == []

    def test_summary_with_done_tasks(self):
        tasks = [
            _make_task("t1", status="done", retry_count=0),
            _make_task("t2", status="done", retry_count=2),
            _make_task("t3", status="todo", retry_count=0),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        summary = result["summary"]
        assert summary["total_tasks"] == 3
        assert summary["done"] == 2
        # first_pass: t1 (retry=0 <= 1), t2 (retry=2 > 1) => 1/2 = 0.5
        assert summary["first_pass_rate"] == 0.5
        assert summary["avg_retries"] == 1.0  # (0+2)/2

    def test_sprints_grouped_by_date(self):
        tasks = [
            _make_task("t1", status="done", state_changed_at="2026-03-15T12:00:00+00:00"),
            _make_task("t2", status="done", state_changed_at="2026-03-15T14:00:00+00:00"),
            _make_task("t3", status="done", state_changed_at="2026-03-16T10:00:00+00:00"),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        assert len(result["sprints"]) == 2
        assert result["sprints"][0]["date"] == "2026-03-15"
        assert result["sprints"][0]["tasks_completed"] == 2
        assert result["sprints"][1]["date"] == "2026-03-16"
        assert result["sprints"][1]["tasks_completed"] == 1

    def test_trends_with_two_dates(self):
        tasks = [
            _make_task("t1", status="done", retry_count=0, state_changed_at="2026-03-15T12:00:00+00:00"),
            _make_task("t2", status="done", retry_count=2, state_changed_at="2026-03-16T12:00:00+00:00"),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        trends = result["trends"]
        assert trends["available"] is True
        assert trends["previous_date"] == "2026-03-15"
        assert trends["current_date"] == "2026-03-16"

    def test_trends_unavailable_single_date(self):
        tasks = [_make_task("t1", status="done")]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        assert result["trends"]["available"] is False

    def test_cost_parsing_from_direct_field(self):
        tasks = [
            _make_task("t1", status="done", execution_result={"total_cost_usd": 1.25}),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        assert result["summary"]["estimated_cost_usd"] == 1.25

    def test_cost_parsing_from_stdout(self):
        tasks = [
            _make_task(
                "t1",
                status="done",
                execution_result={"stdout": '{"total_cost_usd": 2.50, "other": "data"}'},
            ),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        assert result["summary"]["estimated_cost_usd"] == 2.50

    def test_cost_parsing_from_json_string(self):
        tasks = [
            _make_task(
                "t1",
                status="done",
                execution_result=json.dumps({"total_cost_usd": 3.75}),
            ),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        assert result["summary"]["estimated_cost_usd"] == 3.75

    def test_duration_calculation(self):
        tasks = [
            _make_task(
                "t1",
                status="done",
                created_at="2026-03-15T10:00:00+00:00",
                state_changed_at="2026-03-15T12:00:00+00:00",
            ),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        assert result["summary"]["avg_duration_hours"] == 2.0

    def test_learnings_extraction(self):
        tasks = [
            _make_task(
                "t1",
                status="done",
                execution_result={"learnings": ["Use fixtures", "Mock external calls"]},
            ),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        assert "Use fixtures" in result["top_learnings"]
        assert "Mock external calls" in result["top_learnings"]

    def test_code_patterns_count(self):
        tasks = [
            _make_task(
                "t1",
                status="done",
                execution_result={"code_patterns": [{"name": "p1"}, {"name": "p2"}]},
            ),
            _make_task(
                "t2",
                status="done",
                execution_result={"code_patterns": [{"name": "p3"}]},
            ),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        assert result["code_patterns_count"] == 3

    def test_database_error_returns_failure(self):
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.side_effect = (
            Exception("DB connection failed")
        )
        service = SprintStatsService(supabase_client=mock_client)
        success, result = service.get_sprint_stats("proj-1")
        assert not success
        assert "error" in result

    def test_response_structure(self):
        """Verify the full response structure matches requirements."""
        tasks = [
            _make_task("t1", status="done", retry_count=0, state_changed_at="2026-03-15T12:00:00+00:00"),
            _make_task("t2", status="done", retry_count=1, state_changed_at="2026-03-16T12:00:00+00:00"),
            _make_task("t3", status="todo"),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success

        # Verify top-level keys
        assert "project_id" in result
        assert "summary" in result
        assert "sprints" in result
        assert "trends" in result
        assert "top_learnings" in result
        assert "code_patterns_count" in result

        # Verify summary keys
        summary = result["summary"]
        assert "total_tasks" in summary
        assert "done" in summary
        assert "first_pass_rate" in summary
        assert "avg_retries" in summary
        assert "avg_duration_hours" in summary
        assert "estimated_cost_usd" in summary

        # Verify sprint structure
        for sprint in result["sprints"]:
            assert "date" in sprint
            assert "tasks_completed" in sprint
            assert "first_pass_rate" in sprint
            assert "avg_retries" in sprint

    def test_no_cost_returns_zero(self):
        tasks = [_make_task("t1", status="done", execution_result=None)]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        assert result["summary"]["estimated_cost_usd"] == 0.0

    def test_first_pass_rate_all_first_pass(self):
        tasks = [
            _make_task("t1", status="done", retry_count=0),
            _make_task("t2", status="done", retry_count=1),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        # Both have retry_count <= 1, so first_pass_rate = 1.0
        assert result["summary"]["first_pass_rate"] == 1.0

    def test_injection_metrics_with_mixed_tasks(self):
        """Injection metrics separate tasks with and without injection."""
        tasks = [
            _make_task(
                "t1", status="done", retry_count=0,
                execution_result={
                    "injection": {"learnings": 3, "patterns": 2, "kb_chunks": 1, "tokens": 400},
                },
            ),
            _make_task(
                "t2", status="done", retry_count=2,
                execution_result={
                    "injection": {"learnings": 5, "patterns": 0, "kb_chunks": 2, "tokens": 600},
                },
            ),
            _make_task("t3", status="done", retry_count=1, execution_result=None),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        inj = result["injection_metrics"]
        assert inj["tasks_with_injection"] == 2
        assert inj["tasks_without_injection"] == 1
        assert inj["avg_learnings_per_task"] == 4.0  # (3+5)/2
        assert inj["avg_patterns_per_task"] == 1.0   # (2+0)/2
        assert inj["avg_kb_chunks_per_task"] == 1.5   # (1+2)/2
        assert inj["avg_tokens_per_task"] == 500.0     # (400+600)/2
        assert inj["retry_rate_with_injection"] == 1.0  # (0+2)/2
        assert inj["retry_rate_without_injection"] == 1.0  # 1/1

    def test_injection_metrics_no_injection(self):
        """Injection metrics handle no tasks with injection data."""
        tasks = [
            _make_task("t1", status="done", retry_count=0, execution_result=None),
            _make_task("t2", status="done", retry_count=1, execution_result={"result": "SUCCESS"}),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        inj = result["injection_metrics"]
        assert inj["tasks_with_injection"] == 0
        assert inj["tasks_without_injection"] == 2
        assert inj["avg_learnings_per_task"] == 0.0

    def test_injection_metrics_zero_injection_values_treated_as_no_injection(self):
        """Tasks with injection dict but all zero values are treated as no-injection."""
        tasks = [
            _make_task(
                "t1", status="done", retry_count=0,
                execution_result={
                    "injection": {"learnings": 0, "patterns": 0, "kb_chunks": 0, "tokens": 0},
                },
            ),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        inj = result["injection_metrics"]
        assert inj["tasks_with_injection"] == 0
        assert inj["tasks_without_injection"] == 1

    def test_injection_metrics_in_response_structure(self):
        """Injection metrics present in response."""
        tasks = [_make_task("t1", status="done")]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1")
        assert success
        assert "injection_metrics" in result
        inj = result["injection_metrics"]
        expected_keys = {
            "tasks_with_injection", "tasks_without_injection",
            "avg_learnings_per_task", "avg_patterns_per_task",
            "avg_kb_chunks_per_task", "avg_tokens_per_task",
            "retry_rate_with_injection", "retry_rate_without_injection",
        }
        assert set(inj.keys()) == expected_keys


class TestSprintStatsGroupBy:
    """Tests for group_by parameter."""

    def _make_task_with_fields(self, task_id, task_type="feature", module=None, sprint=None, feature=None, phase=None, **kwargs):
        base = _make_task(task_id, **kwargs)
        base["task_type"] = task_type
        base["module"] = module
        base["sprint"] = sprint
        base["feature"] = feature
        base["phase"] = phase
        return base

    def test_group_by_date_default(self):
        tasks = [
            self._make_task_with_fields("t1", status="done", state_changed_at="2026-03-15T12:00:00+00:00"),
            self._make_task_with_fields("t2", status="done", state_changed_at="2026-03-16T12:00:00+00:00"),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1", group_by="date")
        assert success
        assert result["group_by"] == "date"
        assert len(result["sprints"]) == 2

    def test_group_by_task_type(self):
        tasks = [
            self._make_task_with_fields("t1", task_type="bug", status="done"),
            self._make_task_with_fields("t2", task_type="bug", status="done"),
            self._make_task_with_fields("t3", task_type="feature", status="done"),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1", group_by="task_type")
        assert success
        assert result["group_by"] == "task_type"
        assert len(result["sprints"]) == 2
        # Sorted alphabetically
        assert result["sprints"][0]["date"] == "bug"
        assert result["sprints"][0]["tasks_completed"] == 2
        assert result["sprints"][1]["date"] == "feature"
        assert result["sprints"][1]["tasks_completed"] == 1

    def test_group_by_module(self):
        tasks = [
            self._make_task_with_fields("t1", module="engine", status="done"),
            self._make_task_with_fields("t2", module="engine", status="done"),
            self._make_task_with_fields("t3", module=None, status="done"),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1", group_by="module")
        assert success
        assert len(result["sprints"]) == 2
        group_names = [s["date"] for s in result["sprints"]]
        assert "engine" in group_names
        assert "unassigned" in group_names

    def test_group_by_sprint(self):
        tasks = [
            self._make_task_with_fields("t1", sprint="S1", status="done"),
            self._make_task_with_fields("t2", sprint="S2", status="done"),
        ]
        service = _make_service(tasks)
        success, result = service.get_sprint_stats("proj-1", group_by="sprint")
        assert success
        assert len(result["sprints"]) == 2

    def test_invalid_group_by(self):
        service = _make_service([])
        success, result = service.get_sprint_stats("proj-1", group_by="invalid")
        assert not success
        assert "Invalid group_by" in result["error"]
