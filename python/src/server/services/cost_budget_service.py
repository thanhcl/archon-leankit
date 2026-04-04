"""
Cost Budget Service Module

Tracks cumulative cost per project per day/week and enforces budget limits.
Queries cost_usd from execution_runs (primary) or falls back to parsing
total_cost_usd from task execution_result. Provides budget enforcement
(warnings at 80%, pause at 100%) with fail-open behavior when cost data
is unavailable.
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from src.server.utils import get_supabase_client

from ..config.env_aliases import get_engine_default_daily_budget, get_engine_default_weekly_budget
from ..config.logfire_config import get_logger

logger = get_logger(__name__)

# Budget defaults read from environment variables
DEFAULT_DAILY_BUDGET = get_engine_default_daily_budget()
DEFAULT_WEEKLY_BUDGET = get_engine_default_weekly_budget()

# Aliases for backward-compatible references
DEFAULT_MAX_COST_PER_DAY = DEFAULT_DAILY_BUDGET
DEFAULT_MAX_COST_PER_SPRINT = DEFAULT_WEEKLY_BUDGET

BUDGET_WARNING_THRESHOLD = 0.8  # 80%


class CostBudgetService:
    """Tracks and enforces cost budgets per project."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    def get_cost_status(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """Get current cost status for a project including spend vs budget.

        Queries execution_runs as the primary cost source. Falls back to task
        execution_result parsing if execution_runs are unavailable (fail-open).
        """
        try:
            budget_config = self._get_budget_config(project_id)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # Primary: query execution_runs for accurate daily/weekly costs
            daily_cost, weekly_cost = self._fetch_costs_from_execution_runs(project_id)

            # Fallback breakdown from tasks for detailed daily_breakdown view
            tasks = self._fetch_project_tasks(project_id)
            daily_costs = self._accumulate_daily_costs(tasks)

            # Budget limits
            max_day = budget_config["daily_budget_usd"]
            max_week = budget_config["weekly_budget_usd"]

            daily_pct = (daily_cost / max_day) if max_day > 0 else 0.0
            weekly_pct = (weekly_cost / max_week) if max_week > 0 else 0.0

            daily_exceeded = daily_pct >= 1.0
            weekly_exceeded = weekly_pct >= 1.0
            daily_warning = daily_pct >= BUDGET_WARNING_THRESHOLD and not daily_exceeded
            weekly_warning = weekly_pct >= BUDGET_WARNING_THRESHOLD and not weekly_exceeded

            status = "ok"
            if daily_exceeded or weekly_exceeded:
                status = "exceeded"
            elif daily_warning or weekly_warning:
                status = "warning"

            return True, {
                "project_id": project_id,
                "status": status,
                "today": {
                    "date": today,
                    "cost_usd": round(daily_cost, 4),
                    "budget_usd": max_day,
                    "usage_pct": round(daily_pct * 100, 1),
                    "exceeded": daily_exceeded,
                    "warning": daily_warning,
                },
                "weekly": {
                    "total_cost_usd": round(weekly_cost, 4),
                    "budget_usd": max_week,
                    "usage_pct": round(weekly_pct * 100, 1),
                    "exceeded": weekly_exceeded,
                    "warning": weekly_warning,
                },
                # Keep sprint key as alias for backward compat with existing consumers
                "sprint": {
                    "total_cost_usd": round(weekly_cost, 4),
                    "budget_usd": max_week,
                    "usage_pct": round(weekly_pct * 100, 1),
                    "exceeded": weekly_exceeded,
                    "warning": weekly_warning,
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
            if status["weekly"]["exceeded"]:
                exceeded_parts.append(
                    f"weekly (${status['weekly']['total_cost_usd']:.2f}/${status['weekly']['budget_usd']:.2f})"
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

    def _fetch_costs_from_execution_runs(self, project_id: str) -> tuple[float, float]:
        """Query execution_runs for daily and weekly costs.

        Returns (daily_cost_usd, weekly_cost_usd). Fail-open: returns (0.0, 0.0)
        with a warning log if the query fails or cost_usd fields are null (CI-5 not yet available).
        """
        try:
            today = datetime.now(timezone.utc).date()
            week_start = today - timedelta(days=6)  # rolling 7-day window

            response = (
                self.supabase_client.table("archon_execution_runs")
                .select("cost_usd, started_at")
                .eq("project_id", project_id)
                .gte("started_at", week_start.isoformat())
                .execute()
            )

            runs = response.data or []

            # Warn when cost_usd is null — indicates CI-5 LLM metrics not yet populated
            null_count = sum(1 for r in runs if r.get("cost_usd") is None)
            if null_count > 0:
                logger.warning(
                    f"execution_runs has {null_count} rows with null cost_usd — "
                    f"LLM metrics (CI-5) not yet available, allowing execution | "
                    f"project_id={project_id}"
                )

            today_str = today.isoformat()
            daily_cost = 0.0
            weekly_cost = 0.0

            for run in runs:
                cost = run.get("cost_usd")
                if cost is None:
                    continue  # skip null rows; fail-open
                cost_f = float(cost)
                weekly_cost += cost_f
                started_at = run.get("started_at") or ""
                if started_at.startswith(today_str):
                    daily_cost += cost_f

            return daily_cost, weekly_cost

        except Exception as e:
            logger.warning(
                f"Failed to fetch costs from execution_runs, allowing execution | "
                f"project_id={project_id} | error={e}"
            )
            return 0.0, 0.0

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

    def _get_budget_config(self, project_id: str | None) -> dict[str, float]:
        """Get budget configuration for a project.

        Priority order (highest wins):
        1. engine_policy.budget_policy (C-P6-01 — canonical policy source)
        2. project metadata.cost_budget (legacy)
        3. environment variable defaults

        Supports both new field names (daily_budget_usd, weekly_budget_usd) and legacy names
        (max_cost_per_day, max_cost_per_sprint).
        """
        if project_id:
            # 1. Check engine_policy.budget_policy first (C-P6-01)
            try:
                from .projects.engine_policy_service import EnginePolicyService
                policy_service = EnginePolicyService(supabase_client=self.supabase_client)
                policy = policy_service.get_active_policy(project_id)
                if policy:
                    budget_policy = policy.get("budget_policy") or {}
                    ep_daily = budget_policy.get("daily_limit_usd")
                    ep_weekly = budget_policy.get("sprint_limit_usd")
                    if ep_daily is not None or ep_weekly is not None:
                        daily = float(ep_daily) if ep_daily is not None else DEFAULT_DAILY_BUDGET
                        weekly = float(ep_weekly) if ep_weekly is not None else DEFAULT_WEEKLY_BUDGET
                        return {"daily_budget_usd": daily, "weekly_budget_usd": weekly}
            except Exception as e:
                logger.debug(f"Could not read engine_policy budget for project {project_id}: {e}")

            # 2. Fallback to project metadata (legacy)
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
                        daily = float(
                            budget.get("daily_budget_usd")
                            or budget.get("max_cost_per_day")
                            or DEFAULT_DAILY_BUDGET
                        )
                        weekly = float(
                            budget.get("weekly_budget_usd")
                            or budget.get("max_cost_per_sprint")
                            or DEFAULT_WEEKLY_BUDGET
                        )
                        return {"daily_budget_usd": daily, "weekly_budget_usd": weekly}
            except Exception as e:
                logger.debug(f"Could not read budget config for project {project_id}: {e}")

        return {"daily_budget_usd": DEFAULT_DAILY_BUDGET, "weekly_budget_usd": DEFAULT_WEEKLY_BUDGET}

    def update_budget_config(self, project_id: str, daily_budget_usd: float, weekly_budget_usd: float) -> tuple[bool, dict]:
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
                    "daily_budget_usd": daily_budget_usd,
                    "weekly_budget_usd": weekly_budget_usd,
                },
            }

            self.supabase_client.table("archon_projects").update(
                {"metadata": updated_metadata}
            ).eq("id", project_id).execute()

            config = {"daily_budget_usd": daily_budget_usd, "weekly_budget_usd": weekly_budget_usd}
            logger.info(
                f"Budget config updated | project_id={project_id} | "
                f"daily_budget_usd=${daily_budget_usd} | weekly_budget_usd=${weekly_budget_usd}"
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
