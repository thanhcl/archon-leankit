"""Architect-provider boundary for decomposing work into structured task proposals.

All architect providers implement the ArchitectProvider Protocol and return an
ArchitectResponse that is validated against the formal contract before tasks
are created.  Provider-agnostic: identical contract for rule-based, Claude Chat,
and ChatGPT/Codex providers.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from ...config.logfire_config import get_logger
from ...models.api_contracts import (
    ArchitectProposedTask,
    ArchitectRequest,
    ArchitectResponse,
    ArchitectRiskLevel,
    validate_architect_response,
)
from ..credential_service import credential_service
from ..llm_provider_service import get_llm_client

logger = get_logger(__name__)

DEFAULT_ARCHITECT_PROVIDER = "rule-based"
SUPPORTED_ARCHITECT_PROVIDERS = {"rule-based", "chatgpt-codex", "claude-chat"}

_PROVIDER_TO_BACKEND: dict[str, str] = {
    "chatgpt-codex": "openai",
    "claude-chat": "anthropic",
}
_PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    "chatgpt-codex": "gpt-5.4",
    "claude-chat": "claude-sonnet-4-5",
}

# JSON schema hint sent to LLM providers so they return valid ArchitectResponse JSON.
_RESPONSE_SCHEMA_HINT = (
    '{"summary": string, '
    '"risk_level": "low"|"medium"|"high"|"critical", '
    '"recommended_materialization": "none"|"task"|"approval", '
    '"clarifying_questions": [string], '
    '"proposed_tasks": [{'
    '"title": string, "description": string, "task_type": string, '
    '"priority": string, "complexity": string, '
    '"risk_level": "low"|"medium"|"high"|"critical", '
    '"acceptance_criteria": [string], '
    '"dependencies": [string], '
    '"tags": [string], '
    '"suggested_decomposition": [string]'
    "}]}"
)


@runtime_checkable
class ArchitectProvider(Protocol):
    """Protocol implemented by all architect-provider adapters.

    Every adapter receives an ArchitectRequest and returns a validated
    ArchitectResponse.  Validation is enforced by the boundary — adapters
    that return non-conforming output must fall back to the rule-based
    provider rather than propagating invalid data.
    """

    provider_key: str

    async def plan(self, request: ArchitectRequest) -> ArchitectResponse: ...


class RuleBasedArchitectProvider:
    """Default architect-provider backed by deterministic local rules.

    No LLM calls are made.  The response is derived from the request fields
    and is always valid.
    """

    provider_key = DEFAULT_ARCHITECT_PROVIDER

    async def plan(self, request: ArchitectRequest) -> ArchitectResponse:
        has_project = bool(request.project_id)
        has_answers = any(a.strip() for a in request.clarification_answers)
        pending = [q for q in request.payload.get("pending_clarifying_questions", []) if str(q).strip()]

        recommended = "task" if has_project and (not pending or has_answers) else "none"

        clarifying: list[str] = []
        if not has_project:
            clarifying.append("Which project should this request target?")
        if pending and not has_answers:
            clarifying.extend(pending)

        proposed: list[ArchitectProposedTask] = []
        if has_project:
            proposed.append(
                ArchitectProposedTask(
                    title=request.title,
                    description=request.description,
                    task_type="feature",
                    priority="medium",
                    complexity="simple",
                    risk_level=ArchitectRiskLevel.LOW,
                    acceptance_criteria=[],
                    dependencies=[],
                    tags=["architect-request"],
                )
            )

        return validate_architect_response({
            "requested_provider": self.provider_key,
            "resolved_provider": self.provider_key,
            "strategy": "rule-based",
            "model": None,
            "summary": (
                f"Architect triage for '{request.title}' recommends "
                f"{'task materialization' if has_project else 'manual review'} as the next step."
            ),
            "risk_level": "low",
            "proposed_tasks": [t.model_dump() for t in proposed],
            "clarifying_questions": clarifying,
            "recommended_materialization": recommended,
            "metadata": {
                "project_id": request.project_id,
                "clarification_answers": request.clarification_answers,
            },
        })


class LLMArchitectProvider:
    """Architect-provider adapter backed by an LLM (ChatGPT/Codex or Claude Chat).

    Returns a validated ArchitectResponse; falls back to rule-based on any
    provider error to prevent invalid data from reaching task creation.
    """

    def __init__(self, provider_key: str) -> None:
        if provider_key not in _PROVIDER_TO_BACKEND:
            raise ValueError(f"Unsupported LLM architect provider: {provider_key!r}")
        self.provider_key = provider_key
        self._fallback = RuleBasedArchitectProvider()

    async def plan(self, request: ArchitectRequest) -> ArchitectResponse:
        try:
            backend_provider, resolved_model = await self._resolve_model(request.model)
            messages = self._build_messages(request)
            async with get_llm_client(provider=backend_provider) as client:
                response = await client.chat.completions.create(
                    model=resolved_model,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=2000,
                    response_format={"type": "json_object"},
                )
            raw = response.choices[0].message.content
            if not raw or not isinstance(raw, str):
                raise ValueError("LLM architect returned empty content")
            parsed = json.loads(raw)
            parsed.setdefault("requested_provider", self.provider_key)
            parsed.setdefault("resolved_provider", self.provider_key)
            parsed.setdefault("strategy", "provider-generated")
            parsed["model"] = resolved_model
            return validate_architect_response(parsed)
        except Exception as exc:
            logger.warning(
                f"LLM architect provider failed, falling back to rule-based | "
                f"provider={self.provider_key} | error={exc}"
            )
            fallback_response = await self._fallback.plan(request)
            return ArchitectResponse(
                **{
                    **fallback_response.model_dump(),
                    "requested_provider": self.provider_key,
                    "strategy": "fallback-rule-based",
                }
            )

    async def _resolve_model(self, requested_model: str | None) -> tuple[str, str]:
        backend = _PROVIDER_TO_BACKEND[self.provider_key]
        if requested_model:
            return backend, requested_model
        active = await credential_service.get_active_provider("llm")
        if active and active.get("provider") == backend:
            chat_model = active.get("chat_model")
            if isinstance(chat_model, str) and chat_model.strip():
                return backend, chat_model.strip()
        return backend, _PROVIDER_DEFAULT_MODEL[self.provider_key]

    def _build_messages(self, request: ArchitectRequest) -> list[dict[str, str]]:
        planner_hint = (
            "You are ChatGPT/Codex acting as the project architect."
            if self.provider_key == "chatgpt-codex"
            else "You are Claude acting as the project architect."
        )
        system_prompt = "\n".join([
            planner_hint,
            "Decompose the request into structured task proposals.",
            f"Return strict JSON matching this schema (no markdown fences): {_RESPONSE_SCHEMA_HINT}",
            "Each proposed_task must have acceptance_criteria (list of verifiable checks), "
            "dependencies (list of task titles this depends on), and risk_level.",
            "suggested_decomposition lists sub-task titles when a task warrants further breakdown.",
            "Prefer concrete, directly executable tasks over vague placeholders.",
        ])
        user_lines = [
            f"Title: {request.title}",
            f"Description: {request.description}",
            f"Project ID: {request.project_id or 'unknown'}",
        ]
        if request.context:
            user_lines.append(f"Context: {request.context}")
        if request.clarification_answers:
            user_lines.append(f"Clarification answers: {json.dumps(request.clarification_answers, ensure_ascii=True)}")
        if request.payload:
            user_lines.append(f"Payload: {json.dumps(request.payload, ensure_ascii=True)}")
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n".join(user_lines)},
        ]


def normalize_architect_provider(provider: str | None) -> str:
    """Return a supported architect-provider key, defaulting to rule-based."""
    value = (provider or DEFAULT_ARCHITECT_PROVIDER).strip().lower()
    return value if value in SUPPORTED_ARCHITECT_PROVIDERS else DEFAULT_ARCHITECT_PROVIDER


def resolve_architect_provider(provider: str | None) -> ArchitectProvider:
    """Resolve and return the appropriate ArchitectProvider adapter."""
    key = normalize_architect_provider(provider)
    if key == DEFAULT_ARCHITECT_PROVIDER:
        return RuleBasedArchitectProvider()
    return LLMArchitectProvider(key)


async def run_architect_plan(request: ArchitectRequest) -> ArchitectResponse:
    """Run an architect planning request through the provider boundary.

    This is the single entry point for all architect planning.  Provider
    selection, LLM calls, and contract validation all flow through here.
    The returned ArchitectResponse is always contract-validated before
    being returned to the caller.
    """
    provider = resolve_architect_provider(request.requested_provider)
    return await provider.plan(request)
