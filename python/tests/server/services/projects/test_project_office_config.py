"""Tests for Virtual Office config on archon_projects — create, update, list, defaults."""

from unittest.mock import MagicMock

import pytest

from src.server.services.projects import bootstrap_task_template as bootstrap_task_template_module
from src.server.services.projects.bootstrap_architect import BootstrapPlanEnvelope
from src.server.services.projects.bootstrap_planner import BootstrapPlanItem
from src.server.services.projects.project_service import (
    DEFAULT_DIRECTOR_CONFIG,
    DEFAULT_TEAM_CONFIG,
    DEFAULT_TEAM_LEAD_CONFIG,
    OFFICE_FIELDS,
    ProjectService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client(select_data=None, insert_data=None, update_data=None):
    """Mock Supabase client with chained method support."""
    client = MagicMock()
    table = MagicMock()
    plan_table = MagicMock()
    policy_table = MagicMock()

    # select chain
    select = MagicMock()
    select.eq.return_value = select
    select.in_.return_value = select
    select.neq.return_value = select
    select.order.return_value = select
    select.limit.return_value = select
    select.single.return_value = select
    execute_result = MagicMock()
    execute_result.data = select_data if select_data is not None else []
    select.execute.return_value = execute_result
    table.select.return_value = select

    # insert chain
    insert = MagicMock()
    insert_result = MagicMock()
    if isinstance(insert_data, list) and insert_data and isinstance(insert_data[0], list):
        insert.execute.side_effect = [MagicMock(data=item) for item in insert_data]
    else:
        insert_result.data = insert_data if insert_data is not None else [{"id": "proj-new", "title": "New", "created_at": "2026-01-01"}]
        insert.execute.return_value = insert_result
    table.insert.return_value = insert

    # update chain
    update = MagicMock()
    update.eq.return_value = update
    update.neq.return_value = update
    update_result = MagicMock()
    update_result.data = update_data if update_data is not None else [{"id": "proj-1"}]
    update.execute.return_value = update_result
    table.update.return_value = update

    plan_insert = MagicMock()
    plan_insert.execute.return_value = MagicMock(
        data=[{"id": "plan-001", "project_id": "proj-new", "strategy": "rule-based"}]
    )
    plan_table.insert.return_value = plan_insert

    plan_select = MagicMock()
    plan_select.eq.return_value = plan_select
    plan_select.order.return_value = plan_select
    plan_select.limit.return_value = plan_select
    plan_select.execute.return_value = MagicMock(
        data=[{"id": "plan-001", "project_id": "proj-new", "strategy": "rule-based"}]
    )
    plan_table.select.return_value = plan_select

    policy_select = MagicMock()
    policy_select.eq.return_value = policy_select
    policy_select.select.return_value = policy_select
    policy_select.maybe_single.return_value = policy_select
    policy_select.execute.return_value = MagicMock(data=None)
    policy_table.select.return_value = policy_select

    policy_upsert = MagicMock()
    policy_upsert.execute.return_value = MagicMock(
        data=[{"id": "policy-001", "project_id": "proj-new", "is_active": True}]
    )
    policy_table.upsert.return_value = policy_upsert

    def table_factory(name: str):
        if name == "archon_bootstrap_plans":
            return plan_table
        if name == "archon_engine_policies":
            return policy_table
        return table

    client.table.side_effect = table_factory
    client._main_table = table
    client._plan_table = plan_table
    client._policy_table = policy_table
    return client


def _make_project(**overrides):
    base = {
        "id": "proj-001",
        "title": "Test Project",
        "github_repo": None,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "pinned": False,
        "description": "",
        "docs": [],
        "features": [],
        "data": [],
        "source_app": "test-app",
        "layout_id": "executive-suite",
        "team_config": [{"slotIndex": 0, "agentId": "coder-a", "name": "Coder A", "role": "backend-dev", "color": "#FF6B00", "decorations": []}],
        "director_config": {"name": "Boss", "color": "#111"},
        "team_lead_config": {"name": "TL", "color": "#222"},
        "office_settings": {"mockMode": True},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests: OFFICE_FIELDS constant
# ---------------------------------------------------------------------------


class TestOfficeFieldsConstant:
    def test_all_fields_present(self):
        expected = {"source_app", "layout_id", "team_config", "director_config", "team_lead_config", "office_settings"}
        assert set(OFFICE_FIELDS) == expected


# ---------------------------------------------------------------------------
# Tests: create_project with office config
# ---------------------------------------------------------------------------


class TestCreateProjectWithOfficeConfig:
    def test_creates_with_office_fields(self):
        client = _mock_client(
            insert_data=[
                [{"id": "proj-new", "title": "KMS", "created_at": "2026-01-01", "github_repo": None}],
                [{"id": "task-bootstrap-1", "title": "Bootstrap project workspace for KMS", "status": "approved", "tags": ["project-bootstrap"], "created_from": "project-bootstrap"}],
                [{"id": "task-bootstrap-2", "title": "Validate bootstrap workspace for KMS", "status": "approved", "tags": ["project-bootstrap", "bootstrap-validation"], "created_from": "project-bootstrap-validation", "blocked_by": ["task-bootstrap-1"]}],
                [{"id": "task-bootstrap-3", "title": "Seed initial follow-up plan for KMS", "status": "approved", "tags": ["project-bootstrap", "bootstrap-followup"], "created_from": "project-bootstrap-followup", "blocked_by": ["task-bootstrap-2"]}],
            ]
        )
        service = ProjectService(supabase_client=client)

        ok, result = service.create_project(
            title="KMS",
            source_app="sesb-kms",
            layout_id="executive-suite",
            team_config=[{"slotIndex": 0, "agentId": "coder", "name": "C", "role": "dev", "color": "#F00", "decorations": []}],
            director_config={"name": "Thanh", "color": "#1B3A5C"},
        )

        assert ok is True
        # Verify the insert call included office fields
        project_insert = client._main_table.insert.call_args_list[0][0][0]
        bootstrap_insert = client._main_table.insert.call_args_list[1][0][0]
        validation_insert = client._main_table.insert.call_args_list[2][0][0]
        followup_insert = client._main_table.insert.call_args_list[3][0][0]
        assert project_insert["source_app"] == "sesb-kms"
        assert project_insert["layout_id"] == "executive-suite"
        assert len(project_insert["team_config"]) == 1
        assert project_insert["director_config"]["name"] == "Thanh"
        assert bootstrap_insert["created_from"] == "project-bootstrap"
        assert "project-bootstrap" in bootstrap_insert["tags"]
        assert bootstrap_insert["status"] == "approved"
        assert validation_insert["blocked_by"] == ["task-bootstrap-1"]
        assert followup_insert["blocked_by"] == ["task-bootstrap-2"]
        assert result["project"]["bootstrap_task"]["id"] == "task-bootstrap-1"
        assert [task["id"] for task in result["project"]["bootstrap_tasks"]] == [
            "task-bootstrap-1",
            "task-bootstrap-2",
            "task-bootstrap-3",
        ]

    def test_creates_with_default_team_when_not_specified(self):
        client = _mock_client(
            insert_data=[
                [{"id": "proj-new", "title": "My Proj", "created_at": "2026-01-01", "github_repo": None}],
                [{"id": "task-bootstrap-2", "title": "Bootstrap project workspace for My Proj", "status": "approved", "tags": ["project-bootstrap"], "created_from": "project-bootstrap"}],
                [{"id": "task-bootstrap-3", "title": "Validate bootstrap workspace for My Proj", "status": "approved", "tags": ["project-bootstrap", "bootstrap-validation"], "created_from": "project-bootstrap-validation", "blocked_by": ["task-bootstrap-2"]}],
                [{"id": "task-bootstrap-4", "title": "Seed initial follow-up plan for My Proj", "status": "approved", "tags": ["project-bootstrap", "bootstrap-followup"], "created_from": "project-bootstrap-followup", "blocked_by": ["task-bootstrap-3"]}],
            ]
        )
        service = ProjectService(supabase_client=client)

        ok, result = service.create_project(title="My Proj")

        assert ok is True
        insert_call = client._main_table.insert.call_args_list[0][0][0]
        assert insert_call["team_config"] == DEFAULT_TEAM_CONFIG
        assert insert_call["director_config"] == DEFAULT_DIRECTOR_CONFIG
        assert insert_call["team_lead_config"] == DEFAULT_TEAM_LEAD_CONFIG
        assert result["project"]["bootstrap_task"]["id"] == "task-bootstrap-2"
        assert len(result["project"]["bootstrap_tasks"]) == 3

    def test_explicit_empty_team_overrides_default(self):
        client = _mock_client(
            insert_data=[
                [{"id": "proj-new", "title": "Empty", "created_at": "2026-01-01", "github_repo": None}],
                [{"id": "task-bootstrap-3", "title": "Bootstrap project workspace for Empty", "status": "approved", "tags": ["project-bootstrap"], "created_from": "project-bootstrap"}],
                [{"id": "task-bootstrap-4", "title": "Validate bootstrap workspace for Empty", "status": "approved", "tags": ["project-bootstrap", "bootstrap-validation"], "created_from": "project-bootstrap-validation", "blocked_by": ["task-bootstrap-3"]}],
                [{"id": "task-bootstrap-5", "title": "Seed initial follow-up plan for Empty", "status": "approved", "tags": ["project-bootstrap", "bootstrap-followup"], "created_from": "project-bootstrap-followup", "blocked_by": ["task-bootstrap-4"]}],
            ]
        )
        service = ProjectService(supabase_client=client)

        ok, _ = service.create_project(title="Empty", team_config=[])

        insert_call = client._main_table.insert.call_args_list[0][0][0]
        assert insert_call["team_config"] == []

    def test_can_disable_bootstrap_task_creation(self):
        client = _mock_client(
            insert_data=[
                [{"id": "proj-new", "title": "No Bootstrap", "created_at": "2026-01-01", "github_repo": None}],
            ]
        )
        service = ProjectService(supabase_client=client)

        ok, result = service.create_project(title="No Bootstrap", create_bootstrap_task=False)

        assert ok is True
        assert len(client._main_table.insert.call_args_list) == 1
        assert result["project"]["bootstrap_task"] is None
        assert result["project"]["bootstrap_tasks"] is None

    def test_mirrors_legacy_policy_fields_into_engine_policies_on_create(self):
        client = _mock_client(
            insert_data=[[{"id": "proj-new", "title": "Policy Project", "created_at": "2026-01-01", "github_repo": None}]]
        )
        service = ProjectService(supabase_client=client)

        ok, _ = service.create_project(
            title="Policy Project",
            office_settings={"preferred_runner": "codex-cli", "isolation_mode": "git-worktree"},
            team_lead_config={"review_mode": "api"},
            create_bootstrap_task=False,
        )

        assert ok is True
        upsert_payload = client._policy_table.upsert.call_args[0][0]
        assert upsert_payload["project_id"] == "proj-new"
        assert upsert_payload["model_routing"] == {"default_runner": "codex-cli"}
        assert upsert_payload["review_policy"] == {"review_mode": "api"}
        assert upsert_payload["isolation_policy"] == {"worktree_mode": "isolated"}

    def test_bootstrap_task_uses_template_hint(self):
        client = _mock_client(
            insert_data=[
                [{"id": "proj-new", "title": "Starter", "created_at": "2026-01-01", "github_repo": None}],
                [{"id": "task-bootstrap-4", "title": "Bootstrap project workspace for Starter", "status": "approved", "tags": ["project-bootstrap", "template:nextjs-app"], "created_from": "project-bootstrap"}],
                [{"id": "task-bootstrap-5", "title": "Validate bootstrap workspace for Starter", "status": "approved", "tags": ["project-bootstrap", "bootstrap-validation", "template:nextjs-app"], "created_from": "project-bootstrap-validation", "blocked_by": ["task-bootstrap-4"]}],
                [{"id": "task-bootstrap-6", "title": "Seed initial follow-up plan for Starter", "status": "approved", "tags": ["project-bootstrap", "bootstrap-followup", "template:nextjs-app"], "created_from": "project-bootstrap-followup", "blocked_by": ["task-bootstrap-5"]}],
            ]
        )
        service = ProjectService(supabase_client=client)

        ok, _ = service.create_project(title="Starter", bootstrap_template="nextjs-app")

        assert ok is True
        bootstrap_insert = client._main_table.insert.call_args_list[1][0][0]
        validation_insert = client._main_table.insert.call_args_list[2][0][0]
        followup_insert = client._main_table.insert.call_args_list[3][0][0]
        assert "template:nextjs-app" in bootstrap_insert["tags"]
        assert "Template: nextjs-app" in bootstrap_insert["execution_prompt"]
        assert "template:nextjs-app" in validation_insert["tags"]
        assert "template:nextjs-app" in followup_insert["tags"]

    def test_bootstrap_task_pack_carries_project_type_and_policy(self):
        client = _mock_client(
            insert_data=[
                [{"id": "proj-new", "title": "Portal", "created_at": "2026-01-01", "github_repo": None}],
                [{"id": "task-bootstrap-10", "title": "Bootstrap project workspace for Portal", "status": "approved", "tags": ["project-bootstrap", "template:nextjs-app", "project-type:web-app", "bootstrap-policy:strict", "bootstrap-architect:chatgpt-codex", "bootstrap-plan-strategy:fallback-rule-based"], "created_from": "project-bootstrap"}],
                [{"id": "task-bootstrap-11", "title": "Validate bootstrap workspace for Portal", "status": "approved", "tags": ["project-bootstrap", "bootstrap-validation", "template:nextjs-app", "project-type:web-app", "bootstrap-policy:strict", "bootstrap-architect:chatgpt-codex", "bootstrap-plan-strategy:fallback-rule-based"], "created_from": "project-bootstrap-validation", "blocked_by": ["task-bootstrap-10"]}],
                [{"id": "task-bootstrap-12", "title": "Capture bootstrap architecture baseline for Portal", "status": "approved", "tags": ["project-bootstrap", "bootstrap-architecture", "template:nextjs-app", "project-type:web-app", "bootstrap-policy:strict", "bootstrap-architect:chatgpt-codex", "bootstrap-plan-strategy:fallback-rule-based"], "created_from": "project-bootstrap-architecture", "blocked_by": ["task-bootstrap-11"]}],
                [{"id": "task-bootstrap-13", "title": "Seed initial follow-up plan for Portal", "status": "approved", "tags": ["project-bootstrap", "bootstrap-followup", "template:nextjs-app", "project-type:web-app", "bootstrap-policy:strict", "bootstrap-architect:chatgpt-codex", "bootstrap-plan-strategy:fallback-rule-based"], "created_from": "project-bootstrap-followup", "blocked_by": ["task-bootstrap-12"]}],
            ]
        )
        service = ProjectService(supabase_client=client)

        ok, result = service.create_project(
            title="Portal",
            bootstrap_template="nextjs-app",
            project_type="web-app",
            bootstrap_policy="strict",
            bootstrap_architect_provider="chatgpt-codex",
            bootstrap_architect_model="gpt-5.4",
        )

        assert ok is True
        scaffold_insert = client._main_table.insert.call_args_list[1][0][0]
        validation_insert = client._main_table.insert.call_args_list[2][0][0]
        architecture_insert = client._main_table.insert.call_args_list[3][0][0]
        followup_insert = client._main_table.insert.call_args_list[4][0][0]
        assert "Project type: web-app" in scaffold_insert["execution_prompt"]
        assert "Bootstrap policy: strict" in scaffold_insert["execution_prompt"]
        assert "project-type:web-app" in scaffold_insert["tags"]
        assert "bootstrap-policy:strict" in validation_insert["tags"]
        assert "bootstrap-architect:chatgpt-codex" in scaffold_insert["tags"]
        assert "bootstrap-plan-strategy:fallback-rule-based" in validation_insert["tags"]
        assert "bootstrap-architecture" in architecture_insert["tags"]
        assert architecture_insert["blocked_by"] == ["task-bootstrap-11"]
        assert "project-type:web-app" in followup_insert["tags"]
        assert followup_insert["blocked_by"] == ["task-bootstrap-12"]
        assert result["project"]["project_type"] == "web-app"
        assert result["project"]["bootstrap_policy"] == "strict"
        assert result["project"]["bootstrap_architect_provider"] == "chatgpt-codex"
        assert result["project"]["bootstrap_architect_model"] == "gpt-5.4"

    def test_bootstrap_architect_backlog_items_materialize_after_followup(self, monkeypatch):
        client = _mock_client(
            insert_data=[
                [{"id": "proj-new", "title": "Portal", "created_at": "2026-01-01", "github_repo": None}],
                [{"id": "task-bootstrap-10", "title": "Bootstrap project workspace for Portal", "status": "approved", "tags": ["project-bootstrap"], "created_from": "project-bootstrap"}],
                [{"id": "task-bootstrap-11", "title": "Validate bootstrap workspace for Portal", "status": "approved", "tags": ["project-bootstrap", "bootstrap-validation"], "created_from": "project-bootstrap-validation", "blocked_by": ["task-bootstrap-10"]}],
                [{"id": "task-bootstrap-12", "title": "Seed initial follow-up plan for Portal", "status": "approved", "tags": ["project-bootstrap", "bootstrap-followup"], "created_from": "project-bootstrap-followup", "blocked_by": ["task-bootstrap-11"]}],
                [{"id": "task-bootstrap-13", "title": "Implement API contracts", "status": "approved", "tags": ["project-bootstrap", "bootstrap-derived-backlog", "api"], "created_from": "project-bootstrap-derived-1", "blocked_by": ["task-bootstrap-12"]}],
            ]
        )
        service = ProjectService(supabase_client=client)

        class _FakeArchitect:
            provider_key = "fake-architect"

            def create_plan(self, *, context, requested_provider, model=None):
                return BootstrapPlanEnvelope(
                    requested_provider=requested_provider,
                    resolved_provider="fake-architect",
                    strategy="provider-generated",
                    model=model,
                    items=[
                        BootstrapPlanItem(
                            key="scaffold",
                            title="Bootstrap project workspace for Portal",
                            description="Scaffold",
                            task_type="feature",
                            priority="high",
                            complexity="simple",
                            max_retries=2,
                            created_from="project-bootstrap",
                            execution_prompt="Scaffold",
                            acceptance_criteria=["A"],
                            tags=["project-bootstrap"],
                            blocked_on_key=None,
                        ),
                        BootstrapPlanItem(
                            key="validation",
                            title="Validate bootstrap workspace for Portal",
                            description="Validate",
                            task_type="test",
                            priority="high",
                            complexity="simple",
                            max_retries=1,
                            created_from="project-bootstrap-validation",
                            execution_prompt="Validate",
                            acceptance_criteria=["B"],
                            tags=["project-bootstrap", "bootstrap-validation"],
                            blocked_on_key="scaffold",
                        ),
                        BootstrapPlanItem(
                            key="followup",
                            title="Seed initial follow-up plan for Portal",
                            description="Follow up",
                            task_type="improvement",
                            priority="medium",
                            complexity="simple",
                            max_retries=1,
                            created_from="project-bootstrap-followup",
                            execution_prompt="Follow up",
                            acceptance_criteria=["C"],
                            tags=["project-bootstrap", "bootstrap-followup"],
                            blocked_on_key="validation",
                        ),
                    ],
                    backlog_items=[
                        BootstrapPlanItem(
                            key="api-contracts",
                            title="Implement API contracts",
                            description="Create API contracts",
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

        monkeypatch.setattr(bootstrap_task_template_module, "resolve_bootstrap_architect", lambda _provider: _FakeArchitect())

        ok, result = service.create_project(
            title="Portal",
            bootstrap_architect_provider="chatgpt-codex",
        )

        assert ok is True
        derived_insert = client._main_table.insert.call_args_list[4][0][0]
        assert derived_insert["title"] == "Implement API contracts"
        assert derived_insert["blocked_by"] == ["task-bootstrap-12"]
        assert "bootstrap-derived-backlog" in derived_insert["tags"]
        assert result["project"]["bootstrap_tasks"][-1]["id"] == "task-bootstrap-13"


# ---------------------------------------------------------------------------
# Tests: update_project with office config
# ---------------------------------------------------------------------------


class TestUpdateProjectWithOfficeConfig:
    def test_updates_office_fields(self):
        client = _mock_client(update_data=[_make_project()])
        service = ProjectService(supabase_client=client)

        ok, result = service.update_project("proj-001", {
            "source_app": "new-app",
            "layout_id": "central-hub",
            "office_settings": {"mockMode": False},
        })

        assert ok is True
        update_call = client._main_table.update.call_args[0][0]
        assert update_call["source_app"] == "new-app"
        assert update_call["layout_id"] == "central-hub"
        assert update_call["office_settings"] == {"mockMode": False}

    def test_mirrors_legacy_policy_fields_into_engine_policies_on_update(self):
        project = _make_project(
            office_settings={"preferred_runner": "codex-cli", "isolation_mode": "git-worktree"},
            team_lead_config={"review_mode": "multi-perspective"},
        )
        client = _mock_client(update_data=[project])
        service = ProjectService(supabase_client=client)

        ok, _ = service.update_project(
            "proj-001",
            {
                "office_settings": {"preferred_runner": "codex-cli", "isolation_mode": "git-worktree"},
                "team_lead_config": {"review_mode": "multi-perspective"},
            },
        )

        assert ok is True
        upsert_payload = client._policy_table.upsert.call_args[0][0]
        assert upsert_payload["project_id"] == "proj-001"
        assert upsert_payload["model_routing"] == {"default_runner": "codex-cli"}
        assert upsert_payload["review_policy"] == {"review_mode": "multi-perspective"}
        assert upsert_payload["isolation_policy"] == {"worktree_mode": "isolated"}


# ---------------------------------------------------------------------------
# Tests: list_projects includes office fields
# ---------------------------------------------------------------------------


class TestListProjectsWithOfficeFields:
    def test_include_content_has_office_fields(self):
        project = _make_project()
        client = _mock_client(select_data=[project])
        service = ProjectService(supabase_client=client)

        ok, result = service.list_projects(include_content=True)

        assert ok is True
        p = result["projects"][0]
        assert p["source_app"] == "test-app"
        assert p["layout_id"] == "executive-suite"
        assert len(p["team_config"]) == 1
        assert p["director_config"]["name"] == "Boss"

    def test_lightweight_has_office_fields(self):
        project = _make_project()
        client = _mock_client(select_data=[project])
        service = ProjectService(supabase_client=client)

        ok, result = service.list_projects(include_content=False)

        assert ok is True
        p = result["projects"][0]
        assert p["source_app"] == "test-app"
        assert p["layout_id"] == "executive-suite"


# ---------------------------------------------------------------------------
# Tests: list_office_configs
# ---------------------------------------------------------------------------


class TestListOfficeConfigs:
    def test_returns_office_only_fields(self):
        project = _make_project()
        client = _mock_client(select_data=[project])
        service = ProjectService(supabase_client=client)

        ok, result = service.list_office_configs()

        assert ok is True
        assert result["count"] == 1
        p = result["projects"][0]
        assert p["id"] == "proj-001"
        assert p["title"] == "Test Project"
        assert p["source_app"] == "test-app"
        assert p["layout_id"] == "executive-suite"
        assert p["team_config"] == project["team_config"]
        assert p["director_config"] == project["director_config"]
        assert p["team_lead_config"] == project["team_lead_config"]
        assert p["office_settings"] == project["office_settings"]
        # Should NOT have docs/features/data
        assert "docs" not in p
        assert "features" not in p

    def test_empty_projects(self):
        client = _mock_client(select_data=[])
        service = ProjectService(supabase_client=client)

        ok, result = service.list_office_configs()

        assert ok is True
        assert result["count"] == 0
        assert result["projects"] == []

    def test_defaults_for_missing_fields(self):
        project = {"id": "proj-002", "title": "Bare Project"}
        client = _mock_client(select_data=[project])
        service = ProjectService(supabase_client=client)

        ok, result = service.list_office_configs()

        assert ok is True
        p = result["projects"][0]
        assert p["source_app"] is None
        assert p["layout_id"] == "classic"
        assert p["team_config"] == []
        assert p["director_config"] == DEFAULT_DIRECTOR_CONFIG
        assert p["team_lead_config"] == DEFAULT_TEAM_LEAD_CONFIG
        assert p["office_settings"] == {}

    def test_handles_db_error(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB down")
        service = ProjectService(supabase_client=client)

        ok, result = service.list_office_configs()

        assert ok is False
        assert "error" in result


# ---------------------------------------------------------------------------
# Tests: _extract_office_fields
# ---------------------------------------------------------------------------


class TestExtractOfficeFields:
    def test_extracts_all_fields(self):
        project = _make_project()
        fields = ProjectService._extract_office_fields(project)

        assert fields["source_app"] == "test-app"
        assert fields["layout_id"] == "executive-suite"
        assert len(fields["team_config"]) == 1
        assert fields["director_config"]["name"] == "Boss"
        assert fields["team_lead_config"]["name"] == "TL"
        assert fields["office_settings"]["mockMode"] is True

    def test_defaults_for_empty_project(self):
        fields = ProjectService._extract_office_fields({})

        assert fields["source_app"] is None
        assert fields["layout_id"] == "classic"
        assert fields["team_config"] == []
        assert fields["director_config"] == DEFAULT_DIRECTOR_CONFIG
        assert fields["team_lead_config"] == DEFAULT_TEAM_LEAD_CONFIG
        assert fields["office_settings"] == {}


# ---------------------------------------------------------------------------
# Tests: default team config structure
# ---------------------------------------------------------------------------


class TestDefaultTeamConfig:
    def test_default_team_has_5_agents(self):
        assert len(DEFAULT_TEAM_CONFIG) == 5

    def test_each_agent_has_required_fields(self):
        required = {"slotIndex", "agentId", "name", "role", "color", "decorations"}
        for agent in DEFAULT_TEAM_CONFIG:
            assert required.issubset(set(agent.keys())), f"Agent {agent.get('agentId')} missing fields"

    def test_slot_indexes_are_sequential(self):
        indexes = [a["slotIndex"] for a in DEFAULT_TEAM_CONFIG]
        assert indexes == list(range(5))

    def test_agent_ids_are_unique(self):
        ids = [a["agentId"] for a in DEFAULT_TEAM_CONFIG]
        assert len(ids) == len(set(ids))
