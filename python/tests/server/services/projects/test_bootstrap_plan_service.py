"""Tests for bootstrap plan persistence service."""

from unittest.mock import MagicMock

from src.server.services.projects.bootstrap_architect import BootstrapPlanEnvelope
from src.server.services.projects.bootstrap_plan_service import BootstrapPlanService
from src.server.services.projects.bootstrap_planner import BootstrapPlanItem, build_bootstrap_context


def _mock_client():
    client = MagicMock()
    table = MagicMock()

    insert = MagicMock()
    insert.execute.return_value = MagicMock(
        data=[{"id": "plan-001", "project_id": "proj-001", "strategy": "provider-generated"}]
    )
    table.insert.return_value = insert

    select = MagicMock()
    select.eq.return_value = select
    select.order.return_value = select
    select.limit.return_value = select
    select.execute.return_value = MagicMock(
        data=[{"id": "plan-001", "project_id": "proj-001", "strategy": "provider-generated"}]
    )
    table.select.return_value = select

    client.table.return_value = table
    return client


def _make_envelope() -> BootstrapPlanEnvelope:
    return BootstrapPlanEnvelope(
        requested_provider="chatgpt-codex",
        resolved_provider="chatgpt-codex",
        strategy="provider-generated",
        model="gpt-5.4",
        items=[
            BootstrapPlanItem(
                key="scaffold",
                title="Bootstrap",
                description="Scaffold project",
                task_type="feature",
                priority="high",
                complexity="simple",
                max_retries=2,
                created_from="project-bootstrap",
                execution_prompt="Do scaffold",
                acceptance_criteria=["A"],
                tags=["project-bootstrap"],
                blocked_on_key=None,
            )
        ],
        backlog_items=[
            BootstrapPlanItem(
                key="api-contracts",
                title="Implement API contracts",
                description="Create baseline API contracts",
                task_type="feature",
                priority="medium",
                complexity="moderate",
                max_retries=2,
                created_from="project-bootstrap-derived-1",
                execution_prompt="Build API contracts",
                acceptance_criteria=["Contracts created"],
                tags=["project-bootstrap", "bootstrap-derived-backlog", "api"],
                blocked_on_key="followup",
            )
        ],
    )


def test_create_plan_persists_bootstrap_metadata():
    client = _mock_client()
    service = BootstrapPlanService(supabase_client=client)
    context = build_bootstrap_context(
        project_title="Starter",
        project_description="A starter project",
        github_repo="https://github.com/example/starter",
        bootstrap_template="nextjs-app",
        project_type="web-app",
        bootstrap_policy="strict",
    )

    ok, result = service.create_plan(
        project_id="proj-001",
        source_app="virtual-office",
        context=context,
        envelope=_make_envelope(),
        created_tasks=[{"id": "task-1"}],
    )

    assert ok is True
    insert_payload = client.table.return_value.insert.call_args[0][0]
    assert insert_payload["requested_provider"] == "chatgpt-codex"
    assert insert_payload["template"] == "nextjs-app"
    assert insert_payload["project_type"] == "web-app"
    assert insert_payload["bootstrap_policy"] == "strict"
    assert result["plan"]["id"] == "plan-001"
    assert "bootstrap-plan:plan-001" in result["plan"]["created_tasks"][0]["tags"]
    assert insert_payload["metadata"]["backlog_items"][0]["key"] == "api-contracts"
    update_table_calls = client.table.call_args_list
    assert any(call.args[0] == "archon_tasks" for call in update_table_calls)


def test_list_and_get_plan_return_records():
    client = _mock_client()
    service = BootstrapPlanService(supabase_client=client)

    ok, list_result = service.list_plans(project_id="proj-001", limit=5)
    assert ok is True
    assert list_result["plans"][0]["id"] == "plan-001"

    ok, get_result = service.get_plan("plan-001")
    assert ok is True
    assert get_result["plan"]["id"] == "plan-001"


def test_materialize_backlog_creates_missing_tasks_after_followup():
    client = MagicMock()
    plan_table = MagicMock()
    task_table = MagicMock()

    plan_record = {
        "id": "plan-001",
        "project_id": "proj-001",
        "requested_provider": "chatgpt-codex",
        "resolved_provider": "chatgpt-codex",
        "strategy": "provider-generated",
        "model": "gpt-5.4",
        "template": "nextjs-app",
        "project_type": "web-app",
        "bootstrap_policy": "strict",
        "source_app": "virtual-office",
        "status": "materialized",
        "plan_items": [
            {"key": "scaffold", "created_from": "project-bootstrap"},
            {"key": "validation", "created_from": "project-bootstrap-validation"},
            {"key": "followup", "created_from": "project-bootstrap-followup"},
        ],
        "created_tasks": [
            {"id": "task-1", "plan_key": "scaffold", "created_from": "project-bootstrap", "tags": ["project-bootstrap"]},
            {"id": "task-2", "plan_key": "validation", "created_from": "project-bootstrap-validation", "tags": ["project-bootstrap", "bootstrap-validation"]},
            {"id": "task-3", "plan_key": "followup", "created_from": "project-bootstrap-followup", "tags": ["project-bootstrap", "bootstrap-followup"]},
        ],
        "metadata": {
            "backlog_items": [
                {
                    "key": "api-contracts",
                    "title": "Implement API contracts",
                    "description": "Create API contracts",
                    "task_type": "feature",
                    "priority": "medium",
                    "complexity": "moderate",
                    "max_retries": 2,
                    "created_from": "project-bootstrap-derived-1",
                    "execution_prompt": "Build API contracts",
                    "acceptance_criteria": ["Contracts created"],
                    "tags": ["project-bootstrap", "bootstrap-derived-backlog", "api"],
                    "blocked_on_key": "followup",
                }
            ]
        },
        "created_at": "2026-03-21T10:00:00Z",
        "updated_at": "2026-03-21T10:00:00Z",
    }

    plan_select = MagicMock()
    plan_select.eq.return_value = plan_select
    plan_select.execute.return_value = MagicMock(data=[plan_record])
    plan_table.select.return_value = plan_select
    plan_update = MagicMock()
    plan_update.eq.return_value = plan_update
    plan_update.execute.return_value = MagicMock(data=[plan_record])
    plan_table.update.return_value = plan_update

    task_insert = MagicMock()
    task_insert.execute.return_value = MagicMock(
        data=[{
            "id": "task-4",
            "title": "Implement API contracts",
            "status": "approved",
            "tags": ["project-bootstrap", "bootstrap-derived-backlog", "api", "bootstrap-plan:plan-001"],
            "created_from": "project-bootstrap-derived-1",
            "blocked_by": ["task-3"],
        }]
    )
    task_table.insert.return_value = task_insert

    def table_factory(name: str):
        if name == "archon_bootstrap_plans":
            return plan_table
        if name == "archon_tasks":
            return task_table
        return MagicMock()

    client.table.side_effect = table_factory
    service = BootstrapPlanService(supabase_client=client)

    ok, result = service.materialize_backlog("plan-001")

    assert ok is True
    assert result["created_tasks"][0]["id"] == "task-4"
    assert result["created_tasks"][0]["plan_key"] == "api-contracts"
    insert_payload = task_table.insert.call_args[0][0]
    assert insert_payload["blocked_by"] == ["task-3"]
    assert "bootstrap-plan:plan-001" in insert_payload["tags"]
    assert result["plan"]["status"] == "expanded"


def test_materialize_backlog_skips_existing_created_from():
    client = MagicMock()
    plan_table = MagicMock()
    task_table = MagicMock()

    plan_record = {
        "id": "plan-001",
        "project_id": "proj-001",
        "requested_provider": "chatgpt-codex",
        "resolved_provider": "chatgpt-codex",
        "strategy": "provider-generated",
        "model": "gpt-5.4",
        "template": "nextjs-app",
        "project_type": "web-app",
        "bootstrap_policy": "strict",
        "source_app": "virtual-office",
        "status": "materialized",
        "plan_items": [{"key": "followup", "created_from": "project-bootstrap-followup"}],
        "created_tasks": [
            {"id": "task-3", "plan_key": "followup", "created_from": "project-bootstrap-followup"},
            {"id": "task-4", "plan_key": "api-contracts", "created_from": "project-bootstrap-derived-1"},
        ],
        "metadata": {
            "backlog_items": [
                {
                    "key": "api-contracts",
                    "title": "Implement API contracts",
                    "description": "Create API contracts",
                    "task_type": "feature",
                    "priority": "medium",
                    "complexity": "moderate",
                    "max_retries": 2,
                    "created_from": "project-bootstrap-derived-1",
                    "execution_prompt": "Build API contracts",
                    "acceptance_criteria": ["Contracts created"],
                    "tags": ["project-bootstrap", "bootstrap-derived-backlog", "api"],
                    "blocked_on_key": "followup",
                }
            ]
        },
        "created_at": "2026-03-21T10:00:00Z",
        "updated_at": "2026-03-21T10:00:00Z",
    }

    plan_select = MagicMock()
    plan_select.eq.return_value = plan_select
    plan_select.execute.return_value = MagicMock(data=[plan_record])
    plan_table.select.return_value = plan_select

    def table_factory(name: str):
        if name == "archon_bootstrap_plans":
            return plan_table
        if name == "archon_tasks":
            return task_table
        return MagicMock()

    client.table.side_effect = table_factory
    service = BootstrapPlanService(supabase_client=client)

    ok, result = service.materialize_backlog("plan-001")

    assert ok is True
    assert result["created_tasks"] == []
    assert result["skipped"] == 1
    task_table.insert.assert_not_called()
