"""
Task Decomposition for coordinator mode (C-P7-02).

Detects complex tasks that should be decomposed into parallel children
and produces decomposition plans. Called during architect-review stage
when task complexity exceeds the decomposition threshold.

Usage:
    decomposer = TaskDecomposer()
    should, reason = decomposer.should_decompose(task, review_result)
    if should:
        specs = decomposer.build_decomposition_specs(task, review_result)
"""

from __future__ import annotations

from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Default complexity threshold — tasks with estimated effort above this
# are candidates for decomposition
DEFAULT_DECOMPOSITION_THRESHOLD_HOURS = 4

# Maximum children per coordinator task
MAX_CHILDREN = 10


class TaskDecomposer:
    """Analyzes tasks for decomposition into parallel children."""

    def __init__(
        self,
        threshold_hours: float = DEFAULT_DECOMPOSITION_THRESHOLD_HOURS,
        max_children: int = MAX_CHILDREN,
    ):
        self.threshold_hours = threshold_hours
        self.max_children = max_children

    def should_decompose(
        self,
        task: dict[str, Any],
        review_result: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Determine whether a task should be decomposed.

        Checks:
        1. Task is not already a child (no recursive decomposition)
        2. decomposition_mode is not 'none' with explicit opt-out
        3. Complexity indicator suggests decomposition
        4. Architect review suggests splitting

        Returns:
            (should_decompose, reason)
        """
        # No recursive decomposition
        if task.get("parent_task_id"):
            return False, "child tasks cannot be decomposed"

        # Already decomposed
        if task.get("decomposition_mode") == "coordinator":
            return False, "already a coordinator"

        # Check review result for decomposition suggestion
        if review_result and isinstance(review_result, dict):
            decomposition = review_result.get("decomposition")
            if isinstance(decomposition, dict):
                children = decomposition.get("children") or []
                if len(children) >= 2:
                    return True, f"architect review suggests {len(children)} children"

            # Check if review explicitly recommends splitting
            feedback = review_result.get("feedback") or ""
            if "split" in feedback.lower() or "decompose" in feedback.lower():
                return True, "architect review recommends splitting"

        # Check complexity
        complexity = task.get("complexity", "simple")
        if complexity == "complex":
            # Check acceptance criteria count as proxy for scope
            criteria = task.get("acceptance_criteria") or []
            if len(criteria) >= 5:
                return True, f"complex task with {len(criteria)} acceptance criteria"

        return False, "no decomposition needed"

    def build_decomposition_specs(
        self,
        task: dict[str, Any],
        review_result: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build child task specifications from decomposition analysis.

        If the architect review includes a structured decomposition plan,
        use that. Otherwise, generate a basic split from acceptance criteria.

        Returns:
            List of child specs suitable for CoordinatorService.create_coordinator_children()
        """
        # 1. Use architect-provided decomposition if available
        if review_result and isinstance(review_result, dict):
            decomposition = review_result.get("decomposition")
            if isinstance(decomposition, dict):
                children = decomposition.get("children") or []
                if children:
                    return self._normalize_architect_specs(children, task)

        # 2. Fall back to splitting by acceptance criteria groups
        return self._split_by_criteria(task)

    def _normalize_architect_specs(
        self,
        children: list[dict[str, Any]],
        parent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Normalize architect-provided child specs."""
        specs: list[dict[str, Any]] = []
        prev_id: str | None = None

        for i, child in enumerate(children[:self.max_children]):
            spec: dict[str, Any] = {
                "title": child.get("title") or f"Sub-task {i + 1}",
                "description": child.get("description") or "",
                "priority": child.get("priority") or parent.get("priority", "medium"),
                "allowed_paths": child.get("allowed_paths") or parent.get("allowed_paths", []),
                "acceptance_criteria": child.get("acceptance_criteria") or [],
                "complexity": child.get("complexity", "simple"),
            }

            # Sequential dependencies from architect
            deps = child.get("blocked_by") or []
            if child.get("depends_on_previous") and prev_id:
                deps.append(prev_id)
            spec["blocked_by"] = deps

            specs.append(spec)
            # Use a placeholder — actual ID set after creation
            prev_id = f"__child_{i}__"

        return specs

    def _split_by_criteria(
        self,
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Basic decomposition: group acceptance criteria into child tasks."""
        criteria = task.get("acceptance_criteria") or []
        if len(criteria) < 2:
            return []

        # Group criteria into chunks of ~2-3 per child
        chunk_size = max(2, len(criteria) // 3)
        specs: list[dict[str, Any]] = []

        for i in range(0, len(criteria), chunk_size):
            chunk = criteria[i:i + chunk_size]
            if not chunk:
                continue

            chunk_descriptions = []
            for c in chunk:
                if isinstance(c, str):
                    chunk_descriptions.append(c)
                elif isinstance(c, dict):
                    chunk_descriptions.append(c.get("description", str(c)))

            specs.append({
                "title": f"{task.get('title', 'Task')} — Part {len(specs) + 1}",
                "description": f"Implement: {'; '.join(chunk_descriptions[:3])}",
                "priority": task.get("priority", "medium"),
                "acceptance_criteria": chunk,
                "complexity": "simple",
            })

        return specs[:self.max_children]
