"""Tests for engine analytics service (C-P6-03, C-P6-04, D-P2-03)."""

from unittest.mock import MagicMock

import pytest

from src.server.services.engine.analytics_service import EngineAnalyticsService


def _make_run(
    run_id="r1", status="completed", model="sonnet", stage="execute",
    cost_usd=0.5, duration_seconds=60, retry_index=0,
    profile="standard_feature", started_at="2026-04-01T10:00:00Z",
    **overrides,
):
    base = {
        "id": run_id,
        "task_id": "t1",
        "project_id": "p1",
        "status": status,
        "stage": stage,
        "model": model,
        "retry_index": retry_index,
        "cost_usd": cost_usd,
        "duration_seconds": duration_seconds,
        "started_at": started_at,
        "finished_at": "2026-04-01T10:01:00Z",
        "metadata": {"profile_used": profile, "task_type": "feature"},
    }
    base.update(overrides)
    return base


def _make_service(runs):
    service = EngineAnalyticsService.__new__(EngineAnalyticsService)
    mock_client = MagicMock()
    service.supabase_client = mock_client

    execute_result = MagicMock()
    execute_result.data = runs
    mock_query = MagicMock()
    mock_query.select.return_value = mock_query
    mock_query.gte.return_value = mock_query
    mock_query.order.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.execute.return_value = execute_result
    mock_client.table.return_value = mock_query

    return service


class TestProfileMetrics:

    def test_empty_runs(self):
        service = _make_service([])
        result = service.get_profile_metrics()
        assert result["profiles"] == {}
        assert result["anomalies"] == []

    def test_single_profile_success(self):
        runs = [_make_run(f"r{i}", profile="standard_feature") for i in range(5)]
        service = _make_service(runs)
        result = service.get_profile_metrics()

        assert "standard_feature" in result["profiles"]
        p = result["profiles"]["standard_feature"]
        assert p["run_count"] == 5
        assert p["success_rate"] == 1.0
        assert p["avg_cost_usd"] == 0.5

    def test_mixed_success_failure(self):
        runs = [
            _make_run("r1", status="completed", profile="simple_bugfix"),
            _make_run("r2", status="failed", profile="simple_bugfix"),
            _make_run("r3", status="completed", profile="simple_bugfix"),
        ]
        service = _make_service(runs)
        result = service.get_profile_metrics()

        p = result["profiles"]["simple_bugfix"]
        assert p["success_rate"] == pytest.approx(0.667, abs=0.01)
        assert p["failed_count"] == 1

    def test_low_success_anomaly(self):
        runs = [
            _make_run(f"r{i}", status="failed" if i < 4 else "completed", profile="complex")
            for i in range(5)
        ]
        service = _make_service(runs)
        result = service.get_profile_metrics()

        anomalies = [a for a in result["anomalies"] if a["type"] == "low_success_rate"]
        assert len(anomalies) == 1
        assert "complex" in anomalies[0]["profile"]

    def test_over_provisioned_anomaly(self):
        runs = [
            _make_run(f"r{i}", status="completed", profile="research", cost_usd=2.0, retry_index=0)
            for i in range(6)
        ]
        service = _make_service(runs)
        result = service.get_profile_metrics()

        anomalies = [a for a in result["anomalies"] if a["type"] == "over_provisioned"]
        assert len(anomalies) == 1


class TestModelCostBreakdown:

    def test_empty_runs(self):
        service = _make_service([])
        result = service.get_model_cost_breakdown()
        assert result["breakdown"] == []

    def test_cost_by_model_and_stage(self):
        runs = [
            _make_run("r1", model="sonnet", stage="execute", cost_usd=0.5),
            _make_run("r2", model="opus", stage="execute", cost_usd=2.0),
            _make_run("r3", model="sonnet", stage="code-review", cost_usd=0.3),
        ]
        service = _make_service(runs)
        result = service.get_model_cost_breakdown()

        assert len(result["breakdown"]) == 3

    def test_sorted_by_total_cost(self):
        runs = [
            _make_run("r1", model="haiku", cost_usd=0.1),
            _make_run("r2", model="opus", cost_usd=5.0),
        ]
        service = _make_service(runs)
        result = service.get_model_cost_breakdown()

        assert result["breakdown"][0]["model"] == "opus"

    def test_recommendations_for_simple_tasks(self):
        runs = [
            _make_run(f"r{i}", model="sonnet", cost_usd=1.0,
                      metadata={"profile_used": "simple", "task_type": "docs"})
            for i in range(6)
        ]
        service = _make_service(runs)
        result = service.get_model_cost_breakdown()

        assert len(result["recommendations"]) >= 1
        assert "downgrade" in result["recommendations"][0]["type"]


class TestCostTrending:

    def test_empty_runs(self):
        service = _make_service([])
        result = service.get_cost_trending()
        assert result["time_series"] == []
        assert result["total_cost_usd"] == 0.0

    def test_daily_aggregation(self):
        runs = [
            _make_run("r1", cost_usd=1.0, started_at="2026-04-01T10:00:00Z"),
            _make_run("r2", cost_usd=2.0, started_at="2026-04-01T14:00:00Z"),
            _make_run("r3", cost_usd=0.5, started_at="2026-04-02T10:00:00Z"),
        ]
        service = _make_service(runs)
        result = service.get_cost_trending()

        assert len(result["time_series"]) == 2
        day1 = next(d for d in result["time_series"] if d["date"] == "2026-04-01")
        assert day1["cost_usd"] == 3.0

    def test_anomaly_detection(self):
        # 4 normal runs + 1 anomalous
        runs = [
            _make_run(f"r{i}", cost_usd=0.5, profile="std") for i in range(4)
        ]
        runs.append(_make_run("r_outlier", cost_usd=5.0, profile="std"))
        service = _make_service(runs)
        result = service.get_cost_trending()

        assert len(result["anomalous_runs"]) == 1
        assert result["anomalous_runs"][0]["run_id"] == "r_outlier"
        assert result["anomalous_runs"][0]["ratio"] > 3.0

    def test_total_cost(self):
        runs = [
            _make_run("r1", cost_usd=1.0),
            _make_run("r2", cost_usd=2.0),
        ]
        service = _make_service(runs)
        result = service.get_cost_trending()
        assert result["total_cost_usd"] == 3.0

    def test_zero_cost_runs_excluded(self):
        runs = [
            _make_run("r1", cost_usd=0.0),
            _make_run("r2", cost_usd=None),
        ]
        service = _make_service(runs)
        result = service.get_cost_trending()
        assert result["total_cost_usd"] == 0.0
        assert result["anomalous_runs"] == []
