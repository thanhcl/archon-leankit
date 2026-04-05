"""
Coordinator Service for parent-child task orchestration (C-P7-01 through C-P7-03).

Manages the coordinator pattern where a complex task is decomposed into
parallel child tasks, each executing independently with their own contracts
and execution runs. The parent (coordinator) task aggregates child results
and transitions based on children's collective outcome.

Usage:
    coordinator = CoordinatorService(task_service, lifecycle_service, notifier)

    # Create coordinator with children
    children = await coordinator.create_coordinator_children(
        parent_task_id="t1",
        children_specs=[
            {"title": "Sub-task A", "description": "...", "priority": "high"},
            {"title": "Sub-task B", "description": "...", "blocked_by": ["child-a-id"]},
        ],
    )

    # Check if parent should advance based on children status
    should_advance, status = await coordinator.evaluate_parent_status("t1")
"""

from __future__ import annotations

from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Terminal statuses where a child task is considered "finished"
_TERMINAL_STATUSES = {"done", "cancelled", "failed"}


class CoordinatorService:
    """Manages parent-child task coordination."""

    def __init__(self, task_service, lifecycle_service=None, notifier=None, execution_run_service=None):
        self.task_service = task_service
        self.lifecycle_service = lifecycle_service
        self.notifier = notifier
        self.execution_run_service = execution_run_service

    async def create_coordinator_children(
        self,
        parent_task_id: str,
        children_specs: list[dict[str, Any]],
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Create child tasks for a coordinator parent.

        Each child inherits project_id, plan_item_id, and sprint_batch_id
        from the parent. blocked_by between siblings enables ordered execution.

        Args:
            parent_task_id: ID of the coordinator parent task
            children_specs: List of dicts with at least {title, description}.
                Optional: priority, blocked_by, allowed_paths, acceptance_criteria
            project_id: Inherited from parent if not provided in spec

        Returns:
            List of created child task dicts.
        """
        # Get parent task to inherit fields
        ok, parent_result = self.task_service.get_task(parent_task_id)
        if not ok:
            logger.error(f"Cannot fetch parent task for coordinator | task_id={parent_task_id}")
            return []

        parent = parent_result["task"]
        inherit_project_id = project_id or parent.get("project_id")
        inherit_plan_item_id = parent.get("plan_item_id")
        inherit_sprint_batch_id = parent.get("sprint_batch_id")

        # Mark parent as coordinator
        await self.task_service.update_task(
            task_id=parent_task_id,
            update_fields={"decomposition_mode": "coordinator"},
        )

        created_children: list[dict[str, Any]] = []
        for spec in children_specs:
            child_data = {
                "project_id": inherit_project_id,
                "parent_task_id": parent_task_id,
                "title": spec["title"],
                "description": spec.get("description", ""),
                "priority": spec.get("priority", parent.get("priority", "medium")),
                "status": "proposed",
                "blocked_by": spec.get("blocked_by", []),
                "allowed_paths": spec.get("allowed_paths", parent.get("allowed_paths", [])),
                "forbidden_paths": spec.get("forbidden_paths", parent.get("forbidden_paths", [])),
                "acceptance_criteria": spec.get("acceptance_criteria", []),
                "tags": spec.get("tags", parent.get("tags", [])),
                "task_type": spec.get("task_type", parent.get("task_type")),
                "complexity": spec.get("complexity", "simple"),
            }
            if inherit_plan_item_id:
                child_data["plan_item_id"] = inherit_plan_item_id
            if inherit_sprint_batch_id:
                child_data["sprint_batch_id"] = inherit_sprint_batch_id

            ok, result = await self.task_service.create_task(**child_data)
            if ok:
                child = result.get("task", {})
                created_children.append(child)
                logger.info(
                    f"Coordinator child created | parent={parent_task_id} | "
                    f"child={child.get('id')} | title={spec['title']}"
                )
            else:
                logger.error(
                    f"Failed to create coordinator child | parent={parent_task_id} | "
                    f"title={spec['title']} | error={result.get('error')}"
                )

        if self.notifier and created_children:
            await self.notifier.emit(
                "task.coordinator_children_created",
                task_id=parent_task_id,
                data={
                    "parent_task_id": parent_task_id,
                    "children_count": len(created_children),
                    "children_ids": [c.get("id") for c in created_children],
                    "project_id": inherit_project_id,
                },
            )

            # Emit agent.spawned events for sub-agent lineage (C-P7-04)
            for child in created_children:
                await self.notifier.emit(
                    "agent.spawned",
                    task_id=child.get("id", ""),
                    data={
                        "agent_id": child.get("id"),
                        "parent_agent_id": parent_task_id,
                        "parent_task_id": parent_task_id,
                        "project_id": inherit_project_id,
                        "title": child.get("title"),
                    },
                )

        return created_children

    async def evaluate_parent_status(
        self,
        parent_task_id: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Evaluate whether a coordinator parent should transition based on children.

        Returns:
            (should_transition, details) where details includes:
            - next_status: what the parent should transition to
            - children_summary: {total, done, failed, cancelled, in_progress}
            - all_terminal: whether all children are in terminal states
        """
        ok, result = self.task_service.get_subtasks(parent_task_id)
        if not ok:
            return False, {"error": "Failed to fetch subtasks"}

        children = result.get("tasks", [])
        if not children:
            return False, {"reason": "no_children"}

        summary: dict[str, Any] = {
            "total": len(children),
            "done": 0,
            "failed": 0,
            "cancelled": 0,
            "in_progress": 0,
            "total_cost_usd": 0.0,
        }

        for child in children:
            status = child.get("status", "")
            if status == "done":
                summary["done"] += 1
            elif status == "failed":
                summary["failed"] += 1
            elif status == "cancelled":
                summary["cancelled"] += 1
            elif status not in _TERMINAL_STATUSES:
                summary["in_progress"] += 1

        # Cost rollup: aggregate child run costs
        if self.execution_run_service:
            for child in children:
                child_id = child.get("id")
                if not child_id:
                    continue
                try:
                    ok, runs_result = self.execution_run_service.list_runs(
                        task_id=child_id, limit=50,
                    )
                    if ok:
                        for run in runs_result.get("runs", []):
                            cost = run.get("cost_usd") or 0.0
                            summary["total_cost_usd"] += float(cost)
                except Exception:
                    pass
            summary["total_cost_usd"] = round(summary["total_cost_usd"], 4)

        all_terminal = summary["in_progress"] == 0
        if not all_terminal:
            return False, {"reason": "children_still_running", "summary": summary}

        # All children terminal — determine parent next status
        if summary["done"] > 0 and summary["failed"] == 0:
            # TD-3: All done → aggregate qa-eval review before owner review.
            # Parent NEVER auto-completes — aggregate evaluator checks
            # cross-subtask integration and parent acceptance criteria.
            return True, {
                "next_status": "code-review",
                "reason": "all_children_completed_aggregate_review",
                "summary": summary,
                "aggregate_review": True,
            }
        elif summary["done"] > 0:
            # TD-3: Mixed done/failed → aggregate review with warnings.
            # Evaluator identifies which subtask(s) need rework.
            return True, {
                "next_status": "code-review",
                "reason": "children_mixed_results_aggregate_review",
                "summary": summary,
                "aggregate_review": True,
            }
        else:
            # All failed/cancelled — parent fails
            return True, {
                "next_status": "failed",
                "reason": "all_children_failed",
                "summary": summary,
            }

    async def advance_parent_if_ready(
        self,
        parent_task_id: str,
    ) -> bool:
        """Check and advance parent coordinator task if all children are terminal.

        Called after each child task completes. Returns True if parent was advanced.
        """
        should_transition, details = await self.evaluate_parent_status(parent_task_id)
        if not should_transition:
            return False

        next_status = details.get("next_status")
        reason = details.get("reason", "")
        summary = details.get("summary", {})

        if self.lifecycle_service:
            ok, _ = await self.lifecycle_service.execute_transition(
                task_id=parent_task_id,
                new_status=next_status,
                changed_by="coordinator",
                reason=f"Coordinator: {reason} | "
                       f"done={summary.get('done')}/{summary.get('total')} | "
                       f"failed={summary.get('failed')}",
            )
            if ok:
                logger.info(
                    f"Coordinator parent advanced | task_id={parent_task_id} | "
                    f"status={next_status} | {reason}"
                )

                if self.notifier:
                    await self.notifier.emit(
                        "task.coordinator_aggregated",
                        task_id=parent_task_id,
                        data={
                            "parent_task_id": parent_task_id,
                            "next_status": next_status,
                            "children_summary": summary,
                        },
                    )
                return True

        return False

    def get_coordinator_children(
        self,
        parent_task_id: str,
    ) -> list[dict[str, Any]]:
        """Get all children of a coordinator task."""
        ok, result = self.task_service.get_subtasks(parent_task_id)
        if not ok:
            return []
        return result.get("tasks", [])
