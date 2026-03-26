"""Tests for the architect-provider boundary contract."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.server.models.api_contracts import (
    ArchitectProposedTask,
    ArchitectRequest,
    ArchitectResponse,
    ArchitectRiskLevel,
    validate_architect_response,
)
from src.server.services.projects import architect_provider as provider_module
from src.server.services.projects.architect_provider import (
    LLMArchitectProvider,
    RuleBasedArchitectProvider,
    normalize_architect_provider,
    resolve_architect_provider,
    run_architect_plan,
)


# ── Schema tests ──────────────────────────────────────────────────────────────


def test_architect_request_schema_minimal():
    req = ArchitectRequest(title="Add auth", description="Implement JWT auth")
    assert req.title == "Add auth"
    assert req.project_id is None
    assert req.clarification_answers == []


def test_architect_proposed_task_defaults():
    task = ArchitectProposedTask(title="Setup DB", description="Create schema")
    assert task.risk_level == ArchitectRiskLevel.LOW
    assert task.acceptance_criteria == []
    assert task.dependencies == []
    assert task.suggested_decomposition == []


def test_architect_response_full():
    resp = ArchitectResponse(
        requested_provider="rule-based",
        resolved_provider="rule-based",
        strategy="rule-based",
        summary="Plan ready.",
        risk_level=ArchitectRiskLevel.MEDIUM,
        proposed_tasks=[
            ArchitectProposedTask(
                title="T1",
                description="Do T1",
                acceptance_criteria=["All tests pass"],
                dependencies=["T0"],
                risk_level=ArchitectRiskLevel.HIGH,
                suggested_decomposition=["Sub-T1a", "Sub-T1b"],
            )
        ],
    )
    assert resp.risk_level == ArchitectRiskLevel.MEDIUM
    assert resp.proposed_tasks[0].dependencies == ["T0"]
    assert resp.proposed_tasks[0].suggested_decomposition == ["Sub-T1a", "Sub-T1b"]


def test_validate_architect_response_raises_on_missing_required_fields():
    with pytest.raises(ValidationError):
        validate_architect_response({"summary": "oops"})


def test_validate_architect_response_accepts_valid_data():
    data = {
        "requested_provider": "rule-based",
        "resolved_provider": "rule-based",
        "strategy": "rule-based",
        "summary": "Plan ready",
        "proposed_tasks": [],
    }
    resp = validate_architect_response(data)
    assert isinstance(resp, ArchitectResponse)
    assert resp.risk_level == ArchitectRiskLevel.LOW  # default


# ── normalize_architect_provider ─────────────────────────────────────────────


def test_normalize_returns_rule_based_for_none():
    assert normalize_architect_provider(None) == "rule-based"


def test_normalize_returns_rule_based_for_unknown():
    assert normalize_architect_provider("unknown-provider") == "rule-based"


def test_normalize_passes_through_supported():
    assert normalize_architect_provider("chatgpt-codex") == "chatgpt-codex"
    assert normalize_architect_provider("claude-chat") == "claude-chat"
    assert normalize_architect_provider("rule-based") == "rule-based"


# ── resolve_architect_provider ────────────────────────────────────────────────


def test_resolve_returns_rule_based_provider():
    p = resolve_architect_provider(None)
    assert isinstance(p, RuleBasedArchitectProvider)


def test_resolve_returns_llm_provider_for_chatgpt_codex():
    p = resolve_architect_provider("chatgpt-codex")
    assert isinstance(p, LLMArchitectProvider)
    assert p.provider_key == "chatgpt-codex"


def test_resolve_returns_llm_provider_for_claude_chat():
    p = resolve_architect_provider("claude-chat")
    assert isinstance(p, LLMArchitectProvider)
    assert p.provider_key == "claude-chat"


# ── RuleBasedArchitectProvider ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rule_based_with_project_id_returns_task():
    provider = RuleBasedArchitectProvider()
    request = ArchitectRequest(
        title="Harden auth",
        description="Implement MFA and rate limiting",
        project_id="proj-1",
    )
    response = await provider.plan(request)

    assert isinstance(response, ArchitectResponse)
    assert response.resolved_provider == "rule-based"
    assert response.strategy == "rule-based"
    assert response.recommended_materialization == "task"
    assert len(response.proposed_tasks) == 1
    assert response.proposed_tasks[0].title == "Harden auth"
    assert response.clarifying_questions == []


@pytest.mark.asyncio
async def test_rule_based_without_project_adds_clarifying_question():
    provider = RuleBasedArchitectProvider()
    request = ArchitectRequest(
        title="Refactor DB layer",
        description="Simplify query patterns",
    )
    response = await provider.plan(request)

    assert response.recommended_materialization == "none"
    assert "Which project should this request target?" in response.clarifying_questions
    assert response.proposed_tasks == []


@pytest.mark.asyncio
async def test_rule_based_clears_questions_when_answers_present():
    provider = RuleBasedArchitectProvider()
    request = ArchitectRequest(
        title="Add caching",
        description="Redis caching for hot paths",
        project_id="proj-2",
        clarification_answers=["Yes, proceed with task creation."],
        payload={"pending_clarifying_questions": ["Should tasks be created?"]},
    )
    response = await provider.plan(request)

    assert response.recommended_materialization == "task"
    assert response.clarifying_questions == []


# ── LLMArchitectProvider ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_provider_returns_validated_response(monkeypatch):
    llm_payload = {
        "summary": "Auth hardening plan",
        "risk_level": "high",
        "recommended_materialization": "task",
        "clarifying_questions": [],
        "proposed_tasks": [
            {
                "title": "Implement MFA",
                "description": "Add TOTP-based MFA",
                "task_type": "feature",
                "priority": "high",
                "complexity": "complex",
                "risk_level": "high",
                "acceptance_criteria": ["MFA enforced on all logins", "Recovery codes generated"],
                "dependencies": [],
                "tags": ["security", "auth"],
                "suggested_decomposition": ["Setup TOTP library", "Add MFA UI"],
            }
        ],
    }

    async def fake_get_active_provider(_service_type: str):
        return {"provider": "openai", "chat_model": "gpt-5.4"}

    class _FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(llm_payload)))]
            )

    class _FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    @asynccontextmanager
    async def fake_get_llm_client(provider=None):
        yield _FakeClient()

    monkeypatch.setattr(provider_module.credential_service, "get_active_provider", fake_get_active_provider)
    monkeypatch.setattr(provider_module, "get_llm_client", fake_get_llm_client)

    llm_provider = LLMArchitectProvider("chatgpt-codex")
    request = ArchitectRequest(
        title="Auth hardening",
        description="Harden auth with MFA",
        project_id="proj-1",
    )
    response = await llm_provider.plan(request)

    assert isinstance(response, ArchitectResponse)
    assert response.resolved_provider == "chatgpt-codex"
    assert response.strategy == "provider-generated"
    assert response.risk_level == ArchitectRiskLevel.HIGH
    assert response.proposed_tasks[0].title == "Implement MFA"
    assert response.proposed_tasks[0].acceptance_criteria == ["MFA enforced on all logins", "Recovery codes generated"]
    assert response.proposed_tasks[0].suggested_decomposition == ["Setup TOTP library", "Add MFA UI"]


@pytest.mark.asyncio
async def test_llm_provider_falls_back_on_error(monkeypatch):
    async def fake_get_active_provider(_service_type: str):
        return {"provider": "openai", "chat_model": "gpt-5.4"}

    @asynccontextmanager
    async def fake_get_llm_client(provider=None):
        raise RuntimeError("LLM unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(provider_module.credential_service, "get_active_provider", fake_get_active_provider)
    monkeypatch.setattr(provider_module, "get_llm_client", fake_get_llm_client)

    llm_provider = LLMArchitectProvider("chatgpt-codex")
    request = ArchitectRequest(
        title="Fix bug",
        description="Fix the memory leak",
        project_id="proj-1",
    )
    response = await llm_provider.plan(request)

    assert isinstance(response, ArchitectResponse)
    assert response.requested_provider == "chatgpt-codex"
    assert response.strategy == "fallback-rule-based"
    assert response.resolved_provider == "rule-based"


# ── run_architect_plan ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_architect_plan_uses_rule_based_by_default():
    request = ArchitectRequest(
        title="Add metrics",
        description="Expose Prometheus metrics",
        project_id="proj-3",
    )
    response = await run_architect_plan(request)

    assert isinstance(response, ArchitectResponse)
    assert response.resolved_provider == "rule-based"
    assert response.proposed_tasks[0].title == "Add metrics"


@pytest.mark.asyncio
async def test_run_architect_plan_validates_before_returning():
    """Confirms the boundary enforces contract validation on every response."""
    request = ArchitectRequest(
        title="Test validation",
        description="Ensure output is always ArchitectResponse",
    )
    response = await run_architect_plan(request)
    assert isinstance(response, ArchitectResponse)
    # All required fields must be present
    assert response.requested_provider
    assert response.resolved_provider
    assert response.strategy
    assert response.summary
