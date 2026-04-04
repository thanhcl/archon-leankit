"""Tests for Engine API endpoints — status and concurrency config.

Uses a lightweight FastAPI app with only the engine router to avoid
importing the full server (which needs crawl4ai and other heavy deps).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def engine_client():
    """Minimal test client with only the engine router.

    Stubs out crawl4ai and other heavy deps that would be pulled in
    through api_routes.__init__.py import chain. Also breaks circular
    imports introduced by new api_routes that import engine services.
    """
    import sys

    # Block crawl4ai (heavy dependency)
    crawl4ai_mock = MagicMock()
    crawl4ai_submodules = [
        "crawl4ai", "crawl4ai.markdown_generation_strategy",
        "crawl4ai.content_filter_strategy", "crawl4ai.extraction_strategy",
        "crawl4ai.chunking_strategy", "crawl4ai.async_webcrawler",
    ]
    for mod in crawl4ai_submodules:
        if mod not in sys.modules:
            sys.modules[mod] = crawl4ai_mock

    # Break circular import: approval_request_service ↔ task_engine
    # This is triggered when __init__.py imports approval_requests_api
    circular_mocks = [
        "src.server.services.projects.approval_request_service",
        "src.server.api_routes.approval_requests_api",
        "src.server.api_routes.bootstrap_plans_api",
        "src.server.api_routes.channel_health_api",
        "src.server.api_routes.execution_runs_api",
        "src.server.api_routes.external_requests_api",
        "src.server.api_routes.openclaw_api",
        "src.server.api_routes.service_health_api",
        "src.server.api_routes.telegram_api",
    ]
    injected: list[str] = []
    for mod in circular_mocks:
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()
            injected.append(mod)

    from src.server.api_routes.engine_api import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Clean up injected mocks so they don't bleed into other tests
    for mod in injected:
        sys.modules.pop(mod, None)

    return client


class TestEngineStatus:
    def test_status_returns_slot_info(self, engine_client):
        mock_task_svc = MagicMock()
        mock_task_svc.list_tasks.return_value = (True, {
            "tasks": [
                {"id": "t1", "project_id": "proj-1"},
                {"id": "t2", "project_id": "proj-1"},
                {"id": "t3", "project_id": "proj-2"},
            ]
        })

        mock_project_svc = MagicMock()
        mock_project_svc.list_office_configs.return_value = (True, {
            "projects": [
                {"id": "proj-1", "title": "Project 1", "office_settings": {"max_concurrent": 5}},
                {"id": "proj-2", "title": "Project 2", "office_settings": {}},
            ]
        })

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc), \
             patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.get("/api/engine/status")

        assert resp.status_code == 200
        data = resp.json()

        assert data["global"]["total_executing"] == 3
        assert data["global"]["max_parallel_global"] == 10

        projects = {p["project_id"]: p for p in data["projects"]}
        assert projects["proj-1"]["slots_used"] == 2
        assert projects["proj-1"]["max_concurrent"] == 5
        assert projects["proj-1"]["slots_available"] == 3
        assert projects["proj-2"]["slots_used"] == 1

    def test_status_no_executing_tasks(self, engine_client):
        mock_task_svc = MagicMock()
        mock_task_svc.list_tasks.return_value = (True, {"tasks": []})

        mock_project_svc = MagicMock()
        mock_project_svc.list_office_configs.return_value = (True, {
            "projects": [
                {"id": "proj-1", "title": "Project 1", "office_settings": {}},
            ]
        })

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc), \
             patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.get("/api/engine/status")

        data = resp.json()
        assert data["global"]["total_executing"] == 0
        assert data["projects"][0]["slots_used"] == 0

    def test_status_handles_task_fetch_failure(self, engine_client):
        mock_task_svc = MagicMock()
        mock_task_svc.list_tasks.return_value = (False, {"error": "DB down"})

        mock_project_svc = MagicMock()
        mock_project_svc.list_office_configs.return_value = (True, {
            "projects": [
                {"id": "proj-1", "title": "Project 1", "office_settings": {}},
            ]
        })

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc), \
             patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.get("/api/engine/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["global"]["total_executing"] == 0


class TestReviewConfig:
    def test_get_defaults(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.get("/api/engine/review-config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["review_mode"] == "self-review"
        assert data["independent_review_enabled"] is True
        assert data["confidence_approve_threshold"] == 0.8

    def test_get_parses_json_string_payload(self, engine_client):
        stored = '{"review_mode":"api","provider":"anthropic","model":"claude-opus-4-6"}'
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=stored)
            resp = engine_client.get("/api/engine/review-config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["review_mode"] == "api"
        assert data["provider"] == "anthropic"
        assert data["model"] == "claude-opus-4-6"

    def test_update_independent_review_toggle(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            mock_cred.set_credential = AsyncMock()
            resp = engine_client.put(
                "/api/engine/review-config",
                json={"independent_review_enabled": False},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["independent_review_enabled"] is False

    def test_update_mode_to_multi_perspective(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            mock_cred.set_credential = AsyncMock()
            resp = engine_client.put(
                "/api/engine/review-config",
                json={"review_mode": "multi-perspective"},
            )

        assert resp.status_code == 200
        assert resp.json()["config"]["review_mode"] == "multi-perspective"

    def test_update_rejects_invalid_mode(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.put(
                "/api/engine/review-config",
                json={"review_mode": "invalid-mode"},
            )

        assert resp.status_code == 400

    def test_update_empty_body(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.put(
                "/api/engine/review-config",
                json={},
            )

        assert resp.status_code == 400


class TestConcurrencyConfig:
    def test_get_defaults(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.get("/api/engine/concurrency-config")

        assert resp.status_code == 200
        data = resp.json()
        assert "max_parallel_default" in data
        assert "max_parallel_global" in data

    def test_get_parses_json_string_payload(self, engine_client):
        stored = '{"max_parallel_default":5,"max_parallel_global":12}'
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=stored)
            resp = engine_client.get("/api/engine/concurrency-config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["max_parallel_default"] == 5
        assert data["max_parallel_global"] == 12

    def test_update_valid(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            mock_cred.set_credential = AsyncMock()
            resp = engine_client.put(
                "/api/engine/concurrency-config",
                json={"max_parallel_default": 5, "max_parallel_global": 15},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["max_parallel_default"] == 5
        assert data["config"]["max_parallel_global"] == 15

    def test_update_rejects_invalid_range(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.put(
                "/api/engine/concurrency-config",
                json={"max_parallel_default": 0},
            )

        assert resp.status_code == 400

    def test_update_rejects_too_high(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.put(
                "/api/engine/concurrency-config",
                json={"max_parallel_global": 100},
            )

        assert resp.status_code == 400

    def test_update_empty_body(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.put(
                "/api/engine/concurrency-config",
                json={},
            )

        assert resp.status_code == 400


class TestRunnerCapabilities:
    def test_returns_capability_matrix(self, engine_client):
        resp = engine_client.get("/api/engine/runner-capabilities")

        assert resp.status_code == 200
        data = resp.json()
        assert data["default_runner"] == "claude-code-cli"
        runner_keys = {runner["runner_key"] for runner in data["runners"]}
        assert "claude-code-cli" in runner_keys
        assert "codex-cli" in runner_keys

    def test_hides_claude_and_switches_default_when_claude_disabled(self, engine_client):
        with patch.dict("os.environ", {"LEANKIT_ENGINE_DISABLE_CLAUDE_CODE": "true"}, clear=False):
            resp = engine_client.get("/api/engine/runner-capabilities")

        assert resp.status_code == 200
        data = resp.json()
        assert data["default_runner"] == "codex-cli"
        runner_keys = {runner["runner_key"] for runner in data["runners"]}
        assert "claude-code-cli" not in runner_keys
        assert "codex-cli" in runner_keys


class TestEngineHealth:
    def _make_mocks(self, executing_tasks=None, assigned_tasks=None, projects=None):
        mock_task_svc = MagicMock()

        def list_tasks_side_effect(status=None, **kwargs):
            if status == "executing":
                return (True, {"tasks": executing_tasks or []})
            if status == "assigned":
                return (True, {"tasks": assigned_tasks or []})
            return (True, {"tasks": []})

        mock_task_svc.list_tasks.side_effect = list_tasks_side_effect

        mock_project_svc = MagicMock()
        mock_project_svc.list_office_configs.return_value = (True, {
            "projects": projects or []
        })

        mock_budget_svc = MagicMock()
        mock_budget_svc.get_cost_status.return_value = (True, {
            "status": "ok",
            "today": {"cost_usd": 5.0, "budget_usd": 100.0, "usage_pct": 0.05},
            "weekly": {"total_cost_usd": 20.0, "budget_usd": 500.0, "usage_pct": 0.04},
        })

        mock_run_svc = MagicMock()
        mock_run_svc.list_runs.return_value = (True, {"runs": []})

        return mock_task_svc, mock_project_svc, mock_budget_svc, mock_run_svc

    def test_health_idle_when_no_tasks(self, engine_client):
        mock_task_svc, mock_project_svc, mock_budget_svc, mock_run_svc = self._make_mocks(
            projects=[{"id": "proj-1", "title": "P1", "office_settings": {"max_concurrent": 3}}]
        )

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc), \
             patch("src.server.api_routes.engine_api.CostBudgetService", return_value=mock_budget_svc), \
             patch("src.server.api_routes.engine_api.ExecutionRunService", return_value=mock_run_svc), \
             patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.get("/api/engine/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"
        assert data["active_runs"] == 0
        assert data["queue_depth"] == 0

    def test_health_healthy_with_active_and_queued(self, engine_client):
        mock_task_svc, mock_project_svc, mock_budget_svc, mock_run_svc = self._make_mocks(
            executing_tasks=[{"id": "t1", "project_id": "proj-1"}],
            assigned_tasks=[{"id": "t2", "project_id": "proj-1"}, {"id": "t3", "project_id": "proj-1"}],
            projects=[{"id": "proj-1", "title": "P1", "office_settings": {"max_concurrent": 3}}],
        )

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc), \
             patch("src.server.api_routes.engine_api.CostBudgetService", return_value=mock_budget_svc), \
             patch("src.server.api_routes.engine_api.ExecutionRunService", return_value=mock_run_svc), \
             patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.get("/api/engine/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["active_runs"] == 1
        assert data["queue_depth"] == 2
        assert data["global"]["slots_available"] == 9
        proj = data["projects"][0]
        assert proj["capacity"]["used"] == 1
        assert proj["capacity"]["max"] == 3
        assert proj["capacity"]["available"] == 2
        assert proj["budget"]["status"] == "ok"

    def test_health_degraded_with_orphaned_runs(self, engine_client):
        mock_task_svc, mock_project_svc, mock_budget_svc, mock_run_svc = self._make_mocks()

        # Return a stale running execution run (started 3 hours ago)
        stale_started = "2020-01-01T00:00:00Z"
        mock_run_svc.list_runs.return_value = (True, {
            "runs": [{"id": "run-orphan-1", "started_at": stale_started}]
        })

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc), \
             patch("src.server.api_routes.engine_api.CostBudgetService", return_value=mock_budget_svc), \
             patch("src.server.api_routes.engine_api.ExecutionRunService", return_value=mock_run_svc), \
             patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.get("/api/engine/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["pid_watchdog"]["status"] == "orphaned_detected"
        assert "run-orphan-1" in data["pid_watchdog"]["orphaned_run_ids"]

    def test_health_pid_watchdog_ok_with_fresh_run(self, engine_client):
        mock_task_svc, mock_project_svc, mock_budget_svc, mock_run_svc = self._make_mocks(
            executing_tasks=[{"id": "t1", "project_id": "proj-1"}],
        )

        # Fresh run started just now
        from datetime import datetime
        fresh_started = datetime.now(UTC).isoformat()
        mock_run_svc.list_runs.return_value = (True, {
            "runs": [{"id": "run-fresh-1", "started_at": fresh_started}]
        })

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc), \
             patch("src.server.api_routes.engine_api.CostBudgetService", return_value=mock_budget_svc), \
             patch("src.server.api_routes.engine_api.ExecutionRunService", return_value=mock_run_svc), \
             patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.get("/api/engine/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["pid_watchdog"]["status"] == "ok"
        assert data["pid_watchdog"]["orphaned_run_ids"] == []

    def test_health_prefers_heartbeat_over_started_at_for_orphan_detection(self, engine_client):
        mock_task_svc, mock_project_svc, mock_budget_svc, mock_run_svc = self._make_mocks(
            executing_tasks=[{"id": "t1", "project_id": "proj-1"}],
        )
        fresh_heartbeat = datetime.now(UTC).isoformat()
        mock_run_svc.list_runs.return_value = (
            True,
            {
                "runs": [
                    {
                        "id": "run-fresh-heartbeat",
                        "started_at": "2020-01-01T00:00:00Z",
                        "heartbeat_at": fresh_heartbeat,
                    }
                ]
            },
        )

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc), \
             patch("src.server.api_routes.engine_api.CostBudgetService", return_value=mock_budget_svc), \
             patch("src.server.api_routes.engine_api.ExecutionRunService", return_value=mock_run_svc), \
             patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            resp = engine_client.get("/api/engine/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["pid_watchdog"]["status"] == "ok"
        assert data["pid_watchdog"]["orphaned_run_ids"] == []


class TestEngineQueue:
    def test_queue_returns_grouped_tasks(self, engine_client):
        mock_task_svc = MagicMock()
        mock_task_svc.list_tasks.return_value = (True, {
            "tasks": [
                {"id": "t1", "project_id": "proj-1", "title": "Fix bug", "priority": "high",
                 "status": "assigned", "task_order": 10, "blocked_by": None, "updated_at": "2026-01-01T00:00:00"},
                {"id": "t2", "project_id": "proj-1", "title": "Add feature", "priority": "medium",
                 "status": "assigned", "task_order": 5, "blocked_by": None, "updated_at": "2026-01-01T01:00:00"},
                {"id": "t3", "project_id": "proj-2", "title": "Refactor", "priority": "low",
                 "status": "assigned", "task_order": 3, "blocked_by": None, "updated_at": "2026-01-01T02:00:00"},
            ]
        })

        mock_project_svc = MagicMock()
        mock_project_svc.list_office_configs.return_value = (True, {
            "projects": [
                {"id": "proj-1", "title": "Project One", "office_settings": {}},
                {"id": "proj-2", "title": "Project Two", "office_settings": {}},
            ]
        })

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc):
            resp = engine_client.get("/api/engine/queue")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_queued"] == 3

        project_map = {p["project_id"]: p for p in data["projects"]}
        assert project_map["proj-1"]["queue_depth"] == 2
        assert project_map["proj-2"]["queue_depth"] == 1
        assert project_map["proj-1"]["project_name"] == "Project One"

        # proj-1 tasks should be sorted: high before medium
        p1_tasks = project_map["proj-1"]["tasks"]
        assert p1_tasks[0]["priority"] == "high"
        assert p1_tasks[1]["priority"] == "medium"

    def test_queue_empty(self, engine_client):
        mock_task_svc = MagicMock()
        mock_task_svc.list_tasks.return_value = (True, {"tasks": []})

        mock_project_svc = MagicMock()
        mock_project_svc.list_office_configs.return_value = (True, {"projects": []})

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc):
            resp = engine_client.get("/api/engine/queue")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_queued"] == 0
        assert data["projects"] == []

    def test_queue_blocked_task_flagged(self, engine_client):
        mock_task_svc = MagicMock()
        mock_task_svc.list_tasks.return_value = (True, {
            "tasks": [
                {"id": "t1", "project_id": "proj-1", "title": "Blocked task", "priority": "high",
                 "status": "assigned", "task_order": 10, "blocked_by": "t0", "updated_at": "2026-01-01T00:00:00"},
            ]
        })

        mock_project_svc = MagicMock()
        mock_project_svc.list_office_configs.return_value = (True, {"projects": []})

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc):
            resp = engine_client.get("/api/engine/queue")

        data = resp.json()
        task_entry = data["projects"][0]["tasks"][0]
        assert task_entry["is_blocked"] is True
        assert task_entry["blocked_by"] == "t0"

    def test_queue_projects_sorted_by_depth(self, engine_client):
        mock_task_svc = MagicMock()
        mock_task_svc.list_tasks.return_value = (True, {
            "tasks": [
                {"id": "t1", "project_id": "proj-a", "title": "T1", "priority": "medium",
                 "status": "assigned", "task_order": 1, "blocked_by": None, "updated_at": None},
                {"id": "t2", "project_id": "proj-b", "title": "T2", "priority": "medium",
                 "status": "assigned", "task_order": 1, "blocked_by": None, "updated_at": None},
                {"id": "t3", "project_id": "proj-b", "title": "T3", "priority": "medium",
                 "status": "assigned", "task_order": 2, "blocked_by": None, "updated_at": None},
            ]
        })

        mock_project_svc = MagicMock()
        mock_project_svc.list_office_configs.return_value = (True, {"projects": []})

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc):
            resp = engine_client.get("/api/engine/queue")

        data = resp.json()
        # proj-b (2 tasks) should come before proj-a (1 task)
        assert data["projects"][0]["project_id"] == "proj-b"
        assert data["projects"][1]["project_id"] == "proj-a"


class TestEngineHeartbeat:
    def test_heartbeat_registers_started_at(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            mock_cred.set_credential = AsyncMock()
            resp = engine_client.post(
                "/api/engine/heartbeat",
                json={"started_at": "2026-03-25T10:00:00+00:00"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["heartbeat"]["started_at"] == "2026-03-25T10:00:00+00:00"

    def test_heartbeat_updates_last_poll_at(self, engine_client):
        existing = {"started_at": "2026-03-25T10:00:00+00:00"}
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=existing)
            mock_cred.set_credential = AsyncMock()
            resp = engine_client.post(
                "/api/engine/heartbeat",
                json={"last_poll_at": "2026-03-25T11:00:00+00:00"},
            )

        assert resp.status_code == 200
        data = resp.json()
        # Preserves existing started_at and adds last_poll_at
        assert data["heartbeat"]["started_at"] == "2026-03-25T10:00:00+00:00"
        assert data["heartbeat"]["last_poll_at"] == "2026-03-25T11:00:00+00:00"

    def test_heartbeat_empty_body_is_ok(self, engine_client):
        with patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(return_value=None)
            mock_cred.set_credential = AsyncMock()
            resp = engine_client.post("/api/engine/heartbeat", json={})

        assert resp.status_code == 200


class TestEngineHealthUptime:
    def _make_mocks(self, executing_tasks=None, assigned_tasks=None, projects=None, heartbeat=None):
        mock_task_svc = MagicMock()

        def list_tasks_side_effect(status=None, **kwargs):
            if status == "executing":
                return (True, {"tasks": executing_tasks or []})
            if status == "assigned":
                return (True, {"tasks": assigned_tasks or []})
            return (True, {"tasks": []})

        mock_task_svc.list_tasks.side_effect = list_tasks_side_effect

        mock_project_svc = MagicMock()
        mock_project_svc.list_office_configs.return_value = (True, {"projects": projects or []})

        mock_budget_svc = MagicMock()
        mock_budget_svc.get_cost_status.return_value = (True, {
            "status": "ok",
            "today": {"cost_usd": 0, "budget_usd": 100.0, "usage_pct": 0},
            "weekly": {"total_cost_usd": 0, "budget_usd": 500.0, "usage_pct": 0},
        })

        mock_run_svc = MagicMock()
        mock_run_svc.list_runs.return_value = (True, {"runs": []})

        def get_credential_side_effect(key):
            if key == "ENGINE_HEARTBEAT":
                return heartbeat
            return None

        return mock_task_svc, mock_project_svc, mock_budget_svc, mock_run_svc, get_credential_side_effect

    def test_health_includes_uptime_when_heartbeat_set(self, engine_client):
        from datetime import datetime, timedelta

        started = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        heartbeat = {"started_at": started, "last_poll_at": started}

        mock_task_svc, mock_project_svc, mock_budget_svc, mock_run_svc, get_cred = self._make_mocks(
            heartbeat=heartbeat
        )

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc), \
             patch("src.server.api_routes.engine_api.CostBudgetService", return_value=mock_budget_svc), \
             patch("src.server.api_routes.engine_api.ExecutionRunService", return_value=mock_run_svc), \
             patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(side_effect=get_cred)
            resp = engine_client.get("/api/engine/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["started_at"] == started
        assert data["last_poll_at"] == started
        assert data["uptime_seconds"] is not None
        assert data["uptime_seconds"] > 3500  # ~1 hour

    def test_health_uptime_null_when_no_heartbeat(self, engine_client):
        mock_task_svc, mock_project_svc, mock_budget_svc, mock_run_svc, get_cred = self._make_mocks(
            heartbeat=None
        )

        with patch("src.server.api_routes.engine_api.TaskService", return_value=mock_task_svc), \
             patch("src.server.api_routes.engine_api.ProjectService", return_value=mock_project_svc), \
             patch("src.server.api_routes.engine_api.CostBudgetService", return_value=mock_budget_svc), \
             patch("src.server.api_routes.engine_api.ExecutionRunService", return_value=mock_run_svc), \
             patch("src.server.api_routes.engine_api.credential_service") as mock_cred:
            mock_cred.get_credential = AsyncMock(side_effect=get_cred)
            resp = engine_client.get("/api/engine/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["started_at"] is None
        assert data["uptime_seconds"] is None
        assert data["last_poll_at"] is None
