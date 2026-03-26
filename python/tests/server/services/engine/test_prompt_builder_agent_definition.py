"""Tests for PromptBuilder agent_definition integration."""

import pytest

from src.server.services.engine.prompt_builder import PromptBuilder


@pytest.fixture
def builder():
    return PromptBuilder(compress=True)


# ---------------------------------------------------------------------------
# _format_agent_definition
# ---------------------------------------------------------------------------


def test_format_agent_definition_none():
    assert PromptBuilder._format_agent_definition(None) is None


def test_format_agent_definition_empty_dict():
    assert PromptBuilder._format_agent_definition({}) is None


def test_format_agent_definition_with_template():
    defn = {
        "name": "Backend Developer",
        "prompt_template": "You are an expert Python/FastAPI developer.",
        "capabilities": ["python", "fastapi"],
    }
    result = PromptBuilder._format_agent_definition(defn)

    assert result is not None
    assert "## Agent Role: Backend Developer" in result
    assert "You are an expert Python/FastAPI developer." in result
    # Template takes precedence; capabilities list not shown separately
    assert "python" not in result.split("prompt_template")[0]


def test_format_agent_definition_without_template_uses_capabilities():
    defn = {
        "name": "QA Engineer",
        "capabilities": ["testing", "playwright", "vitest"],
    }
    result = PromptBuilder._format_agent_definition(defn)

    assert result is not None
    assert "## Agent Role: QA Engineer" in result
    assert "testing" in result
    assert "playwright" in result


def test_format_agent_definition_name_only():
    defn = {"name": "Architect"}
    result = PromptBuilder._format_agent_definition(defn)

    assert result is not None
    assert "## Agent Role: Architect" in result


def test_format_agent_definition_no_name_no_template_no_caps():
    # Only id/slug, nothing useful for rendering
    defn = {"id": "uuid-1", "slug": "x"}
    assert PromptBuilder._format_agent_definition(defn) is None


# ---------------------------------------------------------------------------
# build() with agent_definition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_injects_agent_definition():
    builder = PromptBuilder(compress=True)
    # Patch async _fetch_kb_context to avoid real network
    builder._fetch_kb_context = lambda task: []  # type: ignore[method-assign]

    task = {
        "id": "t-1",
        "title": "Implement auth",
        "description": "Add JWT authentication",
        "project_id": "p-1",
        "priority": "high",
        "assignee": "Agent",
    }
    agent_def = {
        "name": "Security Engineer",
        "prompt_template": "You specialize in application security and auth patterns.",
        "capabilities": ["jwt", "oauth"],
    }

    # build() is async; _fetch_kb_context is normally async
    import asyncio

    async def mock_kb(t):
        return []

    builder._fetch_kb_context = mock_kb  # type: ignore[method-assign]

    prompt, stats = await builder.build(task, agent_definition=agent_def)

    assert "## Agent Role: Security Engineer" in prompt
    assert "You specialize in application security and auth patterns." in prompt


@pytest.mark.asyncio
async def test_build_without_agent_definition_no_role_section():
    builder = PromptBuilder(compress=True)

    async def mock_kb(t):
        return []

    builder._fetch_kb_context = mock_kb  # type: ignore[method-assign]

    task = {"id": "t-2", "title": "Fix bug", "description": "Fix null pointer", "project_id": "p-1"}

    prompt, _ = await builder.build(task)

    assert "## Agent Role" not in prompt
