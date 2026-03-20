"""
Cost Budget Service Module

Tracks cumulative cost per project per sprint and enforces budget limits.
Parses total_cost_usd from task execution_result, accumulates daily/sprint costs,
and provides budget enforcement (warnings at 80%, pause at 100%).
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from src.server.utils import get_supabase_client

from ..config.logfire_config import get_logger

logger = get_logger(__name__)

# Budget defaults
DEFAULT_MAX_COST_PER_DAY = 100.0
DEFAULT_MAX_COST_PER_SPRINT = 500.0
BUDGET_WARNING_THRESHOLD = 0.8  # 80%


class CostBudgetService:
    """Tracks and enforces cost budgets per project."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    def get_cost_status(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """Get current cost status for a project including spend vs budget."""
        try:
            tasks = self._fetch_project_tasks(project_id)
            budget_config = self._get_budget_config(project_id)

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # Accumulate costs
            daily_costs = self._accumulate_daily_costs(tasks)
            total_cost = sum(daily_costs.values())
            today_cost = daily_costs.get(today, 0.0)

            # Budget status
            max_day = budget_config["max_cost_per_day"]
            max_sprint = budget_config["max_cost_per_sprint"]

            daily_pct = (today_cost / max_day) if max_day > 0 else 0.0
            sprint_pct = (total_cost / max_sprint) if max_sprint > 0 else 0.0

            daily_exceeded = daily_pct >= 1.0
            sprint_exceeded = sprint_pct >= 1.0
            daily_warning = daily_pct >= BUDGET_WARNING_THRESHOLD and not daily_exceeded
            sprint_warning = sprint_pct >= BUDGET_WARNING_THRESHOLD and not sprint_exceeded

            status = "ok"
            if daily_exceeded or sprint_exceeded:
                status = "exceeded"
            elif daily_warning or sprint_warning:
                status = "warning"

            return True, {
                "project_id": project_id,
                "status": status,
                "today": {
                    "date": today,
                    "cost_usd": round(today_cost, 4),
                    "budget_usd": max_day,
                    "usage_pct": round(daily_pct * 100, 1),
                    "exceeded": daily_exceeded,
                    "warning": daily_warning,
                },
                "sprint": {
                    "total_cost_usd": round(total_cost, 4),
                    "budget_usd": max_sprint,
                    "usage_pct": round(sprint_pct * 100, 1),
                    "exceeded": sprint_exceeded,
                    "warning": sprint_warning,
                },
                "daily_breakdown": {
                    date: round(cost, 4)
                    for date, cost in sorted(daily_costs.items())
                },
                "budget_config": budget_config,
            }

        except Exception as e:
            logger.error(f"Error getting cost status for project {project_id}: {e}", exc_info=True)
            return False, {"error": f"Error getting cost status: {str(e)}"}

    def check_budget(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """Check if a project is within budget. Returns (allowed, details).

        Used by task engine before spawning a new CC session.
        """
        ok, status = self.get_cost_status(project_id)
        if not ok:
            # If we can't check budget, allow execution (fail-open for availability)
            logger.warning(f"Budget check failed, allowing execution | project_id={project_id}")
            return True, {"allowed": True, "reason": "budget_check_failed"}

        if status["status"] == "exceeded":
            exceeded_parts = []
            if status["today"]["exceeded"]:
                exceeded_parts.append(
                    f"daily (${status['today']['cost_usd']:.2f}/${status['today']['budget_usd']:.2f})"
                )
            if status["sprint"]["exceeded"]:
                exceeded_parts.append(
                    f"sprint (${status['sprint']['total_cost_usd']:.2f}/${status['sprint']['budget_usd']:.2f})"
                )
            reason = f"Budget exceeded: {', '.join(exceeded_parts)}"
            return False, {"allowed": False, "reason": reason, "status": status}

        if status["status"] == "warning":
            return True, {"allowed": True, "reason": "approaching_limit", "status": status}

        return True, {"allowed": True, "reason": "within_budget", "status": status}

    def record_task_cost(self, project_id: str, task_id: str, execution_result: dict[str, Any]) -> float:
        """Parse and return the cost from an execution result. Cost is already stored
        in the task's execution_result — this method just extracts it for logging."""
        cost = self._parse_cost(execution_result)
        if cost > 0:
            logger.info(f"Task cost recorded | project_id={project_id} | task_id={task_id} | cost=${cost:.4f}")
        return cost

    def _fetch_project_tasks(self, project_id: str) -> list[dict]:
        """Fetch all non-archived tasks for a project that have execution results."""
        response = (
            self.supabase_client.table("archon_tasks")
            .select("id, status, execution_result, state_changed_at, updated_at, created_at")
            .eq("project_id", project_id)
            .or_("archived.is.null,archived.is.false")
            .execute()
        )
        return response.data or []

    def _get_budget_config(self, project_id: str) -> dict[str, float]:
        """Get budget configuration for a project.

        Reads from the project's metadata if available, otherwise uses defaults.
        """
        try:
            response = (
                self.supabase_client.table("archon_projects")
                .select("metadata")
                .eq("id", project_id)
                .single()
                .execute()
            )
            metadata = response.data.get("metadata") if response.data else None
            if isinstance(metadata, dict):
                budget = metadata.get("cost_budget", {})
                if isinstance(budget, dict):
                    return {
                        "max_cost_per_day": float(budget.get("max_cost_per_day", DEFAULT_MAX_COST_PER_DAY)),
                        "max_cost_per_sprint": float(budget.get("max_cost_per_sprint", DEFAULT_MAX_COST_PER_SPRINT)),
                    }
        except Exception as e:
            logger.debug(f"Could not read budget config for project {project_id}: {e}")

        return {
            "max_cost_per_day": DEFAULT_MAX_COST_PER_DAY,
            "max_cost_per_sprint": DEFAULT_MAX_COST_PER_SPRINT,
        }

    def update_budget_config(self, project_id: str, max_cost_per_day: float, max_cost_per_sprint: float) -> tuple[bool, dict]:
        """Update budget configuration for a project by writing to project metadata."""
        try:
            # Read current metadata to preserve other fields
            response = (
                self.supabase_client.table("archon_projects")
                .select("metadata")
                .eq("id", project_id)
                .single()
                .execute()
            )
            if not response.data:
                return False, {"error": f"Project {project_id} not found"}

            existing_metadata = response.data.get("metadata") or {}
            if not isinstance(existing_metadata, dict):
                existing_metadata = {}

            updated_metadata = {
                **existing_metadata,
                "cost_budget": {
                    "max_cost_per_day": max_cost_per_day,
                    "max_cost_per_sprint": max_cost_per_sprint,
                },
            }

            self.supabase_client.table("archon_projects").update(
                {"metadata": updated_metadata}
            ).eq("id", project_id).execute()

            config = {"max_cost_per_day": max_cost_per_day, "max_cost_per_sprint": max_cost_per_sprint}
            logger.info(
                f"Budget config updated | project_id={project_id} | "
                f"max_cost_per_day=${max_cost_per_day} | max_cost_per_sprint=${max_cost_per_sprint}"
            )
            return True, {"budget_config": config}

        except Exception as e:
            logger.error(f"Failed to update budget config for project {project_id}: {e}", exc_info=True)
            return False, {"error": f"Failed to update budget config: {str(e)}"}

    def _accumulate_daily_costs(self, tasks: list[dict]) -> dict[str, float]:
        """Group task costs by completion date."""
        daily: dict[str, float] = defaultdict(float)

        for task in tasks:
            cost = self._parse_cost_from_task(task)
            if cost <= 0:
                continue

            date_str = self._get_task_date(task)
            daily[date_str] += cost

        return dict(daily)

    def _get_task_date(self, task: dict) -> str:
        """Get the date for a task (state_changed_at or updated_at)."""
        date_field = task.get("state_changed_at") or task.get("updated_at") or task.get("created_at", "")
        if not date_field:
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            dt = datetime.fromisoformat(date_field.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _parse_cost_from_task(self, task: dict) -> float:
        """Parse cost from a task's execution_result."""
        result = task.get("execution_result")
        if not result:
            return 0.0
        return self._parse_cost(result if isinstance(result, dict) else self._try_parse_json(result) or {})

    def _parse_cost(self, execution_result: dict[str, Any]) -> float:
        """Parse total_cost_usd from execution result."""
        if not execution_result:
            return 0.0

        # Direct field
        cost = execution_result.get("total_cost_usd")
        if cost is not None:
            return self._to_float(cost)

        # Check stdout for JSON with total_cost_usd
        stdout = execution_result.get("stdout", "")
        if isinstance(stdout, str) and "total_cost_usd" in stdout:
            match = re.search(r'"total_cost_usd"\s*:\s*([\d.]+)', stdout)
            if match:
                return self._to_float(match.group(1))

        return 0.0

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _try_parse_json(value: Any) -> dict | None:
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, ValueError):
                return None
        return None
