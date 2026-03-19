"""Tests for Engine API endpoints — status and concurrency config.

Uses a lightweight FastAPI app with only the engine router to avoid
importing the full server (which needs crawl4ai and other heavy deps).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def engine_client():
    """Minimal test client with only the engine router.

    Stubs out crawl4ai and other heavy deps that would be pulled in
    through api_routes.__init__.py import chain.
    """
    import sys

    # Create a comprehensive mock for crawl4ai to prevent import failures
    crawl4ai_mock = MagicMock()
    crawl4ai_submodules = [
        "crawl4ai", "crawl4ai.markdown_generation_strategy",
        "crawl4ai.content_filter_strategy", "crawl4ai.extraction_strategy",
        "crawl4ai.chunking_strategy", "crawl4ai.async_webcrawler",
    ]
    for mod in crawl4ai_submodules:
        if mod not in sys.modules:
            sys.modules[mod] = crawl4ai_mock

    from src.server.api_routes.engine_api import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


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
