"""Project bootstrap task-pack creation helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...config.logfire_config import get_logger
from .bootstrap_architect import (
    DEFAULT_BOOTSTRAP_ARCHITECT_PROVIDER,
    BootstrapPlanEnvelope,
    create_bootstrap_plan_envelope,
    normalize_bootstrap_architect_provider,
    resolve_bootstrap_architect,
)
from .bootstrap_plan_service import BootstrapPlanService
from .bootstrap_planner import (
    DEFAULT_BOOTSTRAP_POLICY,
    DEFAULT_BOOTSTRAP_TEMPLATE,
    DEFAULT_PROJECT_TYPE,
    BootstrapPlanContext,
    BootstrapPlanItem,
    build_bootstrap_context,
)
from .project_template_service import TemplateTaskDefinition, project_template_service

logger = get_logger(__name__)


def _materialize_plan_items(envelope: BootstrapPlanEnvelope) -> list[Any]:
    """Flatten bootstrap plan envelope into execution order for task insertion."""
    return [*envelope.items, *envelope.backlog_items]


def _task_summary(
    created: dict[str, Any],
    fallback: dict[str, Any],
    *,
    plan_key: str | None = None,
) -> dict[str, Any]:
    """Return lightweight task metadata exposed to operators and clients."""
    return {
        "id": created.get("id"),
        "plan_key": plan_key,
        "title": created.get("title", fallback["title"]),
        "status": created.get("status", fallback["status"]),
        "tags": created.get("tags", fallback["tags"]),
        "created_from": created.get("created_from", fallback["created_from"]),
        "blocked_by": created.get("blocked_by", fallback["blocked_by"]),
    }


def _build_task_payload(
    *,
    plan_item: Any,
    project_id: str,
    source_app: str | None,
    blocked_by: list[str],
    task_order: int,
    timestamp: str,
    architect_provider: str,
    architect_resolved_provider: str,
    architect_strategy: str,
) -> dict[str, Any]:
    """Convert a planner item into a task insert payload."""
    tags = list(plan_item.tags)
    tags.extend([
        f"bootstrap-architect:{architect_provider}",
        f"bootstrap-architect-resolved:{architect_resolved_provider}",
        f"bootstrap-plan-strategy:{architect_strategy}",
    ])
    return {
        "project_id": project_id,
        "title": plan_item.title,
        "description": plan_item.description,
        "status": "approved",
        "assignee": "Platform",
        "task_order": task_order,
        "priority": plan_item.priority,
        "task_type": plan_item.task_type,
        "complexity": plan_item.complexity,
        "max_retries": plan_item.max_retries,
        "source_app": source_app,
        "created_by": "system",
        "created_from": plan_item.created_from,
        "execution_prompt": plan_item.execution_prompt,
        "acceptance_criteria": [{"text": text} for text in plan_item.acceptance_criteria],
        "blocked_by": blocked_by,
        "state_history": [],
        "state_changed_at": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
        "retry_count": 0,
        "tags": tags,
        "executed_by": None,
        "reviewed_by": [],
    }


def _template_task_to_plan_item(task_def: TemplateTaskDefinition) -> BootstrapPlanItem:
    """Convert a template task definition to a BootstrapPlanItem for insertion."""
    return BootstrapPlanItem(
        key=task_def.key,
        title=task_def.title,
        description=task_def.description,
        task_type=task_def.task_type,
        priority=task_def.priority,
        complexity=task_def.complexity,
        max_retries=task_def.max_retries,
        created_from="template-task-pack",
        execution_prompt=task_def.execution_prompt,
        acceptance_criteria=task_def.acceptance_criteria,
        tags=list(task_def.tags),
        blocked_on_key=task_def.blocked_on_key,
    )


def create_project_bootstrap_task(
    *,
    supabase_client: Any,
    project_id: str,
    project_title: str,
    project_description: str | None = None,
    github_repo: str | None = None,
    source_app: str | None = None,
    bootstrap_template: str | None = None,
    project_type: str | None = None,
    bootstrap_policy: str | None = None,
    bootstrap_architect_provider: str | None = None,
    bootstrap_architect_model: str | None = None,
) -> dict[str, Any] | None:
    """Insert only the first bootstrap scaffold task for compatibility paths."""
    task_pack = create_project_bootstrap_task_pack(
        supabase_client=supabase_client,
        project_id=project_id,
        project_title=project_title,
        project_description=project_description,
        github_repo=github_repo,
        source_app=source_app,
        bootstrap_template=bootstrap_template,
        project_type=project_type,
        bootstrap_policy=bootstrap_policy,
        bootstrap_architect_provider=bootstrap_architect_provider,
        bootstrap_architect_model=bootstrap_architect_model,
    )
    return task_pack[0] if task_pack else None


def create_project_bootstrap_task_pack(
    *,
    supabase_client: Any,
    project_id: str,
    project_title: str,
    project_description: str | None = None,
    github_repo: str | None = None,
    source_app: str | None = None,
    bootstrap_template: str | None = None,
    project_type: str | None = None,
    bootstrap_policy: str | None = None,
    bootstrap_architect_provider: str | None = None,
    bootstrap_architect_model: str | None = None,
) -> list[dict[str, Any]]:
    """Insert the policy-shaped bootstrap task graph and return lightweight summaries."""
    requested_provider = normalize_bootstrap_architect_provider(bootstrap_architect_provider)
    architect = resolve_bootstrap_architect(requested_provider)
    context = build_bootstrap_context(
        project_title=project_title,
        project_description=project_description,
        github_repo=github_repo,
        bootstrap_template=bootstrap_template or DEFAULT_BOOTSTRAP_TEMPLATE,
        project_type=project_type or DEFAULT_PROJECT_TYPE,
        bootstrap_policy=bootstrap_policy or DEFAULT_BOOTSTRAP_POLICY,
    )
    envelope = architect.create_plan(
        context=context,
        requested_provider=requested_provider,
        model=bootstrap_architect_model,
    )
    return _insert_bootstrap_plan_envelope(
        supabase_client=supabase_client,
        project_id=project_id,
        source_app=source_app,
        context=context,
        envelope=envelope,
    )


async def create_project_bootstrap_task_pack_async(
    *,
    supabase_client: Any,
    project_id: str,
    project_title: str,
    project_description: str | None = None,
    github_repo: str | None = None,
    source_app: str | None = None,
    bootstrap_template: str | None = None,
    project_type: str | None = None,
    bootstrap_policy: str | None = None,
    bootstrap_architect_provider: str | None = None,
    bootstrap_architect_model: str | None = None,
) -> list[dict[str, Any]]:
    """Insert a bootstrap task graph generated by an async architect-provider flow."""
    requested_provider = normalize_bootstrap_architect_provider(bootstrap_architect_provider)
    context = build_bootstrap_context(
        project_title=project_title,
        project_description=project_description,
        github_repo=github_repo,
        bootstrap_template=bootstrap_template or DEFAULT_BOOTSTRAP_TEMPLATE,
        project_type=project_type or DEFAULT_PROJECT_TYPE,
        bootstrap_policy=bootstrap_policy or DEFAULT_BOOTSTRAP_POLICY,
    )
    envelope = await create_bootstrap_plan_envelope(
        context=context,
        requested_provider=requested_provider,
        model=bootstrap_architect_model,
    )
    return _insert_bootstrap_plan_envelope(
        supabase_client=supabase_client,
        project_id=project_id,
        source_app=source_app,
        context=context,
        envelope=envelope,
    )


def _insert_bootstrap_plan_envelope(
    *,
    supabase_client: Any,
    project_id: str,
    source_app: str | None,
    context: BootstrapPlanContext,
    envelope: BootstrapPlanEnvelope,
) -> list[dict[str, Any]]:
    """Insert a bootstrap plan envelope (plus any template task pack) and return lightweight summaries."""
    timestamp = datetime.now(UTC).isoformat()
    summaries: list[dict[str, Any]] = []
    created_ids_by_key: dict[str, str] = {}

    # Collect standard plan items first, then append any template-specific task pack.
    all_items: list[Any] = list(_materialize_plan_items(envelope))
    template_task_defs = project_template_service.get_task_pack(context.template)
    all_items.extend(_template_task_to_plan_item(td) for td in template_task_defs)

    for task_order, plan_item in enumerate(all_items):
        blocked_by: list[str] = []
        if plan_item.blocked_on_key:
            blocker_id = created_ids_by_key.get(plan_item.blocked_on_key)
            if blocker_id:
                blocked_by = [blocker_id]

        task_data = _build_task_payload(
            plan_item=plan_item,
            project_id=project_id,
            source_app=source_app,
            blocked_by=blocked_by,
            task_order=task_order,
            timestamp=timestamp,
            architect_provider=envelope.requested_provider or DEFAULT_BOOTSTRAP_ARCHITECT_PROVIDER,
            architect_resolved_provider=envelope.resolved_provider,
            architect_strategy=envelope.strategy,
        )

        response = supabase_client.table("archon_tasks").insert(task_data).execute()
        if not response.data:
            logger.warning(
                f"Bootstrap task insert returned no data | project_id={project_id} | step={plan_item.key}"
            )
            return summaries

        created = response.data[0]
        summary = _task_summary(created, task_data, plan_key=plan_item.key)
        summaries.append(summary)
        if summary.get("id"):
            created_ids_by_key[plan_item.key] = summary["id"]

    ok, result = BootstrapPlanService(supabase_client=supabase_client).create_plan(
        project_id=project_id,
        source_app=source_app,
        context=context,
        envelope=envelope,
        created_tasks=summaries,
    )
    if ok:
        plan = result.get("plan") or {}
        created_tasks = plan.get("created_tasks")
        if isinstance(created_tasks, list):
            return created_tasks
    return summaries
