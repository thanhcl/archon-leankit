"""Tests for Virtual Office config on archon_projects — create, update, list, defaults."""

from unittest.mock import MagicMock

import pytest

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

    client.table.return_value = table
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
            insert_data=[{"id": "proj-new", "title": "KMS", "created_at": "2026-01-01", "github_repo": None}]
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
        insert_call = client.table.return_value.insert.call_args[0][0]
        assert insert_call["source_app"] == "sesb-kms"
        assert insert_call["layout_id"] == "executive-suite"
        assert len(insert_call["team_config"]) == 1
        assert insert_call["director_config"]["name"] == "Thanh"

    def test_creates_with_default_team_when_not_specified(self):
        client = _mock_client(
            insert_data=[{"id": "proj-new", "title": "My Proj", "created_at": "2026-01-01", "github_repo": None}]
        )
        service = ProjectService(supabase_client=client)

        ok, result = service.create_project(title="My Proj")

        assert ok is True
        insert_call = client.table.return_value.insert.call_args[0][0]
        assert insert_call["team_config"] == DEFAULT_TEAM_CONFIG
        assert insert_call["director_config"] == DEFAULT_DIRECTOR_CONFIG
        assert insert_call["team_lead_config"] == DEFAULT_TEAM_LEAD_CONFIG

    def test_explicit_empty_team_overrides_default(self):
        client = _mock_client(
            insert_data=[{"id": "proj-new", "title": "Empty", "created_at": "2026-01-01", "github_repo": None}]
        )
        service = ProjectService(supabase_client=client)

        ok, _ = service.create_project(title="Empty", team_config=[])

        insert_call = client.table.return_value.insert.call_args[0][0]
        assert insert_call["team_config"] == []


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
        update_call = client.table.return_value.update.call_args[0][0]
        assert update_call["source_app"] == "new-app"
        assert update_call["layout_id"] == "central-hub"
        assert update_call["office_settings"] == {"mockMode": False}


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
