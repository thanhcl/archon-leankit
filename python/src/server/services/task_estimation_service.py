"""
Task Estimation Service

Predicts task duration and cost based on historical data from completed tasks.
Uses weighted averaging that favors recent tasks for improved accuracy over time.
"""

import json
import re
from datetime import datetime
from typing import Any

from src.server.utils import get_supabase_client

from ..config.logfire_config import get_logger

logger = get_logger(__name__)

# Weight decay factor: more recent tasks get higher weight
RECENCY_DECAY = 0.85  # Each older task gets 85% of the previous task's weight
MIN_SAMPLE_SIZE = 2   # Minimum completed tasks needed for a meaningful estimate


class TaskEstimationService:
    """Predicts task duration and cost from historical completed tasks."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    def estimate_task(
        self,
        project_id: str,
        complexity: str = "simple",
        priority: str = "medium",
    ) -> tuple[bool, dict[str, Any]]:
        """
        Estimate duration and cost for a task based on similar completed tasks.

        Finds completed tasks in the same project with matching complexity/priority,
        then computes weighted averages favoring recent completions.

        Returns:
            (success, {"estimate_duration_seconds": float, "estimate_cost_usd": float,
                        "sample_size": int, "confidence": str})
        """
        try:
            done_tasks = self._query_completed_tasks(project_id)

            if not done_tasks:
                return True, self._empty_estimate("No completed tasks in project")

            # Score and filter tasks by similarity
            scored = self._score_tasks(done_tasks, complexity, priority)

            if len(scored) < MIN_SAMPLE_SIZE:
                # Fall back to all done tasks if not enough similar ones
                scored = [(t, 1.0) for t in done_tasks]

            if not scored:
                return True, self._empty_estimate("No tasks with duration data")

            # Sort by completion date (most recent first) for recency weighting
            scored.sort(key=lambda x: self._get_completion_timestamp(x[0]), reverse=True)

            est_duration = self._weighted_average(
                scored, lambda t: self._calc_duration_seconds(t)
            )
            est_cost = self._weighted_average(
                scored, lambda t: self._parse_cost(t)
            )

            sample_size = len(scored)
            confidence = self._compute_confidence(sample_size, scored, complexity, priority)

            return True, {
                "estimate_duration_seconds": round(est_duration, 0),
                "estimate_cost_usd": round(est_cost, 4),
                "sample_size": sample_size,
                "confidence": confidence,
            }

        except Exception as e:
            logger.error(f"Error estimating task for project {project_id}: {e}", exc_info=True)
            return False, {"error": f"Error estimating task: {str(e)}"}

    def get_project_estimates(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """
        Get estimates for all non-terminal tasks in a project.

        Returns a dict keyed by task_id with estimate data, plus
        a predicted_vs_actual comparison for completed tasks.
        """
        try:
            all_tasks = self._query_all_tasks(project_id)
            done_tasks = [t for t in all_tasks if t.get("status") == "done"]
            active_tasks = [
                t for t in all_tasks
                if t.get("status") not in ("done", "cancelled")
            ]

            # Build estimates for active tasks
            estimates: dict[str, dict] = {}
            for task in active_tasks:
                complexity = task.get("complexity", "simple")
                priority = task.get("priority", "medium")
                _, est = self.estimate_task(project_id, complexity, priority)
                estimates[task["id"]] = {
                    "task_id": task["id"],
                    "title": task.get("title", ""),
                    **est,
                }

            # Build predicted vs actual for done tasks
            predicted_vs_actual = self._compute_predicted_vs_actual(done_tasks)

            return True, {
                "project_id": project_id,
                "estimates": estimates,
                "predicted_vs_actual": predicted_vs_actual,
            }

        except Exception as e:
            logger.error(f"Error getting project estimates for {project_id}: {e}", exc_info=True)
            return False, {"error": f"Error getting project estimates: {str(e)}"}

    def _query_completed_tasks(self, project_id: str) -> list[dict]:
        """Query all completed tasks for a project."""
        response = (
            self.supabase_client.table("archon_tasks")
            .select(
                "id, title, status, complexity, priority, created_at, updated_at, "
                "state_changed_at, execution_result, retry_count"
            )
            .eq("project_id", project_id)
            .eq("status", "done")
            .or_("archived.is.null,archived.is.false")
            .execute()
        )
        return response.data or []

    def _query_all_tasks(self, project_id: str) -> list[dict]:
        """Query all non-archived tasks for a project."""
        response = (
            self.supabase_client.table("archon_tasks")
            .select(
                "id, title, status, complexity, priority, created_at, updated_at, "
                "state_changed_at, execution_result, retry_count"
            )
            .eq("project_id", project_id)
            .or_("archived.is.null,archived.is.false")
            .execute()
        )
        return response.data or []

    def _score_tasks(
        self, tasks: list[dict], target_complexity: str, target_priority: str
    ) -> list[tuple[dict, float]]:
        """Score tasks by similarity to target complexity/priority. Higher = more similar."""
        scored = []
        for task in tasks:
            score = 1.0
            if task.get("complexity") == target_complexity:
                score += 2.0
            if task.get("priority") == target_priority:
                score += 1.0
            scored.append((task, score))
        return scored

    def _weighted_average(
        self,
        scored_tasks: list[tuple[dict, float]],
        value_fn,
    ) -> float:
        """Compute weighted average with recency decay and similarity score."""
        total_weight = 0.0
        weighted_sum = 0.0

        for i, (task, similarity) in enumerate(scored_tasks):
            value = value_fn(task)
            if value <= 0:
                continue
            recency_weight = RECENCY_DECAY ** i
            weight = recency_weight * similarity
            weighted_sum += value * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0
        return weighted_sum / total_weight

    def _compute_confidence(
        self,
        sample_size: int,
        scored: list[tuple[dict, float]],
        target_complexity: str,
        target_priority: str,
    ) -> str:
        """Compute confidence level based on sample size and similarity."""
        exact_matches = sum(
            1 for t, _ in scored
            if t.get("complexity") == target_complexity and t.get("priority") == target_priority
        )

        if exact_matches >= 5:
            return "high"
        if exact_matches >= 2 or sample_size >= 5:
            return "medium"
        return "low"

    def _compute_predicted_vs_actual(self, done_tasks: list[dict]) -> list[dict]:
        """For each completed task, show predicted vs actual duration/cost."""
        if len(done_tasks) < MIN_SAMPLE_SIZE:
            return []

        results = []
        # Use leave-one-out: for each done task, estimate using the others
        for i, task in enumerate(done_tasks):
            others = done_tasks[:i] + done_tasks[i + 1:]
            if len(others) < MIN_SAMPLE_SIZE:
                continue

            complexity = task.get("complexity", "simple")
            priority = task.get("priority", "medium")
            scored = self._score_tasks(others, complexity, priority)
            scored.sort(key=lambda x: self._get_completion_timestamp(x[0]), reverse=True)

            pred_duration = self._weighted_average(scored, lambda t: self._calc_duration_seconds(t))
            pred_cost = self._weighted_average(scored, lambda t: self._parse_cost(t))
            actual_duration = self._calc_duration_seconds(task)
            actual_cost = self._parse_cost(task)

            if actual_duration > 0:
                results.append({
                    "task_id": task["id"],
                    "title": task.get("title", ""),
                    "predicted_duration_seconds": round(pred_duration, 0),
                    "actual_duration_seconds": round(actual_duration, 0),
                    "predicted_cost_usd": round(pred_cost, 4),
                    "actual_cost_usd": round(actual_cost, 4),
                    "duration_accuracy_pct": round(
                        (1 - abs(pred_duration - actual_duration) / actual_duration) * 100, 1
                    ) if actual_duration > 0 else 0.0,
                })

        return results

    def _calc_duration_seconds(self, task: dict) -> float:
        """Calculate task duration in seconds."""
        start_str = task.get("created_at", "")
        end_str = task.get("state_changed_at") or task.get("updated_at", "")
        if not start_str or not end_str:
            return 0.0
        try:
            start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            delta = (end - start).total_seconds()
            return max(delta, 0.0)
        except (ValueError, AttributeError):
            return 0.0

    def _get_completion_timestamp(self, task: dict) -> str:
        """Get completion timestamp for sorting."""
        return task.get("state_changed_at") or task.get("updated_at") or task.get("created_at") or ""

    def _parse_cost(self, task: dict) -> float:
        """Parse cost from execution_result."""
        result = task.get("execution_result")
        if not result:
            return 0.0

        result_data = result if isinstance(result, dict) else self._try_parse_json(result)
        if not result_data:
            return 0.0

        cost = result_data.get("total_cost_usd")
        if cost is not None:
            return self._to_float(cost)

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

    @staticmethod
    def _empty_estimate(reason: str) -> dict[str, Any]:
        return {
            "estimate_duration_seconds": 0,
            "estimate_cost_usd": 0.0,
            "sample_size": 0,
            "confidence": "none",
            "reason": reason,
        }
