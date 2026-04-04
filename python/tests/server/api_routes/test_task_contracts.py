"""Tests for task contract CRUD endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.server.api_routes.projects_api import router


@pytest.fixture
def test_client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


SAMPLE_CONTRACT = {
    "id": "contract-1",
    "task_id": "task-1",
    "version": 1,
    "objective": "Implement auth module",
    "in_scope_paths": ["src/auth/"],
    "acceptance_criteria": [
        {"name": "login", "description": "User can log in", "threshold": "100% pass"}
    ],
    "evidence_requirements": ["unit tests pass", "screenshot of login flow"],
    "negotiated_by": "owner",
    "locked_at": None,
    # Provenance fields
    "source_stage": "planning",
    "source_type": "owner",
    "negotiation_status": "draft",
    "locked_by": None,
    "supersedes_contract_id": None,
    "created_at": "2026-03-30T10:00:00",
    "updated_at": "2026-03-30T10:00:00",
}


class TestCreateTaskContract:
    """Tests for POST /api/tasks/{task_id}/contracts."""

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_create_contract_success(self, mock_ts_class, test_client):
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.create_contract.return_value = (True, {"contract": SAMPLE_CONTRACT})

        resp = test_client.post(
            "/api/tasks/task-1/contracts",
            json={
                "objective": "Implement auth module",
                "in_scope_paths": ["src/auth/"],
                "acceptance_criteria": [
                    {"name": "login", "description": "User can log in", "threshold": "100% pass"}
                ],
                "evidence_requirements": ["unit tests pass"],
                "negotiated_by": "owner",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "contract-1"
        assert data["objective"] == "Implement auth module"
        assert len(data["acceptance_criteria"]) == 1

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_create_contract_task_not_found(self, mock_ts_class, test_client):
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.create_contract.return_value = (
            False,
            {"error": "Task with ID task-missing not found"},
        )

        resp = test_client.post(
            "/api/tasks/task-missing/contracts",
            json={"objective": "Something"},
        )

        assert resp.status_code == 404


class TestListTaskContracts:
    """Tests for GET /api/tasks/{task_id}/contracts."""

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_list_contracts_success(self, mock_ts_class, test_client):
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.list_contracts.return_value = (
            True,
            {"contracts": [SAMPLE_CONTRACT], "total_count": 1},
        )

        resp = test_client.get("/api/tasks/task-1/contracts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 1
        assert len(data["contracts"]) == 1


class TestGetContract:
    """Tests for GET /api/contracts/{contract_id}."""

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_get_contract_success(self, mock_ts_class, test_client):
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.get_contract.return_value = (True, {"contract": SAMPLE_CONTRACT})

        resp = test_client.get("/api/contracts/contract-1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "contract-1"

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_get_contract_not_found(self, mock_ts_class, test_client):
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.get_contract.return_value = (
            False,
            {"error": "Contract with ID missing not found"},
        )

        resp = test_client.get("/api/contracts/missing")
        assert resp.status_code == 404


class TestUpdateContract:
    """Tests for PUT /api/contracts/{contract_id}."""

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_update_contract_success(self, mock_ts_class, test_client):
        updated = {**SAMPLE_CONTRACT, "objective": "Updated objective"}
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.update_contract.return_value = (True, {"contract": updated})

        resp = test_client.put(
            "/api/contracts/contract-1",
            json={"objective": "Updated objective"},
        )
        assert resp.status_code == 200
        assert resp.json()["objective"] == "Updated objective"

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_update_locked_contract_returns_409(self, mock_ts_class, test_client):
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.update_contract.return_value = (
            False,
            {"error": "Cannot update a locked contract. Create a new revision instead."},
        )

        resp = test_client.put(
            "/api/contracts/contract-1",
            json={"objective": "Nope"},
        )
        assert resp.status_code == 409


class TestLockContract:
    """Tests for POST /api/contracts/{contract_id}/lock."""

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_lock_contract_success(self, mock_ts_class, test_client):
        locked = {**SAMPLE_CONTRACT, "locked_at": "2026-03-30T12:00:00"}
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.lock_contract.return_value = (True, {"contract": locked})

        resp = test_client.post("/api/contracts/contract-1/lock")
        assert resp.status_code == 200
        assert resp.json()["locked_at"] is not None

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_lock_already_locked_returns_409(self, mock_ts_class, test_client):
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.lock_contract.return_value = (
            False,
            {"error": "Contract is already locked"},
        )

        resp = test_client.post("/api/contracts/contract-1/lock")
        assert resp.status_code == 409


class TestDeleteContract:
    """Tests for DELETE /api/contracts/{contract_id}."""

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_delete_contract_success(self, mock_ts_class, test_client):
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.delete_contract.return_value = (
            True,
            {"message": "Contract contract-1 deleted"},
        )

        resp = test_client.delete("/api/contracts/contract-1")
        assert resp.status_code == 200

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_delete_locked_contract_returns_409(self, mock_ts_class, test_client):
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.delete_contract.return_value = (
            False,
            {"error": "Cannot delete a locked contract"},
        )

        resp = test_client.delete("/api/contracts/contract-1")
        assert resp.status_code == 409


class TestContractProvenanceFields:
    """Tests that provenance fields are stored and returned correctly."""

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_create_contract_with_provenance(self, mock_ts_class, test_client):
        """Creating a contract with source_stage, source_type, and negotiation_status returns them."""
        contract_with_provenance = {
            **SAMPLE_CONTRACT,
            "source_stage": "negotiation",
            "source_type": "architect",
            "negotiation_status": "negotiated",
            "supersedes_contract_id": "contract-0",
        }
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.create_contract.return_value = (True, {"contract": contract_with_provenance})

        resp = test_client.post(
            "/api/tasks/task-1/contracts",
            json={
                "objective": "Implement auth module",
                "source_stage": "negotiation",
                "source_type": "architect",
                "negotiation_status": "negotiated",
                "supersedes_contract_id": "contract-0",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["source_stage"] == "negotiation"
        assert data["source_type"] == "architect"
        assert data["negotiation_status"] == "negotiated"
        assert data["supersedes_contract_id"] == "contract-0"

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_lock_contract_sets_locked_by(self, mock_ts_class, test_client):
        """Locking with ?locked_by records the actor and sets negotiation_status=locked."""
        locked_contract = {
            **SAMPLE_CONTRACT,
            "locked_at": "2026-03-30T12:00:00",
            "negotiation_status": "locked",
            "locked_by": "owner",
        }
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.lock_contract.return_value = (True, {"contract": locked_contract})

        resp = test_client.post("/api/contracts/contract-1/lock?locked_by=owner")

        assert resp.status_code == 200
        data = resp.json()
        assert data["negotiation_status"] == "locked"
        assert data["locked_by"] == "owner"
        assert data["locked_at"] is not None

        # Verify locked_by was forwarded to the service
        mock_service.lock_contract.assert_called_once_with("contract-1", locked_by="owner")

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_update_contract_negotiation_status(self, mock_ts_class, test_client):
        """Updating negotiation_status from draft to negotiated is allowed on unlocked contract."""
        updated = {**SAMPLE_CONTRACT, "negotiation_status": "negotiated"}
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.update_contract.return_value = (True, {"contract": updated})

        resp = test_client.put(
            "/api/contracts/contract-1",
            json={"negotiation_status": "negotiated"},
        )

        assert resp.status_code == 200
        assert resp.json()["negotiation_status"] == "negotiated"

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_contract_response_includes_all_provenance_fields(self, mock_ts_class, test_client):
        """GET /contracts/{id} response includes all five provenance fields."""
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.get_contract.return_value = (True, {"contract": SAMPLE_CONTRACT})

        resp = test_client.get("/api/contracts/contract-1")
        assert resp.status_code == 200
        data = resp.json()
        for field in ("source_stage", "source_type", "negotiation_status", "locked_by", "supersedes_contract_id"):
            assert field in data, f"Missing field: {field}"

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_default_negotiation_status_is_draft(self, mock_ts_class, test_client):
        """Creating a contract without negotiation_status defaults to 'draft'."""
        draft_contract = {**SAMPLE_CONTRACT, "negotiation_status": "draft"}
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.create_contract.return_value = (True, {"contract": draft_contract})

        resp = test_client.post(
            "/api/tasks/task-1/contracts",
            json={"objective": "Minimal contract"},
        )

        assert resp.status_code == 200
        # Service call should have negotiation_status="draft" by default
        call_kwargs = mock_service.create_contract.call_args
        assert call_kwargs.kwargs.get("negotiation_status") == "draft"

class TestGetTaskEnrichedWithContract:
    """Tests that GET /api/tasks/{task_id} includes current_contract."""

    @patch("src.server.api_routes.projects_api.TaskService")
    def test_task_detail_includes_contract(self, mock_ts_class, test_client):
        task_data = {
            "id": "task-1",
            "project_id": "proj-1",
            "title": "My task",
            "description": "",
            "status": "draft",
            "assignee": "User",
            "task_order": 0,
            "priority": "medium",
            "created_at": "2026-03-30T10:00:00",
            "updated_at": "2026-03-30T10:00:00",
            "current_contract_id": "contract-1",
        }
        mock_service = MagicMock()
        mock_ts_class.return_value = mock_service
        mock_service.get_task.return_value = (True, {"task": task_data})
        mock_service.get_contract.return_value = (True, {"contract": SAMPLE_CONTRACT})

        resp = test_client.get("/api/tasks/task-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_contract"]["id"] == "contract-1"
        assert data["current_contract"]["objective"] == "Implement auth module"
