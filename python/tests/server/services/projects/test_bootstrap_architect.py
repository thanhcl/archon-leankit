"""Tests for bootstrap architect-provider planning."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.server.services.projects import bootstrap_architect as architect_module
from src.server.services.projects.bootstrap_architect import create_bootstrap_plan_envelope
from src.server.services.projects.bootstrap_planner import build_bootstrap_context


@pytest.mark.asyncio
async def test_rule_based_bootstrap_architect_returns_local_plan():
    context = build_bootstrap_context(
        project_title="Starter",
        project_description="A starter project",
        bootstrap_template="default-app",
        project_type="web-app",
        bootstrap_policy="standard",
    )

    envelope = await create_bootstrap_plan_envelope(
        context=context,
        requested_provider="rule-based",
        model=None,
    )

    assert envelope.resolved_provider == "rule-based"
    assert envelope.strategy == "rule-based"
    assert envelope.items[0].key == "scaffold"


@pytest.mark.asyncio
async def test_provider_bootstrap_architect_can_return_generated_plan(monkeypatch):
    context = build_bootstrap_context(
        project_title="Starter",
        project_description="A starter project",
        bootstrap_template="default-app",
        project_type="web-app",
        bootstrap_policy="standard",
    )

    async def fake_get_active_provider(_service_type: str):
        return {"provider": "openai", "chat_model": "gpt-5.4"}

    class _FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=
                                '{"items":['
                                '{"key":"scaffold","title":"Plan scaffold","description":"Scaffold it",'
                                '"task_type":"feature","priority":"high","complexity":"simple","max_retries":2,'
                                '"created_from":"project-bootstrap","execution_prompt":"Do scaffold",'
                                '"acceptance_criteria":["A"],"tags":["project-bootstrap"],"blocked_on_key":null},'
                                '{"key":"validation","title":"Plan validation","description":"Validate it",'
                                '"task_type":"test","priority":"high","complexity":"simple","max_retries":1,'
                                '"created_from":"project-bootstrap-validation","execution_prompt":"Do validation",'
                                '"acceptance_criteria":["A2"],"tags":["project-bootstrap","bootstrap-validation"],"blocked_on_key":"scaffold"},'
                                '{"key":"followup","title":"Plan follow-up","description":"Follow up",'
                                '"task_type":"improvement","priority":"medium","complexity":"simple","max_retries":1,'
                                '"created_from":"project-bootstrap-followup","execution_prompt":"Do follow-up",'
                                '"acceptance_criteria":["B"],"tags":["project-bootstrap","bootstrap-followup"],'
                                '"blocked_on_key":"validation"}'
                                '],"backlog_items":['
                                '{"key":"api-contracts","title":"Implement API contracts","description":"Create baseline API contract module",'
                                '"task_type":"feature","priority":"medium","complexity":"moderate","max_retries":2,'
                                '"created_from":"backlog-seed","execution_prompt":"Build the API contracts module",'
                                '"acceptance_criteria":["Contracts created"],"tags":["api"],"blocked_on_key":null}'
                                "]}"
                        )
                    )
                ]
            )

    class _FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    @asynccontextmanager
    async def fake_get_llm_client(provider=None):
        assert provider == "openai"
        yield _FakeClient()

    monkeypatch.setattr(architect_module.credential_service, "get_active_provider", fake_get_active_provider)
    monkeypatch.setattr(architect_module, "get_llm_client", fake_get_llm_client)

    envelope = await create_bootstrap_plan_envelope(
        context=context,
        requested_provider="chatgpt-codex",
        model=None,
    )

    assert envelope.resolved_provider == "chatgpt-codex"
    assert envelope.strategy == "provider-generated"
    assert envelope.model == "gpt-5.4"
    assert envelope.items[0].title == "Plan scaffold"
    assert envelope.items[2].blocked_on_key == "validation"
    assert len(envelope.backlog_items) == 1
    assert envelope.backlog_items[0].blocked_on_key == "followup"
    assert "bootstrap-derived-backlog" in envelope.backlog_items[0].tags
    assert envelope.backlog_items[0].created_from.startswith("project-bootstrap-derived")


@pytest.mark.asyncio
async def test_supported_project_types_default_to_provider_generated_plans(monkeypatch):
    context = build_bootstrap_context(
        project_title="Starter",
        project_description="A starter project",
        bootstrap_template="default-app",
        project_type="api-service",
        bootstrap_policy="standard",
    )

    async def fake_get_active_provider(_service_type: str):
        return {"provider": "openai", "chat_model": "gpt-5.4"}

    class _FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=
                                '{"items":['
                                '{"key":"scaffold","title":"Plan scaffold","description":"Scaffold it",'
                                '"task_type":"feature","priority":"high","complexity":"simple","max_retries":2,'
                                '"created_from":"project-bootstrap","execution_prompt":"Do scaffold",'
                                '"acceptance_criteria":["A"],"tags":["project-bootstrap"],"blocked_on_key":null},'
                                '{"key":"validation","title":"Plan validation","description":"Validate it",'
                                '"task_type":"test","priority":"high","complexity":"simple","max_retries":1,'
                                '"created_from":"project-bootstrap-validation","execution_prompt":"Do validation",'
                                '"acceptance_criteria":["A2"],"tags":["project-bootstrap","bootstrap-validation"],"blocked_on_key":"scaffold"},'
                                '{"key":"followup","title":"Plan follow-up","description":"Follow up",'
                                '"task_type":"improvement","priority":"medium","complexity":"simple","max_retries":1,'
                                '"created_from":"project-bootstrap-followup","execution_prompt":"Do follow-up",'
                                '"acceptance_criteria":["B"],"tags":["project-bootstrap","bootstrap-followup"],'
                                '"blocked_on_key":"validation"}'
                                "]}"
                        )
                    )
                ]
            )

    class _FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    @asynccontextmanager
    async def fake_get_llm_client(provider=None):
        assert provider == "openai"
        yield _FakeClient()

    monkeypatch.setattr(architect_module.credential_service, "get_active_provider", fake_get_active_provider)
    monkeypatch.setattr(architect_module, "get_llm_client", fake_get_llm_client)

    envelope = await create_bootstrap_plan_envelope(
        context=context,
        requested_provider=None,
        model=None,
    )

    assert envelope.requested_provider == "chatgpt-codex"
    assert envelope.resolved_provider == "chatgpt-codex"
    assert envelope.strategy == "provider-generated-default"
    assert envelope.model == "gpt-5.4"
