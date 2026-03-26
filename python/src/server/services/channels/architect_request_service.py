"""Architect-provider boundary for external channel planning requests."""

from __future__ import annotations

import json
from typing import Any

from ...config.logfire_config import get_logger
from ..credential_service import credential_service
from ..llm_provider_service import get_llm_client

logger = get_logger(__name__)

DEFAULT_ARCHITECT_PROVIDER = "rule-based"
SUPPORTED_ARCHITECT_PROVIDERS = {"rule-based", "chatgpt-codex", "claude-chat"}
ARCHITECT_PROVIDER_TO_BACKEND = {
    "chatgpt-codex": "openai",
    "claude-chat": "anthropic",
}
ARCHITECT_PROVIDER_DEFAULT_MODEL = {
    "chatgpt-codex": "gpt-5.4",
    "claude-chat": "claude-sonnet-4-5",
}


def normalize_architect_provider(provider: str | None) -> str:
    """Return a supported architect-provider key."""
    value = (provider or DEFAULT_ARCHITECT_PROVIDER).strip().lower()
    return value if value in SUPPORTED_ARCHITECT_PROVIDERS else DEFAULT_ARCHITECT_PROVIDER


class ArchitectRequestService:
    """Create a planner response for external architect requests."""

    async def create_plan(
        self,
        *,
        title: str,
        summary: str,
        project_id: str | None = None,
        input_modality: str | None = None,
        input_text: str | None = None,
        clarification_answers: list[str] | None = None,
        sequence_context: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        requested_provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Create an architect response envelope using provider-backed planning when available."""
        normalized_provider = normalize_architect_provider(requested_provider)
        if normalized_provider == DEFAULT_ARCHITECT_PROVIDER:
            return self._fallback_plan(
                title=title,
                summary=summary,
                project_id=project_id,
                input_modality=input_modality,
                clarification_answers=clarification_answers or [],
                sequence_context=sequence_context or {},
                requested_provider=normalized_provider,
                model=model,
            )

        try:
            backend_provider, resolved_model = await self._resolve_backend_model(normalized_provider, model)
            messages = self._build_messages(
                title=title,
                summary=summary,
                project_id=project_id,
                input_modality=input_modality,
                input_text=input_text,
                clarification_answers=clarification_answers or [],
                sequence_context=sequence_context or {},
                payload=payload or {},
                requested_provider=normalized_provider,
            )
            async with get_llm_client(provider=backend_provider) as client:
                response = await client.chat.completions.create(
                    model=resolved_model,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=1800,
                    response_format={"type": "json_object"},
                )

            raw_content = response.choices[0].message.content
            parsed = json.loads(raw_content)
            return {
                "requested_provider": normalized_provider,
                "resolved_provider": normalized_provider,
                "strategy": "provider-generated",
                "model": resolved_model,
                "summary": str(parsed.get("summary") or summary),
                "recommended_materialization": str(parsed.get("recommended_materialization") or "none"),
                "clarifying_questions": [str(item) for item in (parsed.get("clarifying_questions") or []) if str(item).strip()],
                "suggested_tasks": list(parsed.get("suggested_tasks") or []),
                "suggested_approval": parsed.get("suggested_approval"),
                "metadata": {
                    "project_id": project_id,
                    "provider_backend": backend_provider,
                    "input_modality": input_modality,
                    "clarification_answers": clarification_answers or [],
                    "sequence_context": sequence_context or {},
                },
            }
        except Exception as exc:
            logger.warning(f"Architect request provider fallback triggered: {exc}")
            return self._fallback_plan(
                title=title,
                summary=summary,
                project_id=project_id,
                input_modality=input_modality,
                clarification_answers=clarification_answers or [],
                sequence_context=sequence_context or {},
                requested_provider=normalized_provider,
                model=model,
                strategy="fallback-rule-based",
            )

    async def _resolve_backend_model(self, requested_provider: str, requested_model: str | None) -> tuple[str, str]:
        backend_provider = ARCHITECT_PROVIDER_TO_BACKEND[requested_provider]
        if requested_model:
            return backend_provider, requested_model

        active_provider = await credential_service.get_active_provider("llm")
        if active_provider and active_provider.get("provider") == backend_provider:
            chat_model = active_provider.get("chat_model")
            if isinstance(chat_model, str) and chat_model.strip():
                return backend_provider, chat_model.strip()

        return backend_provider, ARCHITECT_PROVIDER_DEFAULT_MODEL[requested_provider]

    def _build_messages(
        self,
        *,
        title: str,
        summary: str,
        project_id: str | None,
        input_modality: str | None,
        input_text: str | None,
        clarification_answers: list[str],
        sequence_context: dict[str, Any],
        payload: dict[str, Any],
        requested_provider: str,
    ) -> list[dict[str, str]]:
        planner_hint = (
            "You are ChatGPT/Codex acting as the LeanKit architect."
            if requested_provider == "chatgpt-codex"
            else "You are Claude acting as the LeanKit architect."
        )
        system_prompt = "\n".join([
            planner_hint,
            "Turn an external channel request into a compact execution plan.",
            "Return strict JSON only.",
            "Schema: {\"summary\": string, \"recommended_materialization\": \"none\"|\"task\"|\"approval\", "
            "\"clarifying_questions\": [string], "
            "\"suggested_tasks\": [{\"title\": string, \"summary\": string, \"task_type\": string, "
            "\"priority\": string, \"complexity\": string, \"tags\": [string]}], "
            "\"suggested_approval\": {\"title\": string, \"summary\": string} | null}.",
            "Prefer task materialization when work should be executed by runners.",
            "Prefer approval materialization only when a human decision is the next action.",
            "If key context is missing, keep materialization conservative and add clarifying questions.",
        ])
        user_prompt = "\n".join([
            f"Title: {title}",
            f"Summary: {summary}",
            f"Project ID: {project_id or 'unknown'}",
            f"Input modality: {input_modality or 'text'}",
            f"Input text: {input_text or summary}",
            f"Clarification answers: {json.dumps(clarification_answers, ensure_ascii=True)}",
            f"Sequence context: {json.dumps(sequence_context, ensure_ascii=True)}",
            f"Payload: {json.dumps(payload, ensure_ascii=True)}",
        ])
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _fallback_plan(
        self,
        *,
        title: str,
        summary: str,
        project_id: str | None,
        input_modality: str | None,
        clarification_answers: list[str],
        sequence_context: dict[str, Any],
        requested_provider: str,
        model: str | None,
        strategy: str = "rule-based",
    ) -> dict[str, Any]:
        pending_clarifications = [
            str(item) for item in (sequence_context.get("pending_clarifying_questions") or []) if str(item).strip()
        ]
        has_answers = any(answer.strip() for answer in clarification_answers)
        recommended_materialization = "task" if project_id and (not pending_clarifications or has_answers) else "none"
        clarifying_questions: list[str] = []
        if not project_id:
            clarifying_questions.append("Which project should this request target?")
        if pending_clarifications and not has_answers:
            clarifying_questions.extend(pending_clarifications)
        elif (sequence_context.get("history_count") or 0) == 0 and input_modality == "voice":
            clarifying_questions.append("Do you want LeanKit to decompose this into tasks immediately, or only produce a plan?")
        return {
            "requested_provider": requested_provider,
            "resolved_provider": DEFAULT_ARCHITECT_PROVIDER,
            "strategy": strategy,
            "model": model,
            "summary": f"Architect triage for '{title}' recommends {'task materialization' if project_id else 'manual review'} as the next step.",
            "recommended_materialization": recommended_materialization,
            "clarifying_questions": clarifying_questions,
            "suggested_tasks": [{
                "title": title,
                "summary": summary,
                "task_type": "feature",
                "priority": "medium",
                "complexity": "simple",
                "tags": [
                    "architect-request",
                    "external-channel:openclaw",
                    *( [f"input-modality:{input_modality}"] if input_modality else [] ),
                ],
            }] if project_id else [],
            "suggested_approval": None,
            "metadata": {
                "project_id": project_id,
                "input_modality": input_modality,
                "clarification_answers": clarification_answers,
                "sequence_context": sequence_context,
            },
        }
