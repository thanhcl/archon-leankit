"""Tests for AgentDefinitionService."""

from unittest.mock import MagicMock, patch

import pytest

from src.server.services.projects.agent_definition_service import AgentDefinitionService


@pytest.fixture
def mock_supabase():
    return MagicMock()


@pytest.fixture
def service(mock_supabase):
    return AgentDefinitionService(supabase_client=mock_supabase)


# ---------------------------------------------------------------------------
# list_definitions
# ---------------------------------------------------------------------------


def test_list_definitions_active_only(service, mock_supabase):
    rows = [{"id": "abc", "slug": "backend-dev", "name": "Backend Dev", "is_active": True}]
    chain = mock_supabase.table.return_value.select.return_value.order.return_value.eq.return_value
    chain.execute.return_value.data = rows

    ok, result = service.list_definitions(include_inactive=False)

    assert ok is True
    assert result["total_count"] == 1
    assert result["agent_definitions"] == rows
    mock_supabase.table.return_value.select.return_value.order.return_value.eq.assert_called_once_with("is_active", True)


def test_list_definitions_include_inactive(service, mock_supabase):
    rows = [{"id": "abc", "slug": "backend-dev", "is_active": True}, {"id": "def", "slug": "retired", "is_active": False}]
    chain = mock_supabase.table.return_value.select.return_value.order.return_value
    chain.execute.return_value.data = rows

    ok, result = service.list_definitions(include_inactive=True)

    assert ok is True
    assert result["total_count"] == 2


def test_list_definitions_db_error(service, mock_supabase):
    mock_supabase.table.return_value.select.return_value.order.return_value.eq.return_value.execute.side_effect = Exception("db down")

    ok, result = service.list_definitions()

    assert ok is False
    assert "db down" in result["error"]


# ---------------------------------------------------------------------------
# get_definition
# ---------------------------------------------------------------------------


def test_get_definition_found(service, mock_supabase):
    row = {"id": "uuid-1", "slug": "qa-engineer", "name": "QA Engineer"}
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = row

    ok, result = service.get_definition("uuid-1")

    assert ok is True
    assert result["agent_definition"] == row


def test_get_definition_not_found(service, mock_supabase):
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = None

    ok, result = service.get_definition("missing-id")

    assert ok is False
    assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# get_definition_by_slug
# ---------------------------------------------------------------------------


def test_get_definition_by_slug_found(service, mock_supabase):
    row = {"id": "uuid-2", "slug": "architect", "name": "Architect"}
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = row

    ok, result = service.get_definition_by_slug("architect")

    assert ok is True
    assert result["agent_definition"]["slug"] == "architect"


def test_get_definition_by_slug_not_found(service, mock_supabase):
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = None

    ok, result = service.get_definition_by_slug("ghost")

    assert ok is False
    assert "ghost" in result["error"]


# ---------------------------------------------------------------------------
# create_definition
# ---------------------------------------------------------------------------


def test_create_definition_success(service, mock_supabase):
    created = {"id": "new-uuid", "slug": "backend-dev", "name": "Backend Developer"}
    mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [created]

    ok, result = service.create_definition(
        slug="backend-dev",
        name="Backend Developer",
        capabilities=["python", "fastapi"],
        model_preferences={"preferred_model": "claude-opus-4-6"},
    )

    assert ok is True
    assert result["agent_definition"]["slug"] == "backend-dev"
    inserted = mock_supabase.table.return_value.insert.call_args[0][0]
    assert inserted["slug"] == "backend-dev"
    assert inserted["capabilities"] == ["python", "fastapi"]
    assert inserted["model_preferences"] == {"preferred_model": "claude-opus-4-6"}


def test_create_definition_with_prompt_template(service, mock_supabase):
    template = "You are an expert {task_title} specialist."
    created = {"id": "new-uuid", "slug": "specialist", "name": "Specialist", "prompt_template": template}
    mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [created]

    ok, result = service.create_definition(slug="specialist", name="Specialist", prompt_template=template)

    assert ok is True
    inserted = mock_supabase.table.return_value.insert.call_args[0][0]
    assert inserted["prompt_template"] == template


def test_create_definition_no_data_returned(service, mock_supabase):
    mock_supabase.table.return_value.insert.return_value.execute.return_value.data = []

    ok, result = service.create_definition(slug="x", name="X")

    assert ok is False
    assert "no data" in result["error"].lower()


# ---------------------------------------------------------------------------
# update_definition
# ---------------------------------------------------------------------------


def test_update_definition_success(service, mock_supabase):
    updated = {"id": "uuid-3", "slug": "backend-dev", "name": "Updated Name"}
    mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [updated]

    ok, result = service.update_definition("uuid-3", name="Updated Name")

    assert ok is True
    assert result["agent_definition"]["name"] == "Updated Name"


def test_update_definition_no_valid_fields(service, mock_supabase):
    ok, result = service.update_definition("uuid-3")

    assert ok is False
    assert "no valid fields" in result["error"].lower()


def test_update_definition_not_found(service, mock_supabase):
    mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []

    ok, result = service.update_definition("missing", name="X")

    assert ok is False
    assert "not found" in result["error"].lower()


# ---------------------------------------------------------------------------
# delete_definition
# ---------------------------------------------------------------------------


def test_delete_definition_success(service, mock_supabase):
    mock_supabase.table.return_value.delete.return_value.eq.return_value.execute.return_value.data = [{"id": "uuid-4"}]

    ok, result = service.delete_definition("uuid-4")

    assert ok is True
    assert "deleted" in result["message"]


def test_delete_definition_not_found(service, mock_supabase):
    mock_supabase.table.return_value.delete.return_value.eq.return_value.execute.return_value.data = []

    ok, result = service.delete_definition("ghost-id")

    assert ok is False
    assert "not found" in result["error"]
