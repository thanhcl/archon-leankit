"""Tests for G-P1-04: Owner feedback capture loop.

Tests cover:
- TaskService.submit_feedback: happy path, not found, wrong status
- POST /api/tasks/{task_id}/feedback: 200, 400, 404
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides):
    base = {
        "id": "task-done-001",
        "project_id": "proj-001",
        "title": "Completed task",
        "status": "done",
        "assignee": "User",
        "task_order": 50,
        "priority": "medium",
        "feature": None,
        "owner": None,
        "source_app": None,
        "complexity": "simple",
        "retry_count": 0,
        "max_retries": 3,
        "state_changed_at": "2026-01-01T00:00:00",
        "blocked_by": [],
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "owner_rating": None,
        "owner_notes": None,
        "improvement_tags": [],
    }
    base.update(overrides)
    return base


def _mock_select(rows):
    """Build a chainable Supabase select mock that returns rows."""
    resp = MagicMock()
    resp.data = rows

    chain = MagicMock()
    chain.eq.return_value = chain
    chain.execute.return_value = resp
    return chain


def _mock_update(rows):
    """Build a chainable Supabase update mock that returns rows."""
    resp = MagicMock()
    resp.data = rows

    chain = MagicMock()
    chain.eq.return_value = chain
    chain.execute.return_value = resp
    return chain


def _build_client(select_rows, update_rows):
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table
    table.select.return_value = _mock_select(select_rows)
    table.update.return_value = _mock_update(update_rows)
    return client


def _make_service(select_rows, update_rows):
    """Create a TaskService with a fully mocked Supabase client."""
    from src.server.services.projects.task_service import TaskService

    client = _build_client(select_rows, update_rows)
    return TaskService(supabase_client=client)


# ---------------------------------------------------------------------------
# TaskService.submit_feedback unit tests
# ---------------------------------------------------------------------------


class TestSubmitFeedbackService:
    def test_happy_path_stores_all_fields(self):
        """Rating, notes, and tags are written to the DB and returned."""
        done_task = _make_task()
        updated_task = _make_task(
            owner_rating=5,
            owner_notes="Great job!",
            improvement_tags=["clarity", "speed"],
        )
        svc = _make_service(select_rows=[done_task], update_rows=[updated_task])

        ok, result = svc.submit_feedback(
            task_id="task-done-001",
            owner_rating=5,
            owner_notes="Great job!",
            improvement_tags=["clarity", "speed"],
        )

        assert ok is True
        assert result["owner_rating"] == 5
        assert result["owner_notes"] == "Great job!"
        assert result["improvement_tags"] == ["clarity", "speed"]

    def test_minimal_feedback_no_notes_no_tags(self):
        """Rating-only feedback (notes=None, tags=[]) is accepted."""
        done_task = _make_task()
        updated_task = _make_task(owner_rating=3, owner_notes=None, improvement_tags=[])
        svc = _make_service(select_rows=[done_task], update_rows=[updated_task])

        ok, result = svc.submit_feedback(
            task_id="task-done-001",
            owner_rating=3,
            owner_notes=None,
            improvement_tags=[],
        )

        assert ok is True
        assert result["owner_rating"] == 3
        assert result["owner_notes"] is None
        assert result["improvement_tags"] == []

    def test_task_not_found_returns_error(self):
        """Returns (False, error) when task ID does not exist."""
        svc = _make_service(select_rows=[], update_rows=[])

        ok, result = svc.submit_feedback(
            task_id="nonexistent-id",
            owner_rating=4,
            owner_notes=None,
            improvement_tags=[],
        )

        assert ok is False
        assert "not found" in result["error"].lower()

    @pytest.mark.parametrize("bad_status", ["draft", "todo", "doing", "review", "assigned", "executing"])
    def test_non_done_status_rejected(self, bad_status):
        """Feedback is refused for tasks that are not 'done'."""
        non_done_task = _make_task(status=bad_status)
        svc = _make_service(select_rows=[non_done_task], update_rows=[])

        ok, result = svc.submit_feedback(
            task_id="task-done-001",
            owner_rating=4,
            owner_notes=None,
            improvement_tags=[],
        )

        assert ok is False
        assert "done" in result["error"].lower()
        assert bad_status in result["error"]

    def test_db_update_returns_empty_data(self):
        """Returns (False, error) when DB update yields no rows."""
        done_task = _make_task()
        svc = _make_service(select_rows=[done_task], update_rows=[])

        ok, result = svc.submit_feedback(
            task_id="task-done-001",
            owner_rating=2,
            owner_notes=None,
            improvement_tags=[],
        )

        assert ok is False
        assert "failed" in result["error"].lower()


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/feedback endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    from src.server.api_routes.projects_api import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestFeedbackEndpoint:
    def test_200_stores_feedback(self, api_client):
        """Returns 200 with stored values for a valid done task."""
        from unittest.mock import patch

        with patch("src.server.api_routes.projects_api.TaskService") as MockSvc:
            instance = MockSvc.return_value
            instance.submit_feedback.return_value = (
                True,
                {
                    "owner_rating": 4,
                    "owner_notes": "Solid work",
                    "improvement_tags": ["tests"],
                },
            )

            response = api_client.post(
                "/api/tasks/task-done-001/feedback",
                json={"owner_rating": 4, "owner_notes": "Solid work", "improvement_tags": ["tests"]},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["owner_rating"] == 4
        assert body["owner_notes"] == "Solid work"
        assert body["improvement_tags"] == ["tests"]
        assert body["task_id"] == "task-done-001"

    def test_400_non_done_task(self, api_client):
        """Returns 400 when the task is not in 'done' status."""
        from unittest.mock import patch

        with patch("src.server.api_routes.projects_api.TaskService") as MockSvc:
            instance = MockSvc.return_value
            instance.submit_feedback.return_value = (
                False,
                {"error": "Feedback can only be submitted for tasks with status 'done', but task x has status 'doing'"},
            )

            response = api_client.post(
                "/api/tasks/task-x/feedback",
                json={"owner_rating": 3, "owner_notes": None, "improvement_tags": []},
            )

        assert response.status_code == 400

    def test_404_task_not_found(self, api_client):
        """Returns 404 when task does not exist."""
        from unittest.mock import patch

        with patch("src.server.api_routes.projects_api.TaskService") as MockSvc:
            instance = MockSvc.return_value
            instance.submit_feedback.return_value = (
                False,
                {"error": "Task with ID missing-id not found"},
            )

            response = api_client.post(
                "/api/tasks/missing-id/feedback",
                json={"owner_rating": 5, "owner_notes": None, "improvement_tags": []},
            )

        assert response.status_code == 404

    def test_422_invalid_rating_below_1(self, api_client):
        """Returns 422 when rating is below 1 (Pydantic validation)."""
        response = api_client.post(
            "/api/tasks/task-done-001/feedback",
            json={"owner_rating": 0, "owner_notes": None, "improvement_tags": []},
        )
        assert response.status_code == 422

    def test_422_invalid_rating_above_5(self, api_client):
        """Returns 422 when rating is above 5 (Pydantic validation)."""
        response = api_client.post(
            "/api/tasks/task-done-001/feedback",
            json={"owner_rating": 6, "owner_notes": None, "improvement_tags": []},
        )
        assert response.status_code == 422

    def test_422_missing_rating(self, api_client):
        """Returns 422 when required owner_rating field is absent."""
        response = api_client.post(
            "/api/tasks/task-done-001/feedback",
            json={"owner_notes": "Oops, forgot rating"},
        )
        assert response.status_code == 422
