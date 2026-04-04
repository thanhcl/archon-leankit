"""Focused tests for task creation behavior in projects API routes."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.server.api_routes.projects_api import router


@pytest.fixture
def test_client():
    """Create a lightweight app with the projects router mounted."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _build_supabase_query_mock(task_rows):
    """Create a fluent mock for the duplicate lookup query."""
    mock_supabase = MagicMock()
    mock_query = MagicMock()

    mock_supabase.table.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.order.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.execute.return_value = SimpleNamespace(data=task_rows)

    return mock_supabase


class TestCreateTaskDeduplication:
    """Tests for duplicate prevention on POST /api/tasks."""

    def test_create_task_returns_recent_existing_task(self, test_client):
        """A recent matching task is returned instead of creating another row."""
        existing_task = {
            "id": "task-existing",
            "project_id": "proj-1",
            "title": "Investigate duplicate creates",
            "description": "Existing draft task",
            "status": "draft",
            "assignee": "User",
            "task_order": 2,
            "priority": "medium",
            "complexity": "simple",
            "blocked_by": [],
            "allowed_paths": [],
            "forbidden_paths": [],
            "repo_guidance_packs": [],
            "reviewed_by": [],
            "created_at": datetime.now(UTC).isoformat(),
        }
        mock_supabase = _build_supabase_query_mock([existing_task])

        with patch("src.server.api_routes.projects_api.get_supabase_client", return_value=mock_supabase), patch(
            "src.server.api_routes.projects_api.TaskService"
        ) as mock_task_service_class:
            mock_task_service = MagicMock()
            mock_task_service.create_task = AsyncMock()
            mock_task_service_class.return_value = mock_task_service

            response = test_client.post(
                "/api/tasks",
                json={
                    "project_id": "proj-1",
                    "title": "Investigate duplicate creates",
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "message": "Task created successfully",
            "task": {
                "id": "task-existing",
                "project_id": "proj-1",
                "title": "Investigate duplicate creates",
                "description": "Existing draft task",
                "status": "draft",
                "assignee": "User",
                "task_order": 2,
                "priority": "medium",
                "complexity": "simple",
                "owner": None,
                "source_app": None,
                "blocked_by": [],
                "allowed_paths": [],
                "forbidden_paths": [],
                "repo_guidance_packs": [],
                "created_by": None,
                "created_from": None,
                "executed_by": None,
                "reviewed_by": [],
                "created_at": existing_task["created_at"],
            },
            "deduplicated": True,
        }
        assert mock_task_service.create_task.await_count == 0

    def test_create_task_ignores_stale_match_and_creates_new_task(self, test_client):
        """A stale matching task should not block a new create request."""
        stale_task = {
            "id": "task-stale",
            "project_id": "proj-1",
            "title": "Investigate duplicate creates",
            "description": "Old draft task",
            "status": "draft",
            "created_at": (datetime.now(UTC) - timedelta(seconds=61)).isoformat(),
        }
        mock_supabase = _build_supabase_query_mock([stale_task])
        created_task = {
            "id": "task-new",
            "project_id": "proj-1",
            "title": "Investigate duplicate creates",
            "description": "Fresh draft task",
            "status": "draft",
            "assignee": "User",
            "task_order": 0,
            "priority": "medium",
            "complexity": "simple",
            "owner": None,
            "source_app": None,
            "blocked_by": [],
            "allowed_paths": [],
            "forbidden_paths": [],
            "repo_guidance_packs": [],
            "created_by": None,
            "created_from": None,
            "executed_by": None,
            "reviewed_by": [],
            "created_at": datetime.now(UTC).isoformat(),
        }

        with patch("src.server.api_routes.projects_api.get_supabase_client", return_value=mock_supabase), patch(
            "src.server.api_routes.projects_api.TaskService"
        ) as mock_task_service_class:
            mock_task_service = MagicMock()
            mock_task_service.create_task = AsyncMock(return_value=(True, {"task": created_task}))
            mock_task_service_class.return_value = mock_task_service

            response = test_client.post(
                "/api/tasks",
                json={
                    "project_id": "proj-1",
                    "title": "Investigate duplicate creates",
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "message": "Task created successfully",
            "task": created_task,
        }
        mock_task_service.create_task.assert_awaited_once()


class TestImplementationCockpit:
    """Tests for GET /api/projects/{project_id}/implementation-cockpit."""

    def test_cockpit_returns_all_sections(self, test_client):
        """Successful cockpit response contains all required sections."""
        cockpit_data = {
            "project_id": "proj-1",
            "header": {"plan_title": "Plan A", "completion_percent": 50.0, "current_phase": None, "health": "healthy"},
            "workstreams": [{"name": "auth", "done": 1, "total": 2}],
            "phases": [],
            "quality": {"first_pass_rate": 1.0, "avg_retries": 0.0, "done_count": 1, "total_runs": 1},
            "issue_ledger": [],
            "throughput": {"items_done": 1, "total_tasks": 2, "total_cost_usd": 0.05, "by_workstream": []},
            "recent_alerts": [],
        }

        with patch("src.server.api_routes.projects_api.CockpitService") as mock_cls:
            mock_service = MagicMock()
            mock_service.get_cockpit.return_value = (True, cockpit_data)
            mock_cls.return_value = mock_service

            response = test_client.get("/api/projects/proj-1/implementation-cockpit")

        assert response.status_code == 200
        body = response.json()
        for section in ("header", "workstreams", "phases", "quality", "issue_ledger", "throughput", "recent_alerts"):
            assert section in body, f"Missing section: {section}"
        assert body["project_id"] == "proj-1"
        assert body["header"]["plan_title"] == "Plan A"

    def test_cockpit_project_not_found_returns_404(self, test_client):
        """Missing project returns 404."""
        with patch("src.server.api_routes.projects_api.CockpitService") as mock_cls:
            mock_service = MagicMock()
            mock_service.get_cockpit.return_value = (False, {"error": "Project proj-missing not found"})
            mock_cls.return_value = mock_service

            response = test_client.get("/api/projects/proj-missing/implementation-cockpit")

        assert response.status_code == 404

    def test_cockpit_service_error_returns_500(self, test_client):
        """Internal service error returns 500."""
        with patch("src.server.api_routes.projects_api.CockpitService") as mock_cls:
            mock_service = MagicMock()
            mock_service.get_cockpit.return_value = (False, {"error": "Database connection failed"})
            mock_cls.return_value = mock_service

            response = test_client.get("/api/projects/proj-1/implementation-cockpit")

        assert response.status_code == 500

    def test_cockpit_exception_returns_500(self, test_client):
        """Unhandled exception returns 500."""
        with patch("src.server.api_routes.projects_api.CockpitService") as mock_cls:
            mock_service = MagicMock()
            mock_service.get_cockpit.side_effect = RuntimeError("unexpected")
            mock_cls.return_value = mock_service

            response = test_client.get("/api/projects/proj-1/implementation-cockpit")

        assert response.status_code == 500

    def test_cockpit_etag_304(self, test_client):
        """ETag match returns 304 Not Modified."""
        cockpit_data = {
            "project_id": "proj-1",
            "header": {"plan_title": "P", "completion_percent": 0, "current_phase": None, "health": "healthy"},
            "workstreams": [],
            "phases": [],
            "quality": {"first_pass_rate": 0, "avg_retries": 0, "done_count": 0, "total_runs": 0},
            "issue_ledger": [],
            "throughput": {"items_done": 0, "total_tasks": 0, "total_cost_usd": 0, "by_workstream": []},
            "recent_alerts": [],
        }

        with patch("src.server.api_routes.projects_api.CockpitService") as mock_cls:
            mock_service = MagicMock()
            mock_service.get_cockpit.return_value = (True, cockpit_data)
            mock_cls.return_value = mock_service

            # First request to get ETag
            resp1 = test_client.get("/api/projects/proj-1/implementation-cockpit")
            assert resp1.status_code == 200
            etag = resp1.headers.get("ETag")
            assert etag is not None

            # Second request with If-None-Match
            resp2 = test_client.get(
                "/api/projects/proj-1/implementation-cockpit",
                headers={"If-None-Match": etag},
            )
            assert resp2.status_code == 304


class TestUpdateTaskStatusBlocked:
    """Tests ensuring PUT /api/tasks/{task_id} rejects status field."""

    def test_put_with_status_field_returns_422(self, test_client):
        """PUT /api/tasks/{task_id} with status field must return 422."""
        response = test_client.put(
            "/api/tasks/task-123",
            json={"status": "done"},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "transition" in detail

    def test_put_with_status_and_other_fields_returns_422(self, test_client):
        """422 is returned even when status is combined with other valid fields."""
        response = test_client.put(
            "/api/tasks/task-123",
            json={"title": "New title", "status": "executing"},
        )
        assert response.status_code == 422

    def test_put_without_status_field_succeeds(self, test_client):
        """PUT /api/tasks/{task_id} without status field proceeds normally."""
        updated_task = {
            "id": "task-123",
            "project_id": "proj-1",
            "title": "Updated title",
            "description": "",
            "status": "draft",
            "assignee": "User",
            "task_order": 0,
            "priority": "medium",
            "complexity": "simple",
            "owner": None,
            "source_app": None,
            "blocked_by": [],
            "allowed_paths": [],
            "forbidden_paths": [],
            "repo_guidance_packs": [],
            "created_by": None,
            "created_from": None,
            "executed_by": None,
            "reviewed_by": [],
            "created_at": "2026-01-01T00:00:00+00:00",
        }

        with patch(
            "src.server.api_routes.projects_api.TaskService"
        ) as mock_task_service_class:
            mock_task_service = MagicMock()
            mock_task_service.update_task = AsyncMock(return_value=(True, {"task": updated_task}))
            mock_task_service_class.return_value = mock_task_service

            response = test_client.put(
                "/api/tasks/task-123",
                json={"title": "Updated title"},
            )

        assert response.status_code == 200
        assert response.json()["task"]["title"] == "Updated title"
        mock_task_service.update_task.assert_awaited_once()
