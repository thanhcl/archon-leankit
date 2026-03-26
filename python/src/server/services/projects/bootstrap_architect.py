"""Architect-provider boundary for bootstrap planning."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ...config.logfire_config import get_logger
from ..credential_service import credential_service
from ..llm_provider_service import get_llm_client
from .bootstrap_planner import BootstrapPlanContext, BootstrapPlanItem, plan_project_bootstrap

DEFAULT_BOOTSTRAP_ARCHITECT_PROVIDER = "rule-based"
DEFAULT_PROVIDER_PROJECT_TYPES = {
    "web-app": "chatgpt-codex",
    "api-service": "chatgpt-codex",
    "library": "chatgpt-codex",
    "automation": "chatgpt-codex",
}
SUPPORTED_BOOTSTRAP_ARCHITECTS = {
    "rule-based",
    "chatgpt-codex",
    "claude-chat",
}

ARCHITECT_PROVIDER_TO_BACKEND = {
    "chatgpt-codex": "openai",
    "claude-chat": "anthropic",
}

ARCHITECT_PROVIDER_DEFAULT_MODEL = {
    "chatgpt-codex": "gpt-5.4",
    "claude-chat": "claude-sonnet-4-5",
}

logger = get_logger(__name__)


@dataclass(frozen=True)
class BootstrapPlanEnvelope:
    """Bootstrap plan plus architect-provider metadata."""

    requested_provider: str
    resolved_provider: str
    strategy: str
    model: str | None
    items: list[BootstrapPlanItem]
    backlog_items: list[BootstrapPlanItem] = field(default_factory=list)


class BootstrapPlanItemModel(BaseModel):
    """Structured bootstrap plan item returned by architect providers."""

    key: str
    title: str
    description: str
    task_type: str
    priority: str
    complexity: str
    max_retries: int = 1
    created_from: str
    execution_prompt: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    blocked_on_key: str | None = None


class BootstrapPlanResponseModel(BaseModel):
    """Structured bootstrap plan response returned by architect providers."""

    items: list[BootstrapPlanItemModel]
    backlog_items: list[BootstrapPlanItemModel] = Field(default_factory=list)


class BootstrapArchitect(Protocol):
    """Protocol for architect-provider bootstrap planners."""

    provider_key: str

    def create_plan(
        self,
        *,
        context: BootstrapPlanContext,
        requested_provider: str,
        model: str | None = None,
    ) -> BootstrapPlanEnvelope: ...


class RuleBasedBootstrapArchitect:
    """Default architect implementation backed by the local rule planner."""

    provider_key = DEFAULT_BOOTSTRAP_ARCHITECT_PROVIDER

    def create_plan(
        self,
        *,
        context: BootstrapPlanContext,
        requested_provider: str,
        model: str | None = None,
    ) -> BootstrapPlanEnvelope:
        strategy = "rule-based"
        if requested_provider != self.provider_key:
            strategy = "fallback-rule-based"
        return BootstrapPlanEnvelope(
            requested_provider=requested_provider,
            resolved_provider=self.provider_key,
            strategy=strategy,
            model=model,
            items=plan_project_bootstrap(context),
            backlog_items=[],
        )


def normalize_bootstrap_architect_provider(provider: str | None) -> str:
    """Return a supported architect-provider key."""
    value = (provider or DEFAULT_BOOTSTRAP_ARCHITECT_PROVIDER).strip().lower()
    return value if value in SUPPORTED_BOOTSTRAP_ARCHITECTS else DEFAULT_BOOTSTRAP_ARCHITECT_PROVIDER


def resolve_default_bootstrap_architect_provider(context: BootstrapPlanContext) -> str:
    """Return the default architect-provider for a normalized bootstrap context."""
    return DEFAULT_PROVIDER_PROJECT_TYPES.get(context.project_type, DEFAULT_BOOTSTRAP_ARCHITECT_PROVIDER)


def resolve_bootstrap_architect(provider: str | None) -> BootstrapArchitect:
    """Resolve the current architect-provider implementation."""
    _ = normalize_bootstrap_architect_provider(provider)
    return RuleBasedBootstrapArchitect()


def _envelope_from_rule_based(
    *,
    context: BootstrapPlanContext,
    requested_provider: str,
    model: str | None = None,
    strategy: str = "fallback-rule-based",
) -> BootstrapPlanEnvelope:
    return BootstrapPlanEnvelope(
        requested_provider=requested_provider,
        resolved_provider=DEFAULT_BOOTSTRAP_ARCHITECT_PROVIDER,
        strategy=strategy,
        model=model,
        items=plan_project_bootstrap(context),
        backlog_items=[],
    )


def _coerce_plan_items(items: list[BootstrapPlanItemModel]) -> list[BootstrapPlanItem]:
    """Convert validated plan item models into runtime plan items."""
    return [
        BootstrapPlanItem(
            key=item.key,
            title=item.title,
            description=item.description,
            task_type=item.task_type,
            priority=item.priority,
            complexity=item.complexity,
            max_retries=item.max_retries,
            created_from=item.created_from,
            execution_prompt=item.execution_prompt,
            acceptance_criteria=item.acceptance_criteria,
            tags=item.tags,
            blocked_on_key=item.blocked_on_key,
        )
        for item in items
    ]


def _coerce_backlog_items(
    *,
    context: BootstrapPlanContext,
    items: list[BootstrapPlanItemModel],
) -> list[BootstrapPlanItem]:
    """Convert provider-generated backlog items into runtime bootstrap backlog tasks."""
    normalized: list[BootstrapPlanItem] = []
    existing_keys: set[str] = set()

    for index, item in enumerate(items, start=1):
        key = item.key.strip() if item.key.strip() else f"backlog-{index}"
        if key in existing_keys:
            key = f"backlog-{index}-{key}"
        existing_keys.add(key)

        created_from = item.created_from.strip() if item.created_from.strip() else f"project-bootstrap-derived-{index}"
        if not created_from.startswith("project-bootstrap"):
            created_from = f"project-bootstrap-derived-{index}"

        tags = list(dict.fromkeys([
            "project-bootstrap",
            "bootstrap-derived-backlog",
            f"template:{context.template}",
            f"project-type:{context.project_type}",
            f"bootstrap-policy:{context.bootstrap_policy}",
            *item.tags,
        ]))

        normalized.append(
            BootstrapPlanItem(
                key=key,
                title=item.title,
                description=item.description,
                task_type=item.task_type,
                priority=item.priority,
                complexity=item.complexity,
                max_retries=item.max_retries,
                created_from=created_from,
                execution_prompt=item.execution_prompt,
                acceptance_criteria=item.acceptance_criteria,
                tags=tags,
                blocked_on_key=item.blocked_on_key or "followup",
            )
        )

    return normalized


def _extract_json_content(response: Any) -> str:
    """Extract JSON content from an OpenAI-compatible chat completion response."""
    try:
        content = response.choices[0].message.content
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError("Architect provider returned an unexpected response shape") from exc

    if not content or not isinstance(content, str):
        raise ValueError("Architect provider returned empty content")
    return content


async def _resolve_backend_model(requested_provider: str, requested_model: str | None) -> tuple[str, str]:
    """Resolve backend provider and model for the requested architect provider."""
    backend_provider = ARCHITECT_PROVIDER_TO_BACKEND[requested_provider]
    if requested_model:
        return backend_provider, requested_model

    active_provider = await credential_service.get_active_provider("llm")
    if active_provider and active_provider.get("provider") == backend_provider:
        chat_model = active_provider.get("chat_model")
        if isinstance(chat_model, str) and chat_model.strip():
            return backend_provider, chat_model.strip()

    return backend_provider, ARCHITECT_PROVIDER_DEFAULT_MODEL[requested_provider]


def _build_bootstrap_architect_messages(
    *,
    context: BootstrapPlanContext,
    requested_provider: str,
) -> list[dict[str, str]]:
    """Build the architect-provider planning prompt."""
    planner_hint = (
        "You are ChatGPT/Codex acting as the project architect."
        if requested_provider == "chatgpt-codex"
        else "You are Claude acting as the project architect."
    )
    system_prompt = "\n".join([
        planner_hint,
        "Generate a bootstrap task plan for a new software project.",
        "Return strict JSON with the shape {\"items\": [...]} and no markdown fences.",
        "You may also return {\"backlog_items\": [...]} for concrete follow-up tasks that should be materialized after bootstrap.",
        "Every item must include: key, title, description, task_type, priority, complexity, max_retries, "
        "created_from, execution_prompt, acceptance_criteria, tags, blocked_on_key.",
        "The plan must include at least scaffold, validation, and followup.",
        "Use architecture as an additional gate when the bootstrap policy is strict.",
        "Keep tasks implementation-oriented and directly executable by automation runners.",
    ])
    user_prompt = "\n".join([
        f"Project title: {context.project_title}",
        f"Project description: {context.project_description or 'No description provided.'}",
        f"Repository: {context.repo_hint}",
        f"Template: {context.template}",
        f"Project type: {context.project_type}",
        f"Bootstrap policy: {context.bootstrap_policy}",
        "Prefer a concise task graph with explicit dependencies via blocked_on_key.",
        "Use created_from values under the project-bootstrap namespace.",
    ])
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


async def create_bootstrap_plan_envelope(
    *,
    context: BootstrapPlanContext,
    requested_provider: str | None,
    model: str | None = None,
) -> BootstrapPlanEnvelope:
    """Create a bootstrap plan using a real architect provider when supported."""
    explicit_provider = isinstance(requested_provider, str) and bool(requested_provider.strip())
    normalized_provider = (
        normalize_bootstrap_architect_provider(requested_provider)
        if explicit_provider
        else resolve_default_bootstrap_architect_provider(context)
    )

    if normalized_provider == DEFAULT_BOOTSTRAP_ARCHITECT_PROVIDER:
        return _envelope_from_rule_based(
            context=context,
            requested_provider=normalized_provider,
            model=model,
            strategy="rule-based",
        )

    try:
        backend_provider, resolved_model = await _resolve_backend_model(normalized_provider, model)
        messages = _build_bootstrap_architect_messages(
            context=context,
            requested_provider=normalized_provider,
        )
        async with get_llm_client(provider=backend_provider) as client:
            response = await client.chat.completions.create(
                model=resolved_model,
                messages=messages,
                temperature=0.2,
                max_tokens=3000,
                response_format={"type": "json_object"},
            )
        content = _extract_json_content(response)
        parsed = BootstrapPlanResponseModel.model_validate(json.loads(content))
        return BootstrapPlanEnvelope(
            requested_provider=normalized_provider,
            resolved_provider=normalized_provider,
            strategy="provider-generated" if explicit_provider else "provider-generated-default",
            model=resolved_model,
            items=_coerce_plan_items(parsed.items),
            backlog_items=_coerce_backlog_items(context=context, items=parsed.backlog_items),
        )
    except Exception as exc:
        logger.warning(
            f"Bootstrap architect provider failed, falling back to rule-based planner | "
            f"requested_provider={normalized_provider} | error={exc}"
        )
        return _envelope_from_rule_based(
            context=context,
            requested_provider=normalized_provider,
            model=model,
            strategy="fallback-rule-based",
        )
