"""
Unit tests for GET /api/engine/metrics endpoint.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.server.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _make_task(project_id: str, status: str = "done", retry_count: int = 0, **overrides) -> dict:
    base = {
        "id": f"task-{id(overrides)}",
        "project_id": project_id,
        "status": status,
        "retry_count": retry_count,
        "updated_at": "2026-03-30T10:00:00Z",
        "state_changed_at": "2026-03-30T10:00:00Z",
        "created_at": "2026-03-30T09:00:00Z",
    }
    base.update(overrides)
    return base


def _make_project(pid: str, title: str = "Test Project") -> dict:
    return {"id": pid, "title": title, "office_settings": {}}


def _mock_budget_status(today_cost: float = 1.0, week_cost: float = 5.0) -> dict:
    return {
        "status": "ok",
        "today": {"cost_usd": today_cost, "budget_usd": 100.0, "usage_pct": 1.0},
        "weekly": {"total_cost_usd": week_cost, "budget_usd": 500.0, "usage_pct": 1.0},
    }


class TestGetEngineMetrics:
    """Tests for GET /api/engine/metrics."""

    @patch("src.server.api_routes.engine_api.get_supabase_client")
    @patch("src.server.api_routes.engine_api.CostBudgetService")
    @patch("src.server.api_routes.engine_api.ProjectService")
    @patch("src.server.api_routes.engine_api.TaskService")
    @patch("src.server.api_routes.engine_api.HealthMonitor")
    def test_metrics_returns_200_with_data(
        self, mock_monitor_cls, mock_task_cls, mock_proj_cls, mock_budget_cls, mock_supabase, client
    ):
        # Setup projects
        mock_proj = MagicMock()
        mock_proj.list_office_configs.return_value = (True, {"projects": [_make_project("p1", "Alpha")]})
        mock_proj_cls.return_value = mock_proj

        # Setup tasks (2 done: 1 first-pass, 1 retry)
        mock_task = MagicMock()
        mock_task.list_tasks.return_value = (True, {"tasks": [
            _make_task("p1", retry_count=0, id="t1"),
            _make_task("p1", retry_count=2, id="t2"),
        ]})
        mock_task_cls.return_value = mock_task

        # Setup budget
        mock_budget = MagicMock()
        mock_budget.get_cost_status.return_value = (True, _mock_budget_status(2.5, 10.0))
        mock_budget_cls.return_value = mock_budget

        # Setup supabase (total costs) - chain: table().select().not_.is_().execute()
        exec_result = MagicMock(data=[
            {"project_id": "p1", "cost_usd": 5.0},
            {"project_id": "p1", "cost_usd": 15.0},
        ])
        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.not_.is_.return_value.execute.return_value = exec_result
        mock_supabase.return_value = mock_sb

        # Setup health monitor alerts (empty)
        mock_monitor_cls.snapshot_alerts.return_value = []

        resp = client.get("/api/engine/metrics")
        assert resp.status_code == 200
        data = resp.json()

        # Structure checks
        assert "projects" in data
        assert "totals" in data
        assert "recent_alerts" in data

        # Per-project checks
        assert len(data["projects"]) == 1
        proj = data["projects"][0]
        assert proj["project_id"] == "p1"
        assert proj["project_name"] == "Alpha"
        assert proj["cost"]["today_usd"] == 2.5
        assert proj["cost"]["week_usd"] == 10.0
        assert proj["cost"]["total_usd"] == 20.0
        assert proj["quality"]["total_done"] == 2
        assert proj["quality"]["first_pass_count"] == 1
        assert proj["quality"]["first_pass_rate"] == 0.5
        assert proj["quality"]["avg_retries"] == 1.0

        # Global totals
        assert data["totals"]["cost"]["today_usd"] == 2.5
        assert data["totals"]["quality"]["total_done"] == 2
        assert data["totals"]["quality"]["first_pass_rate"] == 0.5

    @patch("src.server.api_routes.engine_api.get_supabase_client")
    @patch("src.server.api_routes.engine_api.CostBudgetService")
    @patch("src.server.api_routes.engine_api.ProjectService")
    @patch("src.server.api_routes.engine_api.TaskService")
    @patch("src.server.api_routes.engine_api.HealthMonitor")
    def test_metrics_empty_projects(
        self, mock_monitor_cls, mock_task_cls, mock_proj_cls, mock_budget_cls, mock_supabase, client
    ):
        mock_proj = MagicMock()
        mock_proj.list_office_configs.return_value = (True, {"projects": []})
        mock_proj_cls.return_value = mock_proj

        mock_task = MagicMock()
        mock_task.list_tasks.return_value = (True, {"tasks": []})
        mock_task_cls.return_value = mock_task

        mock_budget_cls.return_value = MagicMock()

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.not_.is_.return_value.execute.return_value = MagicMock(data=[])
        mock_supabase.return_value = mock_sb

        mock_monitor_cls.snapshot_alerts.return_value = []

        resp = client.get("/api/engine/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["projects"] == []
        assert data["totals"]["quality"]["total_done"] == 0
        assert data["totals"]["quality"]["first_pass_rate"] == 1.0
        assert data["totals"]["cost"]["today_usd"] == 0.0
        assert data["recent_alerts"] == []

    @patch("src.server.api_routes.engine_api.get_supabase_client")
    @patch("src.server.api_routes.engine_api.CostBudgetService")
    @patch("src.server.api_routes.engine_api.ProjectService")
    @patch("src.server.api_routes.engine_api.TaskService")
    @patch("src.server.api_routes.engine_api.HealthMonitor")
    def test_metrics_includes_health_alerts(
        self, mock_monitor_cls, mock_task_cls, mock_proj_cls, mock_budget_cls, mock_supabase, client
    ):
        mock_proj = MagicMock()
        mock_proj.list_office_configs.return_value = (True, {"projects": [_make_project("p1")]})
        mock_proj_cls.return_value = mock_proj

        mock_task = MagicMock()
        mock_task.list_tasks.return_value = (True, {"tasks": []})
        mock_task_cls.return_value = mock_task

        mock_budget = MagicMock()
        mock_budget.get_cost_status.return_value = (True, _mock_budget_status())
        mock_budget_cls.return_value = mock_budget

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.not_.is_.return_value.execute.return_value = MagicMock(data=[])
        mock_supabase.return_value = mock_sb

        # Return some alerts
        mock_monitor_cls.snapshot_alerts.return_value = [
            {
                "metric": "first_pass_rate",
                "current_value": 0.3,
                "threshold": 0.6,
                "severity": "critical",
                "suggestion": "Switch to API review",
                "auto_action_taken": None,
                "timestamp": "2026-03-30T12:00:00",
            },
        ]

        resp = client.get("/api/engine/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["recent_alerts"]) == 1
        assert data["recent_alerts"][0]["metric"] == "first_pass_rate"
        assert data["recent_alerts"][0]["severity"] == "critical"

    @patch("src.server.api_routes.engine_api.get_supabase_client")
    @patch("src.server.api_routes.engine_api.CostBudgetService")
    @patch("src.server.api_routes.engine_api.ProjectService")
    @patch("src.server.api_routes.engine_api.TaskService")
    @patch("src.server.api_routes.engine_api.HealthMonitor")
    def test_metrics_alerts_capped_at_10(
        self, mock_monitor_cls, mock_task_cls, mock_proj_cls, mock_budget_cls, mock_supabase, client
    ):
        mock_proj = MagicMock()
        mock_proj.list_office_configs.return_value = (True, {"projects": []})
        mock_proj_cls.return_value = mock_proj

        mock_task = MagicMock()
        mock_task.list_tasks.return_value = (True, {"tasks": []})
        mock_task_cls.return_value = mock_task

        mock_budget_cls.return_value = MagicMock()

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.not_.is_.return_value.execute.return_value = MagicMock(data=[])
        mock_supabase.return_value = mock_sb

        # Return 15 alerts
        mock_monitor_cls.snapshot_alerts.return_value = [
            {"metric": f"metric_{i}", "timestamp": f"2026-03-30T{i:02d}:00:00"}
            for i in range(15)
        ]

        resp = client.get("/api/engine/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["recent_alerts"]) == 10
        # Should be sorted descending by timestamp
        assert data["recent_alerts"][0]["timestamp"] == "2026-03-30T14:00:00"

    @patch("src.server.api_routes.engine_api.get_supabase_client")
    @patch("src.server.api_routes.engine_api.CostBudgetService")
    @patch("src.server.api_routes.engine_api.ProjectService")
    @patch("src.server.api_routes.engine_api.TaskService")
    @patch("src.server.api_routes.engine_api.HealthMonitor")
    def test_metrics_multi_project(
        self, mock_monitor_cls, mock_task_cls, mock_proj_cls, mock_budget_cls, mock_supabase, client
    ):
        mock_proj = MagicMock()
        mock_proj.list_office_configs.return_value = (True, {"projects": [
            _make_project("p1", "Alpha"),
            _make_project("p2", "Beta"),
        ]})
        mock_proj_cls.return_value = mock_proj

        mock_task = MagicMock()
        mock_task.list_tasks.return_value = (True, {"tasks": [
            _make_task("p1", retry_count=0, id="t1"),
            _make_task("p2", retry_count=1, id="t2"),
            _make_task("p2", retry_count=3, id="t3"),
        ]})
        mock_task_cls.return_value = mock_task

        mock_budget = MagicMock()
        mock_budget.get_cost_status.side_effect = [
            (True, _mock_budget_status(1.0, 3.0)),  # p1
            (True, _mock_budget_status(2.0, 7.0)),  # p2
        ]
        mock_budget_cls.return_value = mock_budget

        exec_result = MagicMock(data=[
            {"project_id": "p1", "cost_usd": 10.0},
            {"project_id": "p2", "cost_usd": 25.0},
        ])
        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.not_.is_.return_value.execute.return_value = exec_result
        mock_supabase.return_value = mock_sb

        mock_monitor_cls.snapshot_alerts.return_value = []

        resp = client.get("/api/engine/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["projects"]) == 2

        p1 = next(p for p in data["projects"] if p["project_id"] == "p1")
        p2 = next(p for p in data["projects"] if p["project_id"] == "p2")

        assert p1["quality"]["total_done"] == 1
        assert p1["quality"]["first_pass_rate"] == 1.0
        assert p2["quality"]["total_done"] == 2
        assert p2["quality"]["first_pass_rate"] == 0.0
        assert p2["quality"]["avg_retries"] == 2.0

        # Global totals
        assert data["totals"]["quality"]["total_done"] == 3
        assert data["totals"]["cost"]["today_usd"] == 3.0

    @patch("src.server.api_routes.engine_api.get_supabase_client")
    @patch("src.server.api_routes.engine_api.CostBudgetService")
    @patch("src.server.api_routes.engine_api.ProjectService")
    @patch("src.server.api_routes.engine_api.TaskService")
    @patch("src.server.api_routes.engine_api.HealthMonitor")
    def test_metrics_budget_failure_graceful(
        self, mock_monitor_cls, mock_task_cls, mock_proj_cls, mock_budget_cls, mock_supabase, client
    ):
        """Budget service failure should not break the endpoint."""
        mock_proj = MagicMock()
        mock_proj.list_office_configs.return_value = (True, {"projects": [_make_project("p1")]})
        mock_proj_cls.return_value = mock_proj

        mock_task = MagicMock()
        mock_task.list_tasks.return_value = (True, {"tasks": [_make_task("p1", id="t1")]})
        mock_task_cls.return_value = mock_task

        mock_budget = MagicMock()
        mock_budget.get_cost_status.return_value = (False, {"error": "db down"})
        mock_budget_cls.return_value = mock_budget

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.not_.is_.return_value.execute.return_value = MagicMock(data=[])
        mock_supabase.return_value = mock_sb

        mock_monitor_cls.snapshot_alerts.return_value = []

        resp = client.get("/api/engine/metrics")
        assert resp.status_code == 200
        data = resp.json()
        # Cost should fall back to 0 for today/week
        assert data["projects"][0]["cost"]["today_usd"] == 0.0
        assert data["projects"][0]["cost"]["week_usd"] == 0.0

    @patch("src.server.api_routes.engine_api.get_supabase_client")
    @patch("src.server.api_routes.engine_api.CostBudgetService")
    @patch("src.server.api_routes.engine_api.ProjectService")
    @patch("src.server.api_routes.engine_api.TaskService")
    def test_metrics_500_on_unexpected_error(
        self, mock_task_cls, mock_proj_cls, mock_budget_cls, mock_supabase, client
    ):
        """Unexpected errors return 500 with error detail."""
        mock_proj = MagicMock()
        mock_proj.list_office_configs.side_effect = RuntimeError("db crashed")
        mock_proj_cls.return_value = mock_proj
        mock_task_cls.return_value = MagicMock()
        mock_budget_cls.return_value = MagicMock()
        mock_supabase.return_value = MagicMock()

        resp = client.get("/api/engine/metrics")
        assert resp.status_code == 500
        assert "error" in resp.json()["detail"]


class TestHealthMonitorSnapshotAlerts:
    """Tests for HealthMonitor.snapshot_alerts classmethod."""

    def test_snapshot_alerts_returns_alerts_on_low_first_pass_rate(self):
        from src.server.services.engine.health_monitor import HealthMonitor

        mock_task_service = MagicMock()
        # 10 done tasks, 3 first-pass (retry_count=0), 7 retried
        tasks = []
        for i in range(3):
            tasks.append(_make_task("p1", retry_count=0, id=f"fp-{i}"))
        for i in range(7):
            tasks.append(_make_task("p1", retry_count=2, id=f"retry-{i}"))
        mock_task_service.list_tasks.return_value = (True, {"tasks": tasks})

        alerts = HealthMonitor.snapshot_alerts(mock_task_service)
        metrics_in_alerts = [a["metric"] for a in alerts]
        assert "first_pass_rate" in metrics_in_alerts

    def test_snapshot_alerts_empty_when_healthy(self):
        from src.server.services.engine.health_monitor import HealthMonitor

        mock_task_service = MagicMock()
        # 5 done tasks all first-pass
        tasks = [_make_task("p1", retry_count=0, id=f"t-{i}") for i in range(5)]
        mock_task_service.list_tasks.return_value = (True, {"tasks": tasks})

        alerts = HealthMonitor.snapshot_alerts(mock_task_service)
        assert alerts == []

    def test_snapshot_alerts_no_tasks(self):
        from src.server.services.engine.health_monitor import HealthMonitor

        mock_task_service = MagicMock()
        mock_task_service.list_tasks.return_value = (True, {"tasks": []})

        alerts = HealthMonitor.snapshot_alerts(mock_task_service)
        assert alerts == []
