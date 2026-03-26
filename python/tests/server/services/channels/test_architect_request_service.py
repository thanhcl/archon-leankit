"""Tests for architect-request service used by external channels."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.server.services.channels import architect_request_service as architect_module
from src.server.services.channels.architect_request_service import ArchitectRequestService


@pytest.mark.asyncio
async def test_architect_request_service_falls_back_to_rule_based():
    service = ArchitectRequestService()
    result = await service.create_plan(
        title="Plan auth hardening",
        summary="Need a plan for auth hardening",
        project_id="proj-1",
        requested_provider="rule-based",
    )

    assert result["resolved_provider"] == "rule-based"
    assert result["recommended_materialization"] == "task"
    assert result["clarifying_questions"] == []
    assert result["suggested_tasks"][0]["title"] == "Plan auth hardening"


@pytest.mark.asyncio
async def test_architect_request_service_adds_clarifying_questions_without_project():
    service = ArchitectRequestService()
    result = await service.create_plan(
        title="Plan this request",
        summary="Need help planning from voice",
        project_id=None,
        input_modality="voice",
        requested_provider="rule-based",
    )

    assert result["recommended_materialization"] == "none"
    assert "Which project should this request target?" in result["clarifying_questions"]


@pytest.mark.asyncio
async def test_architect_request_service_resolves_pending_clarifications_when_answers_arrive():
    service = ArchitectRequestService()
    result = await service.create_plan(
        title="Plan auth hardening",
        summary="Need a concrete task plan",
        project_id="proj-1",
        input_modality="voice",
        clarification_answers=["Create the tasks immediately."],
        sequence_context={
            "sequence_id": "seq-77",
            "history_count": 2,
            "pending_clarifying_questions": [
                "Do you want LeanKit to decompose this into tasks immediately, or only produce a plan?",
            ],
            "pending_clarifying_request_id": "ext-open-prev",
        },
        requested_provider="rule-based",
    )

    assert result["recommended_materialization"] == "task"
    assert result["clarifying_questions"] == []


@pytest.mark.asyncio
async def test_architect_request_service_can_use_provider(monkeypatch):
    async def fake_get_active_provider(_service_type: str):
        return {"provider": "openai", "chat_model": "gpt-5.4"}

    class _FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"summary":"Create a delivery task.","recommended_materialization":"task","clarifying_questions":["Should I also add rollout tasks?"],"suggested_tasks":[{"title":"Implement auth hardening","summary":"Create implementation task","task_type":"feature","priority":"high","complexity":"complex","tags":["security"]}],"suggested_approval":null}'
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

    service = ArchitectRequestService()
    result = await service.create_plan(
        title="Plan auth hardening",
        summary="Need a plan for auth hardening",
        project_id="proj-1",
        requested_provider="chatgpt-codex",
    )

    assert result["resolved_provider"] == "chatgpt-codex"
    assert result["strategy"] == "provider-generated"
    assert result["model"] == "gpt-5.4"
    assert result["clarifying_questions"] == ["Should I also add rollout tasks?"]
    assert result["suggested_tasks"][0]["title"] == "Implement auth hardening"
