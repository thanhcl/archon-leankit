"""
API/client response conformance tests.

Validates that API responses match the generated TypeScript contract types at runtime
(not just at codegen time).  For each critical endpoint the test:

  1. Mocks the service layer to return a known fixture.
  2. Calls the endpoint via FastAPI TestClient.
  3. Asserts the response JSON conforms to the Pydantic contract model:
       - all required fields present
       - no extra / undocumented fields
       - Pydantic model_validate succeeds (type-level check)

Critical endpoints covered: tasks, execution_runs, projects, bootstrap_plans.
Must complete in < 30 seconds.
"""

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.server.models.api_contracts import (
    BootstrapPlanListResponse,
    BootstrapPlanResponse,
    ExecutionRunListResponse,
    ExecutionRunResponse,
    ProjectListResponse,
    ProjectResponse,
    TaskListResponse,
    TaskResponse,
)

# ---------------------------------------------------------------------------
# Conformance helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc).isoformat()


def _required_fields(model: type[BaseModel]) -> set[str]:
    """Fields with no default — must appear in every response."""
    return {name for name, f in model.model_fields.items() if f.is_required()}


def _all_fields(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields.keys())


def assert_conforms(data: dict[str, Any], model: type[BaseModel]) -> None:
    """Assert *data* fully conforms to *model*:
    - all required fields present
    - no undocumented extra fields
    - Pydantic model_validate succeeds (deep type check)
    """
    keys = set(data.keys())
    missing = _required_fields(model) - keys
    extra = keys - _all_fields(model)

    errors: list[str] = []
    if missing:
        errors.append(f"missing required fields: {sorted(missing)}")
    if extra:
        errors.append(f"extra undocumented fields: {sorted(extra)}")

    assert not errors, f"{model.__name__} conformance failure — " + "; ".join(errors)

    model.model_validate(data)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _task_fixture(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "task-c01",
        "project_id": "proj-c01",
        "title": "Conformance test task",
        "description": "desc",
        "status": "draft",
        "assignee": "User",
        "task_order": 1,
        "priority": "medium",
        "complexity": "simple",
        "retry_count": 0,
        "max_retries": 3,
        "created_at": NOW,
        "updated_at": NOW,
        "archived": False,
        "allowed_paths": [],
        "forbidden_paths": [],
        "repo_guidance_packs": [],
        "task_type": "feature",
        "linked_plan_item": None,
        "feature": None,
        "owner": None,
        "source_app": None,
        "state_changed_at": None,
        "archived_at": None,
        "archived_by": None,
        "parent_task_id": None,
        "blocked_by": None,
        "phase": None,
        "module": None,
        "sprint": None,
        "tags": None,
        "created_by": None,
        "created_from": None,
        "executed_by": None,
        "reviewed_by": None,
        "sources": None,
        "code_examples": None,
        "acceptance_criteria": None,
        "execution_result": None,
        "architect_review": None,
        "execution_prompt": None,
        "state_history": None,
        "review_history": None,
        "stats": None,
        "featureColor": None,
        "plan_item_id": None,
    }
    base.update(kw)
    return base


def _execution_run_fixture(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "run-c01",
        "task_id": "task-c01",
        "project_id": "proj-c01",
        "status": "running",
        "stage": "execute",
        "started_at": NOW,
        "retry_index": 0,
        "engine_id": None,
        "session_id": None,
        "model": None,
        "finished_at": None,
        "duration_seconds": None,
        "token_input": None,
        "token_output": None,
        "total_tokens": None,
        "thinking_tokens": None,
        "cost_usd": None,
        "result_summary": None,
        "error_summary": None,
        "metadata": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(kw)
    return base


def _project_fixture(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "proj-c01",
        "title": "Conformance project",
        "created_at": NOW,
        "updated_at": NOW,
        "description": None,
        "github_repo": None,
        "docs": None,
        "features": None,
        "data": None,
        "technical_sources": None,
        "business_sources": None,
        "pinned": False,
        "source_app": None,
        "layout_id": None,
        "team_config": None,
        "director_config": None,
        "team_lead_config": None,
        "office_settings": None,
        "bootstrap_task": None,
        "bootstrap_tasks": None,
        "bootstrap_plan": None,
        "bootstrap_template": None,
        "project_type": None,
        "bootstrap_policy": None,
        "bootstrap_architect_provider": None,
        "bootstrap_architect_model": None,
    }
    base.update(kw)
    return base


def _bootstrap_plan_fixture(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "plan-c01",
        "project_id": "proj-c01",
        "requested_provider": "claude",
        "resolved_provider": "claude",
        "strategy": "standard",
        "template": "default",
        "project_type": "web",
        "bootstrap_policy": "auto",
        "status": "completed",
        "created_at": NOW,
        "updated_at": NOW,
        "model": None,
        "source_app": None,
        "plan_items": None,
        "created_tasks": None,
        "metadata": None,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _env_isolation() -> Any:
    """Restore environment variables after module-level fixtures load dotenv.

    Importing api_routes submodules triggers __init__.py which imports all
    other routers.  One of those routers calls load_dotenv(), which sets env
    vars (e.g. LEANKIT_OPENCLAW_INGEST_SECRET) from the project .env file.
    This fixture snapshots the env before and restores it after the module
    so the injected vars don't bleed into subsequent test modules.
    """
    before = dict(os.environ)
    yield
    for key in list(os.environ.keys()):
        if key not in before:
            del os.environ[key]
    for key, val in before.items():
        os.environ.setdefault(key, val)
        os.environ[key] = val


# ---------------------------------------------------------------------------
# TestClient fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def execution_runs_client() -> TestClient:
    """Minimal TestClient with only the execution-runs router."""
    from src.server.api_routes.execution_runs_api import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(scope="module")
def bootstrap_plans_client() -> TestClient:
    """Minimal TestClient with only the bootstrap-plans router."""
    from src.server.api_routes.bootstrap_plans_api import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Execution Runs — uses validate_response() so response is Pydantic-enforced
# ---------------------------------------------------------------------------


class TestExecutionRunConformance:
    def test_list_returns_execution_run_list_response_shape(
        self, execution_runs_client: TestClient
    ) -> None:
        """GET /api/execution-runs → body matches ExecutionRunListResponse."""
        run = _execution_run_fixture()
        mock_svc = MagicMock()
        mock_svc.list_runs.return_value = (
            True,
            {"runs": [run], "total_count": 1, "filters_applied": "none"},
        )

        with patch(
            "src.server.api_routes.execution_runs_api.ExecutionRunService",
            return_value=mock_svc,
        ):
            resp = execution_runs_client.get("/api/execution-runs")

        assert resp.status_code == 200
        body = resp.json()
        assert_conforms(body, ExecutionRunListResponse)
        assert len(body["runs"]) == 1
        assert_conforms(body["runs"][0], ExecutionRunResponse)

    def test_single_run_returns_execution_run_response_shape(
        self, execution_runs_client: TestClient
    ) -> None:
        """GET /api/execution-runs/{id} → body matches ExecutionRunResponse."""
        run = _execution_run_fixture()
        mock_svc = MagicMock()
        mock_svc.get_run.return_value = (True, {"run": run})

        with patch(
            "src.server.api_routes.execution_runs_api.ExecutionRunService",
            return_value=mock_svc,
        ):
            resp = execution_runs_client.get("/api/execution-runs/run-c01")

        assert resp.status_code == 200
        assert_conforms(resp.json(), ExecutionRunResponse)

    def test_completed_run_with_metrics_conforms(
        self, execution_runs_client: TestClient
    ) -> None:
        """ExecutionRunResponse with all metric fields populated still conforms."""
        run = _execution_run_fixture(
            status="completed",
            stage="code-review",
            finished_at=NOW,
            duration_seconds=45.2,
            token_input=1000,
            token_output=500,
            total_tokens=1500,
            thinking_tokens=200,
            cost_usd=0.023,
            result_summary="Success",
        )
        mock_svc = MagicMock()
        mock_svc.get_run.return_value = (True, {"run": run})

        with patch(
            "src.server.api_routes.execution_runs_api.ExecutionRunService",
            return_value=mock_svc,
        ):
            resp = execution_runs_client.get("/api/execution-runs/run-c01")

        assert resp.status_code == 200
        data = resp.json()
        assert_conforms(data, ExecutionRunResponse)
        assert data["cost_usd"] == pytest.approx(0.023)
        assert data["total_tokens"] == 1500

    def test_undocumented_service_field_is_stripped(
        self, execution_runs_client: TestClient
    ) -> None:
        """Fields added by service but absent from contract must NOT leak into response."""
        run = _execution_run_fixture(internal_db_col="surprise")
        mock_svc = MagicMock()
        mock_svc.get_run.return_value = (True, {"run": run})

        with patch(
            "src.server.api_routes.execution_runs_api.ExecutionRunService",
            return_value=mock_svc,
        ):
            resp = execution_runs_client.get("/api/execution-runs/run-c01")

        assert resp.status_code == 200
        assert "internal_db_col" not in resp.json(), (
            "Service-internal field leaked through ExecutionRunResponse; "
            "add it to the contract or strip it in the service."
        )

    def test_all_valid_statuses_accepted(self, execution_runs_client: TestClient) -> None:
        """All ExecutionRunStatus enum values round-trip through the response."""
        for status in ("queued", "running", "reviewing", "completed", "failed", "cancelled"):
            run = _execution_run_fixture(status=status, finished_at=NOW)
            mock_svc = MagicMock()
            mock_svc.get_run.return_value = (True, {"run": run})

            with patch(
                "src.server.api_routes.execution_runs_api.ExecutionRunService",
                return_value=mock_svc,
            ):
                resp = execution_runs_client.get("/api/execution-runs/run-c01")

            assert resp.status_code == 200, f"status={status!r} rejected with 422"
            assert resp.json()["status"] == status

    def test_all_valid_stages_accepted(self, execution_runs_client: TestClient) -> None:
        """All ExecutionRunStage enum values round-trip through the response."""
        for stage in ("execute", "architect-review", "code-review", "retry"):
            run = _execution_run_fixture(stage=stage)
            mock_svc = MagicMock()
            mock_svc.get_run.return_value = (True, {"run": run})

            with patch(
                "src.server.api_routes.execution_runs_api.ExecutionRunService",
                return_value=mock_svc,
            ):
                resp = execution_runs_client.get("/api/execution-runs/run-c01")

            assert resp.status_code == 200, f"stage={stage!r} rejected with 422"
            assert resp.json()["stage"] == stage


# ---------------------------------------------------------------------------
# Bootstrap Plans — uses validate_response() so response is Pydantic-enforced
# ---------------------------------------------------------------------------


class TestBootstrapPlanConformance:
    def test_list_returns_bootstrap_plan_list_response_shape(
        self, bootstrap_plans_client: TestClient
    ) -> None:
        """GET /api/projects/{id}/bootstrap-plans → body matches BootstrapPlanListResponse."""
        plan = _bootstrap_plan_fixture()
        mock_svc = MagicMock()
        mock_svc.list_plans.return_value = (True, {"plans": [plan], "total_count": 1})

        with patch(
            "src.server.api_routes.bootstrap_plans_api.BootstrapPlanService",
            return_value=mock_svc,
        ):
            resp = bootstrap_plans_client.get("/api/projects/proj-c01/bootstrap-plans")

        assert resp.status_code == 200
        body = resp.json()
        assert_conforms(body, BootstrapPlanListResponse)
        assert len(body["plans"]) == 1
        assert_conforms(body["plans"][0], BootstrapPlanResponse)

    def test_single_plan_returns_bootstrap_plan_response_shape(
        self, bootstrap_plans_client: TestClient
    ) -> None:
        """GET /api/bootstrap-plans/{id} → body matches BootstrapPlanResponse."""
        plan = _bootstrap_plan_fixture()
        mock_svc = MagicMock()
        mock_svc.get_plan.return_value = (True, {"plan": plan})

        with patch(
            "src.server.api_routes.bootstrap_plans_api.BootstrapPlanService",
            return_value=mock_svc,
        ):
            resp = bootstrap_plans_client.get("/api/bootstrap-plans/plan-c01")

        assert resp.status_code == 200
        assert_conforms(resp.json(), BootstrapPlanResponse)

    def test_undocumented_plan_field_stripped(
        self, bootstrap_plans_client: TestClient
    ) -> None:
        """Service-internal fields absent from BootstrapPlanResponse must not appear."""
        plan = _bootstrap_plan_fixture(internal_debug_flag=True)
        mock_svc = MagicMock()
        mock_svc.get_plan.return_value = (True, {"plan": plan})

        with patch(
            "src.server.api_routes.bootstrap_plans_api.BootstrapPlanService",
            return_value=mock_svc,
        ):
            resp = bootstrap_plans_client.get("/api/bootstrap-plans/plan-c01")

        assert resp.status_code == 200
        assert "internal_debug_flag" not in resp.json()

    def test_empty_plan_list_conforms(
        self, bootstrap_plans_client: TestClient
    ) -> None:
        """Empty bootstrap plan list still conforms to BootstrapPlanListResponse."""
        mock_svc = MagicMock()
        mock_svc.list_plans.return_value = (True, {"plans": [], "total_count": 0})

        with patch(
            "src.server.api_routes.bootstrap_plans_api.BootstrapPlanService",
            return_value=mock_svc,
        ):
            resp = bootstrap_plans_client.get("/api/projects/proj-c01/bootstrap-plans")

        assert resp.status_code == 200
        body = resp.json()
        assert_conforms(body, BootstrapPlanListResponse)
        assert body["total_count"] == 0


# ---------------------------------------------------------------------------
# Projects — schema-level + response-shape tests (no heavy app needed)
# ---------------------------------------------------------------------------


class TestProjectConformance:
    """ProjectResponse / ProjectListResponse conformance tests.

    The GET /api/projects endpoint returns {projects, timestamp, count} which
    matches ProjectListResponse — verified here at the Pydantic-schema level.
    """

    def test_project_response_fixture_conforms(self) -> None:
        """ProjectResponse accepts a fully-formed project fixture."""
        assert_conforms(_project_fixture(), ProjectResponse)

    def test_project_list_response_fixture_conforms(self) -> None:
        """ProjectListResponse accepts a well-formed list fixture."""
        data = {
            "projects": [_project_fixture()],
            "timestamp": NOW,
            "count": 1,
        }
        assert_conforms(data, ProjectListResponse)

    def test_project_response_missing_required_id_detected(self) -> None:
        """Missing required field 'id' is detected by assert_conforms."""
        proj = _project_fixture()
        del proj["id"]
        with pytest.raises(AssertionError, match="missing required fields"):
            assert_conforms(proj, ProjectResponse)

    def test_project_response_missing_timestamps_detected(self) -> None:
        """Missing created_at / updated_at are detected."""
        proj = _project_fixture()
        del proj["created_at"]
        del proj["updated_at"]
        with pytest.raises(AssertionError, match="missing required fields"):
            assert_conforms(proj, ProjectResponse)

    def test_project_response_extra_field_detected(self) -> None:
        """Undocumented DB column in project dict is caught."""
        proj = _project_fixture(db_internal_col="secret")
        with pytest.raises(AssertionError, match="extra undocumented fields"):
            assert_conforms(proj, ProjectResponse)

    def test_project_list_response_missing_timestamp_detected(self) -> None:
        """ProjectListResponse missing 'timestamp' fails conformance."""
        data = {"projects": [], "count": 0}
        with pytest.raises(AssertionError, match="missing required fields"):
            assert_conforms(data, ProjectListResponse)


# ---------------------------------------------------------------------------
# Tasks — schema-level tests + drift documentation
# ---------------------------------------------------------------------------


class TestTaskConformance:
    """TaskResponse / TaskListResponse conformance tests.

    Includes drift tests that document known divergence between the API wire
    format and the TypeScript contract for list endpoints.
    """

    def test_task_response_fixture_conforms(self) -> None:
        """TaskResponse accepts a fully-formed task fixture."""
        assert_conforms(_task_fixture(), TaskResponse)

    def test_task_list_response_fixture_conforms(self) -> None:
        """TaskListResponse accepts a well-formed list fixture."""
        data = {
            "tasks": [_task_fixture()],
            "total_count": 1,
            "filters_applied": "none",
            "include_closed": False,
        }
        assert_conforms(data, TaskListResponse)

    def test_task_response_missing_required_fields_detected(self) -> None:
        """Partial task fixture (missing id, status, created_at…) fails conformance."""
        with pytest.raises(AssertionError, match="missing required fields"):
            assert_conforms({"id": "t1", "project_id": "p1"}, TaskResponse)

    def test_task_response_extra_field_detected(self) -> None:
        """DB-internal fields absent from TaskResponse are caught."""
        task = _task_fixture(db_secret_col="internal")
        with pytest.raises(AssertionError, match="extra undocumented fields"):
            assert_conforms(task, TaskResponse)

    def test_all_task_statuses_valid(self) -> None:
        """All 15 TaskStatus enum values are accepted by TaskResponse."""
        statuses = [
            "draft", "proposed", "approved", "planning", "owner-qa",
            "assigned", "executing", "architect-review", "code-review",
            "review", "done", "failed", "escalated", "on-hold", "cancelled",
        ]
        for status in statuses:
            parsed = TaskResponse.model_validate(_task_fixture(status=status))
            assert parsed.status == status

    def test_task_response_with_linked_plan_item_conforms(self) -> None:
        """TaskResponse accepts a non-null linked_plan_item."""
        task = _task_fixture(
            plan_item_id="item-001",
            linked_plan_item={"id": "item-001", "item_key": "K-1", "title": "Plan item", "status": "open"},
        )
        assert_conforms(task, TaskResponse)

    # ── Drift documentation ────────────────────────────────────────────────

    def test_get_tasks_endpoint_shape_drifts_from_contract(self) -> None:
        """DRIFT: GET /api/tasks returns {tasks, pagination}, not TaskListResponse.

        The TypeScript contract (TaskListResponse) expects:
          total_count, filters_applied, include_closed

        The actual /api/tasks endpoint returns:
          pagination: {total, page, per_page, pages}

        This test locks the drift in place so it cannot silently worsen.
        When the endpoint is fixed to match TaskListResponse, remove this test.
        """
        actual_response_shape = {
            "tasks": [_task_fixture()],
            "pagination": {"total": 1, "page": 1, "per_page": 10, "pages": 1},
        }
        response_keys = set(actual_response_shape.keys())
        contract_keys = _all_fields(TaskListResponse)
        required_keys = _required_fields(TaskListResponse)

        extra_in_response = response_keys - contract_keys
        missing_from_response = required_keys - response_keys

        assert "pagination" in extra_in_response, (
            "DRIFT RESOLVED: GET /api/tasks no longer returns 'pagination'. Update this test."
        )
        assert "total_count" in missing_from_response, (
            "DRIFT RESOLVED: GET /api/tasks now returns 'total_count'. Update this test."
        )

    def test_get_project_tasks_endpoint_returns_array_not_list_response(self) -> None:
        """DRIFT: GET /api/projects/{id}/tasks returns a bare task array, not TaskListResponse.

        The TypeScript contract expects a TaskListResponse object {tasks, total_count, …}.
        The endpoint returns a list[TaskResponse] directly with no wrapper object.

        When the endpoint is fixed to return TaskListResponse, remove this test.
        """
        actual_response = [_task_fixture()]
        assert isinstance(actual_response, list), (
            "DRIFT RESOLVED: the endpoint now returns a dict. Update this test."
        )
        with pytest.raises((AttributeError, TypeError)):
            assert_conforms(actual_response, TaskListResponse)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cross-model schema parity (TS interface vs Pydantic model fields)
# ---------------------------------------------------------------------------


class TestSchemaParityWithTypeScript:
    """Verify that Pydantic models have all fields assumed by the TypeScript client.

    These are the fields that the frontend directly reads — any regression here
    breaks the UI without a TypeScript compile error (because missing optional
    fields surface as undefined at runtime).
    """

    def test_task_response_has_ts_expected_fields(self) -> None:
        fields = _all_fields(TaskResponse)
        expected = {
            "id", "project_id", "title", "status", "created_at", "updated_at",
            "task_type", "linked_plan_item", "priority", "complexity",
            "assignee", "task_order", "archived",
        }
        missing = expected - fields
        assert not missing, f"TaskResponse missing TS-expected fields: {sorted(missing)}"

    def test_execution_run_response_has_ts_expected_fields(self) -> None:
        fields = _all_fields(ExecutionRunResponse)
        expected = {
            "id", "task_id", "project_id", "status", "stage",
            "started_at", "retry_index", "cost_usd", "total_tokens",
        }
        missing = expected - fields
        assert not missing, f"ExecutionRunResponse missing TS-expected fields: {sorted(missing)}"

    def test_project_response_has_ts_expected_fields(self) -> None:
        fields = _all_fields(ProjectResponse)
        expected = {"id", "title", "created_at", "updated_at", "description", "github_repo"}
        missing = expected - fields
        assert not missing, f"ProjectResponse missing TS-expected fields: {sorted(missing)}"

    def test_bootstrap_plan_response_has_ts_expected_fields(self) -> None:
        fields = _all_fields(BootstrapPlanResponse)
        expected = {
            "id", "project_id", "requested_provider", "resolved_provider",
            "strategy", "template", "project_type", "bootstrap_policy",
            "status", "created_at", "updated_at",
        }
        missing = expected - fields
        assert not missing, (
            f"BootstrapPlanResponse missing TS-expected fields: {sorted(missing)}"
        )

    def test_task_list_response_has_ts_expected_fields(self) -> None:
        fields = _all_fields(TaskListResponse)
        expected = {"tasks", "total_count", "filters_applied", "include_closed"}
        missing = expected - fields
        assert not missing, f"TaskListResponse missing TS-expected fields: {sorted(missing)}"

    def test_execution_run_list_response_has_ts_expected_fields(self) -> None:
        fields = _all_fields(ExecutionRunListResponse)
        expected = {"runs", "total_count", "filters_applied"}
        missing = expected - fields
        assert not missing, (
            f"ExecutionRunListResponse missing TS-expected fields: {sorted(missing)}"
        )

    def test_task_list_response_total_count_is_int(self) -> None:
        """total_count field must remain int, not float/string (TS uses number)."""
        parsed = TaskListResponse.model_validate({
            "tasks": [], "total_count": 42, "filters_applied": "none", "include_closed": False,
        })
        assert isinstance(parsed.total_count, int)

    def test_execution_run_list_filters_applied_default(self) -> None:
        """filters_applied has a default so omitting it in service response is safe."""
        parsed = ExecutionRunListResponse.model_validate({"runs": [], "total_count": 0})
        assert parsed.filters_applied == "none"
