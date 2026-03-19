"""
Sprint Stats Service Module

Computes sprint statistics for a project by analyzing task completion data,
grouping done tasks by date, and calculating metrics like first-pass rate,
average retries, duration, and estimated cost.
"""

import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from src.server.utils import get_supabase_client

from ..config.logfire_config import get_logger

logger = get_logger(__name__)


class SprintStatsService:
    """Service for computing sprint statistics from project tasks."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    VALID_GROUP_BY = {"date", "task_type", "module", "sprint", "feature", "phase"}

    def get_sprint_stats(self, project_id: str, group_by: str = "date") -> tuple[bool, dict[str, Any]]:
        """
        Get sprint statistics for a project.

        Groups done tasks by the specified dimension and computes metrics including
        first-pass rate, average retries, average duration, and estimated cost.

        Args:
            project_id: Project UUID
            group_by: Grouping dimension - one of: date, task_type, module, sprint, feature, phase
        """
        try:
            if group_by not in self.VALID_GROUP_BY:
                return False, {
                    "error": f"Invalid group_by '{group_by}'. Must be one of: {', '.join(sorted(self.VALID_GROUP_BY))}"
                }

            # Query all tasks for the project
            response = (
                self.supabase_client.table("archon_tasks")
                .select(
                    "id, title, status, retry_count, created_at, updated_at, "
                    "state_changed_at, execution_result, complexity, priority, "
                    "task_type, module, sprint, feature, phase"
                )
                .eq("project_id", project_id)
                .or_("archived.is.null,archived.is.false")
                .execute()
            )

            all_tasks = response.data or []
            done_tasks = [t for t in all_tasks if t.get("status") == "done"]

            summary = self._compute_summary(all_tasks, done_tasks)

            if group_by == "date":
                sprints = self._compute_sprints(done_tasks)
            else:
                sprints = self._compute_sprints_by_field(done_tasks, group_by)

            trends = self._compute_trends(sprints)
            top_learnings = self._extract_top_learnings(done_tasks)
            code_patterns_count = self._count_code_patterns(done_tasks)
            injection_metrics = self._compute_injection_metrics(done_tasks)

            return True, {
                "project_id": project_id,
                "group_by": group_by,
                "summary": summary,
                "sprints": sprints,
                "trends": trends,
                "top_learnings": top_learnings,
                "code_patterns_count": code_patterns_count,
                "injection_metrics": injection_metrics,
            }

        except Exception as e:
            logger.error(f"Error computing sprint stats for project {project_id}: {e}", exc_info=True)
            return False, {"error": f"Error computing sprint stats: {str(e)}"}

    def _compute_summary(self, all_tasks: list[dict], done_tasks: list[dict]) -> dict[str, Any]:
        """Compute overall summary metrics."""
        total = len(all_tasks)
        done_count = len(done_tasks)

        if done_count == 0:
            return {
                "total_tasks": total,
                "done": 0,
                "first_pass_rate": 0.0,
                "avg_retries": 0.0,
                "avg_duration_hours": 0.0,
                "estimated_cost_usd": 0.0,
            }

        first_pass = sum(1 for t in done_tasks if t.get("retry_count", 0) <= 1)
        total_retries = sum(t.get("retry_count", 0) for t in done_tasks)
        total_duration = sum(self._calc_duration_hours(t) for t in done_tasks)
        total_cost = sum(self._parse_cost(t) for t in done_tasks)

        return {
            "total_tasks": total,
            "done": done_count,
            "first_pass_rate": round(first_pass / done_count, 4),
            "avg_retries": round(total_retries / done_count, 2),
            "avg_duration_hours": round(total_duration / done_count, 2),
            "estimated_cost_usd": round(total_cost, 2),
        }

    def _compute_sprints(self, done_tasks: list[dict]) -> list[dict[str, Any]]:
        """Group done tasks by completion date and compute per-day metrics."""
        by_date: dict[str, list[dict]] = defaultdict(list)

        for task in done_tasks:
            date_str = self._get_completion_date(task)
            by_date[date_str].append(task)

        sprints = []
        for date_str in sorted(by_date.keys()):
            tasks = by_date[date_str]
            count = len(tasks)
            first_pass = sum(1 for t in tasks if t.get("retry_count", 0) <= 1)
            total_retries = sum(t.get("retry_count", 0) for t in tasks)
            total_duration = sum(self._calc_duration_hours(t) for t in tasks)
            total_cost = sum(self._parse_cost(t) for t in tasks)

            sprints.append({
                "date": date_str,
                "tasks_completed": count,
                "first_pass_rate": round(first_pass / count, 4) if count else 0.0,
                "avg_retries": round(total_retries / count, 2) if count else 0.0,
                "avg_duration_hours": round(total_duration / count, 2) if count else 0.0,
                "estimated_cost_usd": round(total_cost, 2),
            })

        return sprints

    def _compute_sprints_by_field(self, done_tasks: list[dict], field: str) -> list[dict[str, Any]]:
        """Group done tasks by a categorical field and compute per-group metrics."""
        by_group: dict[str, list[dict]] = defaultdict(list)

        for task in done_tasks:
            group_value = task.get(field) or "unassigned"
            by_group[group_value].append(task)

        sprints = []
        for group_value in sorted(by_group.keys()):
            tasks = by_group[group_value]
            count = len(tasks)
            first_pass = sum(1 for t in tasks if t.get("retry_count", 0) <= 1)
            total_retries = sum(t.get("retry_count", 0) for t in tasks)
            total_duration = sum(self._calc_duration_hours(t) for t in tasks)
            total_cost = sum(self._parse_cost(t) for t in tasks)

            sprints.append({
                "date": group_value,  # reuse "date" key for compatibility
                "tasks_completed": count,
                "first_pass_rate": round(first_pass / count, 4) if count else 0.0,
                "avg_retries": round(total_retries / count, 2) if count else 0.0,
                "avg_duration_hours": round(total_duration / count, 2) if count else 0.0,
                "estimated_cost_usd": round(total_cost, 2),
            })

        return sprints

    def _compute_trends(self, sprints: list[dict[str, Any]]) -> dict[str, Any]:
        """Compare the last two sprint date groups to show trends."""
        if len(sprints) < 2:
            return {"available": False, "message": "Need at least 2 date groups for trends"}

        prev = sprints[-2]
        curr = sprints[-1]

        def delta(key: str) -> float:
            return round(curr.get(key, 0) - prev.get(key, 0), 4)

        return {
            "available": True,
            "previous_date": prev["date"],
            "current_date": curr["date"],
            "tasks_completed_delta": delta("tasks_completed"),
            "first_pass_rate_delta": delta("first_pass_rate"),
            "avg_retries_delta": delta("avg_retries"),
            "avg_duration_hours_delta": delta("avg_duration_hours"),
            "estimated_cost_usd_delta": delta("estimated_cost_usd"),
        }

    def _extract_top_learnings(self, done_tasks: list[dict], limit: int = 5) -> list[str]:
        """Extract top learnings from execution results of done tasks."""
        learnings = []
        for task in done_tasks:
            result = task.get("execution_result")
            if not result:
                continue
            result_data = result if isinstance(result, dict) else self._try_parse_json(result)
            if not result_data:
                continue

            # Look for learnings in common result structures
            for key in ("learnings", "lessons_learned", "top_learnings"):
                val = result_data.get(key)
                if isinstance(val, list):
                    learnings.extend(str(item) for item in val)
                elif isinstance(val, str) and val:
                    learnings.append(val)

        return learnings[:limit]

    def _count_code_patterns(self, done_tasks: list[dict]) -> int:
        """Count total code patterns found across done tasks."""
        count = 0
        for task in done_tasks:
            result = task.get("execution_result")
            if not result:
                continue
            result_data = result if isinstance(result, dict) else self._try_parse_json(result)
            if not result_data:
                continue

            patterns = result_data.get("code_patterns", [])
            if isinstance(patterns, list):
                count += len(patterns)

        return count

    def _compute_injection_metrics(self, done_tasks: list[dict]) -> dict[str, Any]:
        """Compute injection metrics: avg learnings/patterns/kb_chunks per task,
        and compare retry rates for tasks with vs without injection."""
        with_injection: list[dict] = []
        without_injection: list[dict] = []

        total_learnings = 0
        total_patterns = 0
        total_kb_chunks = 0
        total_tokens = 0
        injected_count = 0

        for task in done_tasks:
            result = task.get("execution_result")
            if not result:
                without_injection.append(task)
                continue
            result_data = result if isinstance(result, dict) else self._try_parse_json(result)
            if not result_data:
                without_injection.append(task)
                continue

            injection = result_data.get("injection")
            if isinstance(injection, dict) and any(
                injection.get(k, 0) > 0 for k in ("learnings", "patterns", "kb_chunks")
            ):
                with_injection.append(task)
                injected_count += 1
                total_learnings += injection.get("learnings", 0)
                total_patterns += injection.get("patterns", 0)
                total_kb_chunks += injection.get("kb_chunks", 0)
                total_tokens += injection.get("tokens", 0)
            else:
                without_injection.append(task)

        def _avg_retries(tasks: list[dict]) -> float:
            if not tasks:
                return 0.0
            return round(sum(t.get("retry_count", 0) for t in tasks) / len(tasks), 2)

        return {
            "tasks_with_injection": injected_count,
            "tasks_without_injection": len(without_injection),
            "avg_learnings_per_task": round(total_learnings / injected_count, 2) if injected_count else 0.0,
            "avg_patterns_per_task": round(total_patterns / injected_count, 2) if injected_count else 0.0,
            "avg_kb_chunks_per_task": round(total_kb_chunks / injected_count, 2) if injected_count else 0.0,
            "avg_tokens_per_task": round(total_tokens / injected_count, 2) if injected_count else 0.0,
            "retry_rate_with_injection": _avg_retries(with_injection),
            "retry_rate_without_injection": _avg_retries(without_injection),
        }

    def _get_completion_date(self, task: dict) -> str:
        """Get the date a task was completed (state_changed_at or updated_at)."""
        date_field = task.get("state_changed_at") or task.get("updated_at") or task.get("created_at", "")
        if not date_field:
            return "unknown"
        try:
            dt = datetime.fromisoformat(date_field.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            return "unknown"

    def _calc_duration_hours(self, task: dict) -> float:
        """Calculate task duration in hours from created_at to state_changed_at/updated_at."""
        start_str = task.get("created_at", "")
        end_str = task.get("state_changed_at") or task.get("updated_at", "")
        if not start_str or not end_str:
            return 0.0
        try:
            start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            delta = (end - start).total_seconds() / 3600.0
            return max(delta, 0.0)
        except (ValueError, AttributeError):
            return 0.0

    def _parse_cost(self, task: dict) -> float:
        """Parse total_cost_usd from execution_result stdout JSON."""
        result = task.get("execution_result")
        if not result:
            return 0.0

        result_data = result if isinstance(result, dict) else self._try_parse_json(result)
        if not result_data:
            return 0.0

        # Direct field
        cost = result_data.get("total_cost_usd")
        if cost is not None:
            return self._to_float(cost)

        # Check stdout for JSON with total_cost_usd
        stdout = result_data.get("stdout", "")
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
