"""Persistence service for bootstrap plan metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger
from .bootstrap_architect import BootstrapPlanEnvelope
from .bootstrap_planner import BootstrapPlanContext

logger = get_logger(__name__)


class BootstrapPlanService:
    """Stores and retrieves first-class bootstrap plan records."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    def create_plan(
        self,
        *,
        project_id: str,
        source_app: str | None,
        context: BootstrapPlanContext,
        envelope: BootstrapPlanEnvelope,
        created_tasks: list[dict[str, Any]],
    ) -> tuple[bool, dict[str, Any]]:
        """Persist one bootstrap plan record."""
        try:
            now = datetime.now().isoformat()
            payload = {
                "project_id": project_id,
                "requested_provider": envelope.requested_provider,
                "resolved_provider": envelope.resolved_provider,
                "strategy": envelope.strategy,
                "model": envelope.model,
                "template": context.template,
                "project_type": context.project_type,
                "bootstrap_policy": context.bootstrap_policy,
                "source_app": source_app,
                "status": "materialized" if created_tasks else "planned",
                "plan_items": [
                    {
                        "key": item.key,
                        "title": item.title,
                        "description": item.description,
                        "task_type": item.task_type,
                        "priority": item.priority,
                        "complexity": item.complexity,
                        "max_retries": item.max_retries,
                        "created_from": item.created_from,
                        "execution_prompt": item.execution_prompt,
                        "acceptance_criteria": item.acceptance_criteria,
                        "tags": item.tags,
                        "blocked_on_key": item.blocked_on_key,
                    }
                    for item in envelope.items
                ],
                "created_tasks": created_tasks,
                "metadata": {
                    "project_title": context.project_title,
                    "project_description": context.project_description,
                    "repo_hint": context.repo_hint,
                    "backlog_items": [
                        {
                            "key": item.key,
                            "title": item.title,
                            "description": item.description,
                            "task_type": item.task_type,
                            "priority": item.priority,
                            "complexity": item.complexity,
                            "max_retries": item.max_retries,
                            "created_from": item.created_from,
                            "execution_prompt": item.execution_prompt,
                            "acceptance_criteria": item.acceptance_criteria,
                            "tags": item.tags,
                            "blocked_on_key": item.blocked_on_key,
                        }
                        for item in envelope.backlog_items
                    ],
                },
                "created_at": now,
                "updated_at": now,
            }
            response = self.supabase_client.table("archon_bootstrap_plans").insert(payload).execute()
            if response.data:
                plan = response.data[0]
                plan_id = plan.get("id")
                if isinstance(plan_id, str) and plan_id:
                    updated_tasks = self._attach_plan_tag_to_tasks(plan_id, created_tasks)
                    plan["created_tasks"] = updated_tasks
                    self._update_plan_created_tasks(plan_id, updated_tasks, now)
                return True, {"plan": plan}
            return False, {"error": "Failed to persist bootstrap plan"}
        except Exception as exc:
            logger.warning(f"Failed to persist bootstrap plan | project_id={project_id} | error={exc}")
            return False, {"error": str(exc)}

    def preview_plan(
        self,
        *,
        project_id: str,
        source_app: str | None,
        context: BootstrapPlanContext,
        envelope: BootstrapPlanEnvelope,
    ) -> tuple[bool, dict[str, Any]]:
        """Build a dry-run bootstrap plan preview without persisting anything."""
        try:
            def _item_to_dict(item: Any, *, is_backlog: bool) -> dict[str, Any]:
                return {
                    "key": item.key,
                    "title": item.title,
                    "description": item.description,
                    "task_type": item.task_type,
                    "priority": item.priority,
                    "complexity": item.complexity,
                    "max_retries": item.max_retries,
                    "created_from": item.created_from,
                    "execution_prompt": item.execution_prompt,
                    "acceptance_criteria": item.acceptance_criteria,
                    "tags": item.tags,
                    "blocked_on_key": item.blocked_on_key,
                    "is_backlog": is_backlog,
                }

            plan_items = [_item_to_dict(item, is_backlog=False) for item in envelope.items]
            backlog_items = [_item_to_dict(item, is_backlog=True) for item in envelope.backlog_items]
            return True, {
                "project_id": project_id,
                "requested_provider": envelope.requested_provider,
                "resolved_provider": envelope.resolved_provider,
                "strategy": envelope.strategy,
                "model": envelope.model,
                "template": context.template,
                "project_type": context.project_type,
                "bootstrap_policy": context.bootstrap_policy,
                "source_app": source_app,
                "dry_run": True,
                "plan_items": plan_items,
                "backlog_items": backlog_items,
                "total_task_count": len(plan_items) + len(backlog_items),
            }
        except Exception as exc:
            logger.warning(f"Failed to build bootstrap plan preview | project_id={project_id} | error={exc}")
            return False, {"error": str(exc)}

    def list_plans(self, *, project_id: str, limit: int = 20) -> tuple[bool, dict[str, Any]]:
        """List bootstrap plans for a project."""
        try:
            response = (
                self.supabase_client.table("archon_bootstrap_plans")
                .select("*")
                .eq("project_id", project_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            plans = response.data or []
            return True, {"plans": plans, "total_count": len(plans)}
        except Exception as exc:
            logger.warning(f"Failed to list bootstrap plans | project_id={project_id} | error={exc}")
            return False, {"error": str(exc)}

    def get_plan(self, plan_id: str) -> tuple[bool, dict[str, Any]]:
        """Get a single bootstrap plan by ID."""
        try:
            response = (
                self.supabase_client.table("archon_bootstrap_plans")
                .select("*")
                .eq("id", plan_id)
                .execute()
            )
            if response.data:
                return True, {"plan": response.data[0]}
            return False, {"error": f"Bootstrap plan {plan_id} not found"}
        except Exception as exc:
            logger.warning(f"Failed to fetch bootstrap plan | plan_id={plan_id} | error={exc}")
            return False, {"error": str(exc)}

    def materialize_backlog(self, plan_id: str) -> tuple[bool, dict[str, Any]]:
        """Materialize any not-yet-created backlog items from a persisted bootstrap plan."""
        ok, result = self.get_plan(plan_id)
        if not ok:
            return False, result

        plan = result["plan"]
        project_id = plan.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            return False, {"error": f"Bootstrap plan {plan_id} is missing project_id"}

        backlog_items = self._get_backlog_items(plan)
        if not backlog_items:
            return True, {"plan": plan, "created_tasks": [], "skipped": 0}

        existing_tasks = list(plan.get("created_tasks") or [])
        existing_keys = {
            str(task.get("plan_key"))
            for task in existing_tasks
            if isinstance(task.get("plan_key"), str) and str(task.get("plan_key")).strip()
        }
        existing_created_from = {
            str(task.get("created_from"))
            for task in existing_tasks
            if isinstance(task.get("created_from"), str) and str(task.get("created_from")).strip()
        }
        key_to_task_id = self._build_key_to_task_id(plan, existing_tasks)
        created_tasks: list[dict[str, Any]] = []
        skipped = 0
        task_order_start = len(existing_tasks)

        for index, item in enumerate(backlog_items):
            item_key = item.get("key")
            created_from = item.get("created_from")
            if (
                isinstance(item_key, str)
                and item_key in existing_keys
                or isinstance(created_from, str)
                and created_from in existing_created_from
            ):
                skipped += 1
                continue

            blocked_key = item.get("blocked_on_key")
            blocked_by: list[str] = []
            if isinstance(blocked_key, str):
                blocker_id = key_to_task_id.get(blocked_key)
                if blocker_id:
                    blocked_by = [blocker_id]

            tags = [str(tag) for tag in (item.get("tags") or [])]
            tags = list(dict.fromkeys([
                *tags,
                f"bootstrap-plan:{plan_id}",
                f"bootstrap-architect:{plan.get('requested_provider')}",
                f"bootstrap-architect-resolved:{plan.get('resolved_provider')}",
                f"bootstrap-plan-strategy:{plan.get('strategy')}",
            ]))

            task_payload = {
                "project_id": project_id,
                "title": item.get("title"),
                "description": item.get("description"),
                "status": "approved",
                "assignee": "Platform",
                "task_order": task_order_start + index,
                "priority": item.get("priority", "medium"),
                "task_type": item.get("task_type", "improvement"),
                "complexity": item.get("complexity", "simple"),
                "max_retries": item.get("max_retries", 1),
                "source_app": plan.get("source_app"),
                "created_by": "system",
                "created_from": item.get("created_from"),
                "execution_prompt": item.get("execution_prompt"),
                "acceptance_criteria": [{"text": text} for text in (item.get("acceptance_criteria") or [])],
                "blocked_by": blocked_by,
                "state_history": [],
                "state_changed_at": datetime.now().isoformat(),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "retry_count": 0,
                "tags": tags,
                "executed_by": None,
                "reviewed_by": [],
            }

            response = self.supabase_client.table("archon_tasks").insert(task_payload).execute()
            if not response.data:
                return False, {"error": f"Failed to materialize backlog task for bootstrap plan {plan_id}"}

            created = response.data[0]
            summary = {
                "id": created.get("id"),
                "plan_key": item.get("key"),
                "title": created.get("title", task_payload["title"]),
                "status": created.get("status", task_payload["status"]),
                "tags": created.get("tags", task_payload["tags"]),
                "created_from": created.get("created_from", task_payload["created_from"]),
                "blocked_by": created.get("blocked_by", blocked_by),
            }
            created_tasks.append(summary)
            if isinstance(item_key, str) and isinstance(summary.get("id"), str):
                key_to_task_id[item_key] = summary["id"]

        updated_created_tasks = [*existing_tasks, *created_tasks]
        status = "expanded" if created_tasks else plan.get("status", "materialized")
        self._update_plan_created_tasks_and_status(plan_id, updated_created_tasks, status)
        plan["created_tasks"] = updated_created_tasks
        plan["status"] = status
        plan["updated_at"] = datetime.now().isoformat()
        return True, {"plan": plan, "created_tasks": created_tasks, "skipped": skipped}

    def _attach_plan_tag_to_tasks(
        self,
        plan_id: str,
        created_tasks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Tag materialized bootstrap tasks with their owning bootstrap plan id."""
        plan_tag = f"bootstrap-plan:{plan_id}"
        updated_tasks: list[dict[str, Any]] = []

        for task in created_tasks:
            task_id = task.get("id")
            tags = [str(tag) for tag in (task.get("tags") or [])]
            if plan_tag not in tags:
                tags.append(plan_tag)

            if isinstance(task_id, str) and task_id:
                try:
                    (
                        self.supabase_client.table("archon_tasks")
                        .update({"tags": tags, "updated_at": datetime.now().isoformat()})
                        .eq("id", task_id)
                        .execute()
                    )
                except Exception as exc:
                    logger.warning(
                        f"Failed to update bootstrap task tag | task_id={task_id} | plan_id={plan_id} | error={exc}"
                    )

            updated_tasks.append({**task, "tags": tags})

        return updated_tasks

    def _update_plan_created_tasks(
        self,
        plan_id: str,
        created_tasks: list[dict[str, Any]],
        updated_at: str,
    ) -> None:
        """Keep the persisted bootstrap plan record in sync with tagged tasks."""
        try:
            (
                self.supabase_client.table("archon_bootstrap_plans")
                .update({"created_tasks": created_tasks, "updated_at": updated_at})
                .eq("id", plan_id)
                .execute()
            )
        except Exception as exc:
            logger.warning(
                f"Failed to update bootstrap plan created_tasks | plan_id={plan_id} | error={exc}"
            )

    def _update_plan_created_tasks_and_status(
        self,
        plan_id: str,
        created_tasks: list[dict[str, Any]],
        status: str,
    ) -> None:
        """Persist created task expansion state after later backlog materialization."""
        updated_at = datetime.now().isoformat()
        try:
            (
                self.supabase_client.table("archon_bootstrap_plans")
                .update({"created_tasks": created_tasks, "status": status, "updated_at": updated_at})
                .eq("id", plan_id)
                .execute()
            )
        except Exception as exc:
            logger.warning(
                f"Failed to update bootstrap plan status | plan_id={plan_id} | error={exc}"
            )

    @staticmethod
    def _get_backlog_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
        """Read normalized backlog items from persisted bootstrap-plan metadata."""
        metadata = plan.get("metadata")
        if not isinstance(metadata, dict):
            return []
        backlog_items = metadata.get("backlog_items")
        if not isinstance(backlog_items, list):
            return []
        return [item for item in backlog_items if isinstance(item, dict)]

    @staticmethod
    def _build_key_to_task_id(
        plan: dict[str, Any],
        created_tasks: list[dict[str, Any]],
    ) -> dict[str, str]:
        """Build a key -> task id map from persisted plan items and created task summaries."""
        mapping: dict[str, str] = {}
        plan_keys_by_created_from: dict[str, str] = {}

        for item in list(plan.get("plan_items") or []) + BootstrapPlanService._get_backlog_items(plan):
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            created_from = item.get("created_from")
            if isinstance(key, str) and isinstance(created_from, str):
                plan_keys_by_created_from[created_from] = key

        for task in created_tasks:
            if not isinstance(task, dict):
                continue
            task_id = task.get("id")
            if not isinstance(task_id, str) or not task_id:
                continue
            plan_key = task.get("plan_key")
            if isinstance(plan_key, str) and plan_key:
                mapping[plan_key] = task_id
                continue
            created_from = task.get("created_from")
            if isinstance(created_from, str) and created_from in plan_keys_by_created_from:
                mapping[plan_keys_by_created_from[created_from]] = task_id

        return mapping
