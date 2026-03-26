"""Tests for AI-assisted project creation with bootstrap task support."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.projects import project_creation_service as project_creation_service_module
from src.server.services.projects.project_creation_service import ProjectCreationService


def _mock_client():
    client = MagicMock()
    project_table = MagicMock()
    task_table = MagicMock()
    plan_table = MagicMock()

    project_insert = MagicMock()
    project_insert.execute.return_value = MagicMock(
        data=[{"id": "proj-001", "title": "Starter", "created_at": "2026-01-01T00:00:00"}]
    )
    project_table.insert.return_value = project_insert

    task_insert = MagicMock()
    task_insert.execute.side_effect = [
        MagicMock(data=[{"id": "task-bootstrap-1", "title": "Bootstrap project workspace for Starter", "status": "approved", "tags": ["project-bootstrap", "template:nextjs-app"], "created_from": "project-bootstrap"}]),
        MagicMock(data=[{"id": "task-bootstrap-2", "title": "Validate bootstrap workspace for Starter", "status": "approved", "tags": ["project-bootstrap", "bootstrap-validation", "template:nextjs-app"], "created_from": "project-bootstrap-validation", "blocked_by": ["task-bootstrap-1"]}]),
        MagicMock(data=[{"id": "task-bootstrap-3", "title": "Capture bootstrap architecture baseline for Starter", "status": "approved", "tags": ["project-bootstrap", "bootstrap-architecture", "template:nextjs-app"], "created_from": "project-bootstrap-architecture", "blocked_by": ["task-bootstrap-2"]}]),
        MagicMock(data=[{"id": "task-bootstrap-4", "title": "Seed initial follow-up plan for Starter", "status": "approved", "tags": ["project-bootstrap", "bootstrap-followup", "template:nextjs-app"], "created_from": "project-bootstrap-followup", "blocked_by": ["task-bootstrap-3"]}]),
    ]
    task_table.insert.return_value = task_insert

    plan_insert = MagicMock()
    plan_insert.execute.return_value = MagicMock(
        data=[{
            "id": "plan-001",
            "project_id": "proj-001",
            "strategy": "provider-generated",
            "resolved_provider": "chatgpt-codex",
            "requested_provider": "chatgpt-codex",
            "model": "gpt-5.4",
        }]
    )
    plan_table.insert.return_value = plan_insert

    plan_select = MagicMock()
    plan_select.eq.return_value = plan_select
    plan_select.order.return_value = plan_select
    plan_select.limit.return_value = plan_select
    plan_select.execute.return_value = MagicMock(
        data=[{
            "id": "plan-001",
            "project_id": "proj-001",
            "strategy": "provider-generated",
            "resolved_provider": "chatgpt-codex",
            "requested_provider": "chatgpt-codex",
            "model": "gpt-5.4",
        }]
    )
    plan_table.select.return_value = plan_select

    select = MagicMock()
    select.eq.return_value = select
    select.execute.return_value = MagicMock(
        data=[{
            "id": "proj-001",
            "title": "Starter",
            "description": "A starter project",
            "github_repo": "https://github.com/example/starter",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "docs": [],
            "features": [],
            "data": [],
            "pinned": False,
        }]
    )
    project_table.select.return_value = select

    def table_factory(name: str):
        if name == "archon_projects":
            return project_table
        if name == "archon_tasks":
            return task_table
        if name == "archon_bootstrap_plans":
            return plan_table
        return project_table

    client.table.side_effect = table_factory
    return client


@pytest.mark.asyncio
async def test_create_project_with_ai_returns_bootstrap_task():
    client = _mock_client()
    service = ProjectCreationService(supabase_client=client)
    service._generate_ai_documentation = AsyncMock(return_value=False)

    ok, result = await service.create_project_with_ai(
        progress_id="p-1",
        title="Starter",
        description="A starter project",
        github_repo="https://github.com/example/starter",
        bootstrap_template="nextjs-app",
        project_type="web-app",
        bootstrap_policy="strict",
        bootstrap_architect_provider="chatgpt-codex",
        bootstrap_architect_model="gpt-5.4",
    )

    assert ok is True
    assert result["project_id"] == "proj-001"
    assert result["project"]["bootstrap_task"] is not None
    assert result["project"]["bootstrap_task"]["title"].startswith("Bootstrap project workspace")
    assert [task["id"] for task in result["project"]["bootstrap_tasks"]] == [
        "task-bootstrap-1",
        "task-bootstrap-2",
        "task-bootstrap-3",
        "task-bootstrap-4",
    ]
    assert result["project"]["bootstrap_template"] == "nextjs-app"
    assert result["project"]["project_type"] == "web-app"
    assert result["project"]["bootstrap_policy"] == "strict"
    assert result["project"]["bootstrap_architect_provider"] == "chatgpt-codex"
    assert result["project"]["bootstrap_architect_model"] == "gpt-5.4"
    assert result["project"]["bootstrap_plan"]["id"] == "plan-001"


@pytest.mark.asyncio
async def test_create_project_with_ai_can_disable_bootstrap_task():
    client = _mock_client()
    service = ProjectCreationService(supabase_client=client)
    service._generate_ai_documentation = AsyncMock(return_value=False)

    ok, result = await service.create_project_with_ai(
        progress_id="p-2",
        title="No Bootstrap",
        description="No bootstrap",
        create_bootstrap_task=False,
    )

    assert ok is True
    assert result["project"]["bootstrap_task"] is None
    assert result["project"]["bootstrap_tasks"] is None


@pytest.mark.asyncio
async def test_create_project_with_ai_uses_async_architect_bootstrap_path(monkeypatch):
    client = _mock_client()
    service = ProjectCreationService(supabase_client=client)
    service._generate_ai_documentation = AsyncMock(return_value=False)

    async_planner = AsyncMock(return_value=[
        {"id": "task-provider-1", "title": "Provider scaffold", "status": "approved", "tags": ["provider-generated"], "created_from": "project-bootstrap"},
        {"id": "task-provider-2", "title": "Provider follow-up", "status": "approved", "tags": ["provider-generated"], "created_from": "project-bootstrap-followup", "blocked_by": ["task-provider-1"]},
    ])
    monkeypatch.setattr(project_creation_service_module, "create_project_bootstrap_task_pack_async", async_planner)

    ok, result = await service.create_project_with_ai(
        progress_id="p-3",
        title="Provider Planned",
        description="Provider planned bootstrap",
        bootstrap_architect_provider="chatgpt-codex",
        bootstrap_architect_model="gpt-5.4",
    )

    assert ok is True
    async_planner.assert_awaited_once()
    assert result["project"]["bootstrap_task"]["id"] == "task-provider-1"
    assert result["project"]["bootstrap_tasks"][1]["id"] == "task-provider-2"
    assert result["project"]["bootstrap_plan"]["id"] == "plan-001"


@pytest.mark.asyncio
async def test_create_project_with_ai_exposes_default_provider_for_supported_project_types(monkeypatch):
    client = _mock_client()
    service = ProjectCreationService(supabase_client=client)
    service._generate_ai_documentation = AsyncMock(return_value=False)

    async_planner = AsyncMock(return_value=[
        {"id": "task-provider-1", "title": "Provider scaffold", "status": "approved", "tags": ["provider-generated"], "created_from": "project-bootstrap"},
        {"id": "task-provider-2", "title": "Provider follow-up", "status": "approved", "tags": ["provider-generated"], "created_from": "project-bootstrap-followup", "blocked_by": ["task-provider-1"]},
    ])
    monkeypatch.setattr(project_creation_service_module, "create_project_bootstrap_task_pack_async", async_planner)

    ok, result = await service.create_project_with_ai(
        progress_id="p-4",
        title="Provider Defaulted",
        description="Default provider path",
        project_type="web-app",
    )

    assert ok is True
    assert result["project"]["bootstrap_plan"]["resolved_provider"] == "chatgpt-codex"
    assert result["project"]["bootstrap_architect_provider"] == "chatgpt-codex"
    assert result["project"]["bootstrap_architect_model"] == "gpt-5.4"
