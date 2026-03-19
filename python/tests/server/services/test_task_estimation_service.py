"""
Tests for TaskEstimationService

Tests estimation logic with mocked Supabase client.
"""

import pytest
from unittest.mock import MagicMock, call

from src.server.services.task_estimation_service import TaskEstimationService


def _make_task(
    task_id="t1",
    status="done",
    complexity="simple",
    priority="medium",
    retry_count=0,
    created_at="2026-03-15T10:00:00+00:00",
    updated_at="2026-03-15T12:00:00+00:00",
    state_changed_at="2026-03-15T12:00:00+00:00",
    execution_result=None,
):
    return {
        "id": task_id,
        "title": f"Task {task_id}",
        "status": status,
        "complexity": complexity,
        "priority": priority,
        "retry_count": retry_count,
        "created_at": created_at,
        "updated_at": updated_at,
        "state_changed_at": state_changed_at,
        "execution_result": execution_result,
    }


def _make_service_with_done_tasks(done_tasks):
    """Create service where completed tasks query returns done_tasks."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.data = done_tasks

    # Chain: table().select().eq("project_id", ...).eq("status", "done").or_().execute()
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.or_.return_value.execute.return_value = (
        mock_response
    )
    return TaskEstimationService(supabase_client=mock_client)


def _make_service_with_all_tasks(all_tasks, done_tasks=None):
    """Create service for get_project_estimates which queries all tasks and done tasks separately."""
    mock_client = MagicMock()

    # For _query_all_tasks: table().select().eq("project_id").or_().execute()
    all_response = MagicMock()
    all_response.data = all_tasks

    # For _query_completed_tasks: table().select().eq("project_id").eq("status", "done").or_().execute()
    done_response = MagicMock()
    done_response.data = done_tasks if done_tasks is not None else [t for t in all_tasks if t["status"] == "done"]

    # We need to handle both query patterns
    # Pattern 1 (all tasks): .eq("project_id", X).or_().execute()
    # Pattern 2 (done tasks): .eq("project_id", X).eq("status", "done").or_().execute()
    mock_eq = MagicMock()

    # When .eq("status", "done") is called after first eq
    mock_eq.eq.return_value.or_.return_value.execute.return_value = done_response
    # When .or_() is called directly after first eq (all tasks)
    mock_eq.or_.return_value.execute.return_value = all_response

    mock_client.table.return_value.select.return_value.eq.return_value = mock_eq

    return TaskEstimationService(supabase_client=mock_client)


class TestEstimateTask:
    """Tests for single task estimation."""

    def test_no_completed_tasks_returns_empty(self):
        service = _make_service_with_done_tasks([])
        success, result = service.estimate_task("proj-1", "simple", "medium")
        assert success
        assert result["estimate_duration_seconds"] == 0
        assert result["estimate_cost_usd"] == 0.0
        assert result["confidence"] == "none"
        assert result["sample_size"] == 0

    def test_single_completed_task(self):
        # 2 hours = 7200 seconds
        tasks = [
            _make_task("t1", created_at="2026-03-15T10:00:00+00:00", state_changed_at="2026-03-15T12:00:00+00:00"),
        ]
        service = _make_service_with_done_tasks(tasks)
        success, result = service.estimate_task("proj-1", "simple", "medium")
        assert success
        # With only 1 task < MIN_SAMPLE_SIZE, falls back to all tasks
        assert result["estimate_duration_seconds"] == 7200
        assert result["sample_size"] == 1

    def test_multiple_similar_tasks_average(self):
        # Task 1: 2 hours, Task 2: 4 hours
        tasks = [
            _make_task(
                "t1",
                created_at="2026-03-15T10:00:00+00:00",
                state_changed_at="2026-03-15T12:00:00+00:00",
                complexity="simple",
                priority="medium",
            ),
            _make_task(
                "t2",
                created_at="2026-03-15T10:00:00+00:00",
                state_changed_at="2026-03-15T14:00:00+00:00",
                complexity="simple",
                priority="medium",
            ),
        ]
        service = _make_service_with_done_tasks(tasks)
        success, result = service.estimate_task("proj-1", "simple", "medium")
        assert success
        # Weighted average favoring more recent (t2)
        assert result["estimate_duration_seconds"] > 0
        assert result["sample_size"] == 2

    def test_complexity_similarity_scoring(self):
        # Complex tasks should be weighted higher when estimating complex
        tasks = [
            _make_task(
                "t1",
                created_at="2026-03-15T10:00:00+00:00",
                state_changed_at="2026-03-15T11:00:00+00:00",
                complexity="simple",
                priority="medium",
            ),
            _make_task(
                "t2",
                created_at="2026-03-15T10:00:00+00:00",
                state_changed_at="2026-03-15T14:00:00+00:00",
                complexity="complex",
                priority="medium",
            ),
        ]
        service = _make_service_with_done_tasks(tasks)
        _, simple_result = service.estimate_task("proj-1", "simple", "medium")
        _, complex_result = service.estimate_task("proj-1", "complex", "medium")

        # Complex estimate should be higher since the complex task took longer
        assert complex_result["estimate_duration_seconds"] > simple_result["estimate_duration_seconds"]

    def test_cost_estimation(self):
        tasks = [
            _make_task(
                "t1",
                execution_result={"total_cost_usd": 1.50},
            ),
            _make_task(
                "t2",
                execution_result={"total_cost_usd": 2.50},
                state_changed_at="2026-03-16T12:00:00+00:00",
            ),
        ]
        service = _make_service_with_done_tasks(tasks)
        success, result = service.estimate_task("proj-1")
        assert success
        assert result["estimate_cost_usd"] > 0

    def test_confidence_high_with_many_exact_matches(self):
        tasks = [
            _make_task(f"t{i}", complexity="complex", priority="high")
            for i in range(6)
        ]
        service = _make_service_with_done_tasks(tasks)
        success, result = service.estimate_task("proj-1", "complex", "high")
        assert success
        assert result["confidence"] == "high"

    def test_confidence_medium_with_some_matches(self):
        tasks = [
            _make_task("t1", complexity="simple", priority="medium"),
            _make_task("t2", complexity="simple", priority="medium"),
            _make_task("t3", complexity="complex", priority="high"),
        ]
        service = _make_service_with_done_tasks(tasks)
        success, result = service.estimate_task("proj-1", "simple", "medium")
        assert success
        assert result["confidence"] == "medium"

    def test_confidence_low_with_few_matches(self):
        tasks = [
            _make_task("t1", complexity="complex", priority="high"),
            _make_task("t2", complexity="complex", priority="high"),
        ]
        service = _make_service_with_done_tasks(tasks)
        success, result = service.estimate_task("proj-1", "simple", "low")
        assert success
        assert result["confidence"] == "low"

    def test_database_error_returns_failure(self):
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.or_.return_value.execute.side_effect = (
            Exception("DB connection failed")
        )
        service = TaskEstimationService(supabase_client=mock_client)
        success, result = service.estimate_task("proj-1")
        assert not success
        assert "error" in result


class TestGetProjectEstimates:
    """Tests for project-wide estimation."""

    def test_empty_project(self):
        service = _make_service_with_all_tasks([])
        success, result = service.get_project_estimates("proj-1")
        assert success
        assert result["estimates"] == {}
        assert result["predicted_vs_actual"] == []

    def test_active_tasks_get_estimates(self):
        all_tasks = [
            _make_task("t1", status="done"),
            _make_task("t2", status="done", state_changed_at="2026-03-16T12:00:00+00:00"),
            _make_task("t3", status="todo"),
        ]
        service = _make_service_with_all_tasks(all_tasks)
        success, result = service.get_project_estimates("proj-1")
        assert success
        # Active task t3 should have an estimate
        assert "t3" in result["estimates"]
        assert result["estimates"]["t3"]["task_id"] == "t3"

    def test_done_tasks_excluded_from_estimates(self):
        all_tasks = [
            _make_task("t1", status="done"),
            _make_task("t2", status="done", state_changed_at="2026-03-16T12:00:00+00:00"),
        ]
        service = _make_service_with_all_tasks(all_tasks)
        success, result = service.get_project_estimates("proj-1")
        assert success
        assert "t1" not in result["estimates"]
        assert "t2" not in result["estimates"]

    def test_cancelled_tasks_excluded(self):
        all_tasks = [
            _make_task("t1", status="done"),
            _make_task("t2", status="done", state_changed_at="2026-03-16T12:00:00+00:00"),
            _make_task("t3", status="cancelled"),
        ]
        service = _make_service_with_all_tasks(all_tasks)
        success, result = service.get_project_estimates("proj-1")
        assert success
        assert "t3" not in result["estimates"]


class TestPredictedVsActual:
    """Tests for predicted vs actual comparison."""

    def test_needs_minimum_tasks(self):
        tasks = [_make_task("t1")]
        service = _make_service_with_done_tasks(tasks)
        # Access private method for targeted testing
        result = service._compute_predicted_vs_actual(tasks)
        assert result == []

    def test_predicted_vs_actual_with_enough_tasks(self):
        tasks = [
            _make_task(
                "t1",
                created_at="2026-03-15T10:00:00+00:00",
                state_changed_at="2026-03-15T12:00:00+00:00",
            ),
            _make_task(
                "t2",
                created_at="2026-03-15T10:00:00+00:00",
                state_changed_at="2026-03-15T14:00:00+00:00",
            ),
            _make_task(
                "t3",
                created_at="2026-03-15T10:00:00+00:00",
                state_changed_at="2026-03-15T13:00:00+00:00",
            ),
        ]
        service = _make_service_with_done_tasks(tasks)
        result = service._compute_predicted_vs_actual(tasks)
        assert len(result) > 0
        for item in result:
            assert "task_id" in item
            assert "predicted_duration_seconds" in item
            assert "actual_duration_seconds" in item
            assert "duration_accuracy_pct" in item

    def test_accuracy_calculation(self):
        # Two identical tasks should predict perfectly
        tasks = [
            _make_task(
                "t1",
                created_at="2026-03-15T10:00:00+00:00",
                state_changed_at="2026-03-15T12:00:00+00:00",
            ),
            _make_task(
                "t2",
                created_at="2026-03-15T10:00:00+00:00",
                state_changed_at="2026-03-15T12:00:00+00:00",
            ),
            _make_task(
                "t3",
                created_at="2026-03-15T10:00:00+00:00",
                state_changed_at="2026-03-15T12:00:00+00:00",
            ),
        ]
        service = _make_service_with_done_tasks(tasks)
        result = service._compute_predicted_vs_actual(tasks)
        # All tasks are identical, so predicted should match actual perfectly
        for item in result:
            assert item["duration_accuracy_pct"] == 100.0


class TestWeightedAverage:
    """Tests for the weighted average calculation."""

    def test_recency_favors_recent(self):
        service = TaskEstimationService(supabase_client=MagicMock())

        # Both tasks have same 2h duration, different completion times for ordering
        scored = [
            (
                _make_task(
                    "recent",
                    created_at="2026-03-16T10:00:00+00:00",
                    state_changed_at="2026-03-16T12:00:00+00:00",
                ),
                1.0,
            ),
            (
                _make_task(
                    "old",
                    created_at="2026-03-15T10:00:00+00:00",
                    state_changed_at="2026-03-15T12:00:00+00:00",
                ),
                1.0,
            ),
        ]

        # Both 2h = 7200s, so weighted average should be 7200
        result = service._weighted_average(scored, lambda t: service._calc_duration_seconds(t))
        assert result == 7200

    def test_similarity_score_weighting(self):
        service = TaskEstimationService(supabase_client=MagicMock())

        # Higher similarity score = more weight
        scored = [
            (
                _make_task(
                    "similar",
                    created_at="2026-03-15T10:00:00+00:00",
                    state_changed_at="2026-03-15T14:00:00+00:00",
                ),
                4.0,  # High similarity
            ),
            (
                _make_task(
                    "different",
                    created_at="2026-03-15T10:00:00+00:00",
                    state_changed_at="2026-03-15T11:00:00+00:00",
                ),
                1.0,  # Low similarity
            ),
        ]

        result = service._weighted_average(scored, lambda t: service._calc_duration_seconds(t))
        # Should be closer to 4h (14400s) than 1h (3600s) due to higher weight
        assert result > 10000  # Closer to 14400 than 3600

    def test_zero_values_skipped(self):
        service = TaskEstimationService(supabase_client=MagicMock())

        scored = [
            (_make_task("t1"), 1.0),  # Has 2h duration
            (_make_task("t2", created_at="", state_changed_at=""), 1.0),  # No duration
        ]

        result = service._weighted_average(scored, lambda t: service._calc_duration_seconds(t))
        assert result == 7200  # Only the valid task counts


class TestCostParsing:
    """Tests for cost parsing from execution_result."""

    def test_direct_cost(self):
        service = TaskEstimationService(supabase_client=MagicMock())
        task = _make_task(execution_result={"total_cost_usd": 2.75})
        assert service._parse_cost(task) == 2.75

    def test_cost_from_stdout(self):
        service = TaskEstimationService(supabase_client=MagicMock())
        task = _make_task(
            execution_result={"stdout": '{"total_cost_usd": 3.50}'}
        )
        assert service._parse_cost(task) == 3.50

    def test_no_cost(self):
        service = TaskEstimationService(supabase_client=MagicMock())
        task = _make_task(execution_result=None)
        assert service._parse_cost(task) == 0.0

    def test_json_string_cost(self):
        import json
        service = TaskEstimationService(supabase_client=MagicMock())
        task = _make_task(execution_result=json.dumps({"total_cost_usd": 1.25}))
        assert service._parse_cost(task) == 1.25


class TestResponseStructure:
    """Tests for response shape consistency."""

    def test_estimate_task_structure(self):
        tasks = [_make_task("t1"), _make_task("t2")]
        service = _make_service_with_done_tasks(tasks)
        success, result = service.estimate_task("proj-1")
        assert success
        assert "estimate_duration_seconds" in result
        assert "estimate_cost_usd" in result
        assert "sample_size" in result
        assert "confidence" in result

    def test_empty_estimate_structure(self):
        result = TaskEstimationService._empty_estimate("test reason")
        assert result["estimate_duration_seconds"] == 0
        assert result["estimate_cost_usd"] == 0.0
        assert result["sample_size"] == 0
        assert result["confidence"] == "none"
        assert result["reason"] == "test reason"
