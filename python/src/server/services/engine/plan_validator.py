"""
Plan Validator for LeanKit V3 Task Engine.

Validates project plans before execution begins. Catches plan defects
(missing requirements, dependency cycles, scope issues) before burning
execution budget.

Performs 5-dimension validation:
  1. REQUIREMENT_COVERAGE — Every acceptance criterion maps to a task
  2. DEPENDENCY_CORRECTNESS — No cycles in blocked_by, all refs exist
  3. SCOPE_SANITY — Tasks are reasonable size, bounded count
  4. EDIT_BOUNDARY_FEASIBILITY — No conflicting allowed_paths
  5. CONTRACT_COMPLIANCE — Every task has acceptance_criteria

Adopted from GSD's plan-check mechanism with 11-dimension validation
(adapted to 5 dimensions relevant to LeanKit's contract model).

Usage:
    validator = PlanValidator(supabase_client=client)
    result = await validator.validate(project_id="proj-123")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...config.logfire_config import get_logger
from ...utils import get_supabase_client

logger = get_logger(__name__)

TASKS_TABLE = "archon_tasks"

# Scope limits
MAX_TASKS_WARNING = 20
MAX_TASK_DESCRIPTION_LENGTH = 50  # Minimum description length


@dataclass
class ValidationDimension:
    """Result of a single validation dimension."""

    name: str
    passed: bool
    issues: list[str] = field(default_factory=list)
    severity: str = "info"  # "blocker" | "warning" | "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "issues": self.issues,
            "severity": self.severity,
        }


@dataclass
class PlanValidationResult:
    """Aggregate result of plan validation."""

    passed: bool
    dimensions: list[ValidationDimension] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    auto_fixed: list[str] = field(default_factory=list)
    iteration: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blocker_count": len(self.blockers),
            "warning_count": len(self.warnings),
            "auto_fixed_count": len(self.auto_fixed),
            "iteration": self.iteration,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "blockers": self.blockers,
            "warnings": self.warnings,
            "auto_fixed": self.auto_fixed,
        }


class PlanValidator:
    """Validates project plans before execution begins."""

    def __init__(self, supabase_client=None):
        self._client = supabase_client or get_supabase_client()

    async def validate(
        self,
        project_id: str,
        task_statuses: tuple[str, ...] = ("approved", "planning", "assigned"),
    ) -> PlanValidationResult:
        """Validate all pending tasks in a project.

        Args:
            project_id: Project to validate.
            task_statuses: Which task statuses to include in validation.

        Returns:
            PlanValidationResult with per-dimension status.
        """
        tasks = await self._fetch_project_tasks(project_id, task_statuses)

        if not tasks:
            return PlanValidationResult(
                passed=True,
                info=["No tasks found to validate"],
            )

        dimensions: list[ValidationDimension] = []
        all_blockers: list[str] = []
        all_warnings: list[str] = []
        all_info: list[str] = []

        # Dimension 1: Requirement Coverage
        d1 = self._check_requirement_coverage(tasks)
        dimensions.append(d1)
        self._collect_issues(d1, all_blockers, all_warnings, all_info)

        # Dimension 2: Dependency Correctness
        d2 = self._check_dependency_correctness(tasks)
        dimensions.append(d2)
        self._collect_issues(d2, all_blockers, all_warnings, all_info)

        # Dimension 3: Scope Sanity
        d3 = self._check_scope_sanity(tasks)
        dimensions.append(d3)
        self._collect_issues(d3, all_blockers, all_warnings, all_info)

        # Dimension 4: Edit Boundary Feasibility
        d4 = self._check_edit_boundary_feasibility(tasks)
        dimensions.append(d4)
        self._collect_issues(d4, all_blockers, all_warnings, all_info)

        # Dimension 5: Contract Compliance
        d5 = self._check_contract_compliance(tasks)
        dimensions.append(d5)
        self._collect_issues(d5, all_blockers, all_warnings, all_info)

        passed = len(all_blockers) == 0

        logger.info(
            f"Plan validation | project_id={project_id} | "
            f"tasks={len(tasks)} | passed={passed} | "
            f"blockers={len(all_blockers)} | warnings={len(all_warnings)}"
        )

        return PlanValidationResult(
            passed=passed,
            dimensions=dimensions,
            blockers=all_blockers,
            warnings=all_warnings,
            info=all_info,
        )

    async def validate_with_retry(
        self,
        project_id: str,
        max_iterations: int = 3,
    ) -> PlanValidationResult:
        """Validate with auto-fix iterations.

        Runs validation, attempts auto-fixes for simple issues,
        then re-validates. Max iterations before escalation.

        Args:
            project_id: Project to validate.
            max_iterations: Max validation-fix cycles. Default: 3.

        Returns:
            Final PlanValidationResult after iterations.
        """
        result: PlanValidationResult | None = None

        for iteration in range(1, max_iterations + 1):
            result = await self.validate(project_id)
            result.iteration = iteration

            if result.passed:
                logger.info(
                    f"Plan validation passed | project_id={project_id} | "
                    f"iteration={iteration}"
                )
                return result

            # Attempt auto-fixes on simple issues
            if iteration < max_iterations:
                fixed = await self._attempt_auto_fixes(project_id, result)
                result.auto_fixed = fixed
                if not fixed:
                    # No fixable issues — further iterations won't help
                    logger.info(
                        f"Plan validation: no auto-fixable issues remaining | "
                        f"project_id={project_id} | blockers={len(result.blockers)}"
                    )
                    return result

                logger.info(
                    f"Plan validation: auto-fixed {len(fixed)} issues, re-validating | "
                    f"project_id={project_id} | iteration={iteration}"
                )

        # result is guaranteed non-None because max_iterations >= 1
        assert result is not None
        return result

    # ── Validation Dimensions ─────────────────────────────────────────

    def _check_requirement_coverage(
        self, tasks: list[dict[str, Any]],
    ) -> ValidationDimension:
        """Dim 1: Every task should have acceptance criteria."""
        issues: list[str] = []
        tasks_without_criteria = []

        for task in tasks:
            criteria = task.get("acceptance_criteria") or []
            if not criteria:
                tasks_without_criteria.append(task.get("title", task.get("id", "?")))

        if tasks_without_criteria:
            count = len(tasks_without_criteria)
            preview = ", ".join(tasks_without_criteria[:3])
            if count > 3:
                preview += f" (+{count - 3} more)"
            issues.append(
                f"{count} task(s) have no acceptance criteria: {preview}"
            )

        severity = "warning" if issues else "info"
        return ValidationDimension(
            name="requirement_coverage",
            passed=len(issues) == 0,
            issues=issues,
            severity=severity,
        )

    def _check_dependency_correctness(
        self, tasks: list[dict[str, Any]],
    ) -> ValidationDimension:
        """Dim 2: No cycles in blocked_by, all referenced IDs exist."""
        issues: list[str] = []
        task_ids = {t["id"] for t in tasks if t.get("id")}

        # Check for invalid references
        for task in tasks:
            blocked_by = task.get("blocked_by") or []
            if isinstance(blocked_by, str):
                blocked_by = [blocked_by]
            for dep_id in blocked_by:
                if dep_id not in task_ids:
                    issues.append(
                        f"Task '{task.get('title', task['id'])}' blocked by "
                        f"non-existent task '{dep_id}'"
                    )

        # Check for cycles using DFS
        adj: dict[str, list[str]] = {}
        for task in tasks:
            tid = task.get("id", "")
            blocked_by = task.get("blocked_by") or []
            if isinstance(blocked_by, str):
                blocked_by = [blocked_by]
            adj[tid] = [dep for dep in blocked_by if dep in task_ids]

        cycle = self._detect_cycle(adj)
        if cycle:
            cycle_str = " -> ".join(cycle[:5])
            issues.append(f"Dependency cycle detected: {cycle_str}")

        severity = "blocker" if issues else "info"
        return ValidationDimension(
            name="dependency_correctness",
            passed=len(issues) == 0,
            issues=issues,
            severity=severity,
        )

    def _check_scope_sanity(
        self, tasks: list[dict[str, Any]],
    ) -> ValidationDimension:
        """Dim 3: Tasks are reasonable size, bounded count."""
        issues: list[str] = []
        total = len(tasks)

        if total > MAX_TASKS_WARNING:
            issues.append(
                f"Large plan: {total} tasks (recommend splitting into phases)"
            )

        # Check for tasks with very short descriptions
        for task in tasks:
            desc = (task.get("description") or "").strip()
            title = (task.get("title") or "").strip()
            if len(desc) < MAX_TASK_DESCRIPTION_LENGTH and len(title) < 10:
                issues.append(
                    f"Task '{title or task.get('id', '?')}' has insufficient "
                    f"description ({len(desc)} chars)"
                )

        severity = "warning" if issues else "info"
        return ValidationDimension(
            name="scope_sanity",
            passed=True,  # Scope issues are warnings, not blockers
            issues=issues,
            severity=severity,
        )

    def _check_edit_boundary_feasibility(
        self, tasks: list[dict[str, Any]],
    ) -> ValidationDimension:
        """Dim 4: No two concurrent tasks have conflicting allowed_paths."""
        issues: list[str] = []

        # Group tasks by wave/dependency level (tasks with same or no blocked_by are concurrent)
        concurrent_groups = self._group_concurrent_tasks(tasks)

        for group in concurrent_groups:
            if len(group) < 2:
                continue

            for i, task_a in enumerate(group):
                paths_a = set(task_a.get("allowed_paths") or [])
                if not paths_a:
                    continue

                for task_b in group[i + 1:]:
                    paths_b = set(task_b.get("allowed_paths") or [])
                    if not paths_b:
                        continue

                    overlap = paths_a & paths_b
                    if overlap:
                        title_a = task_a.get("title", task_a.get("id", "?"))
                        title_b = task_b.get("title", task_b.get("id", "?"))
                        overlap_preview = ", ".join(list(overlap)[:3])
                        issues.append(
                            f"Concurrent tasks '{title_a}' and '{title_b}' "
                            f"have overlapping paths: {overlap_preview}"
                        )

        severity = "warning" if issues else "info"
        return ValidationDimension(
            name="edit_boundary_feasibility",
            passed=True,  # Overlaps are warnings (shared isolation can handle)
            issues=issues,
            severity=severity,
        )

    def _check_contract_compliance(
        self, tasks: list[dict[str, Any]],
    ) -> ValidationDimension:
        """Dim 5: Tasks targeting execution have required fields."""
        issues: list[str] = []

        for task in tasks:
            status = task.get("status", "")
            if status not in ("approved", "assigned"):
                continue

            # Tasks about to execute should have a description
            if not (task.get("description") or "").strip():
                issues.append(
                    f"Task '{task.get('title', task.get('id', '?'))}' has no description"
                )

            # Tasks should have a title
            if not (task.get("title") or "").strip():
                issues.append(
                    f"Task '{task.get('id', '?')}' has no title"
                )

        severity = "blocker" if issues else "info"
        return ValidationDimension(
            name="contract_compliance",
            passed=len(issues) == 0,
            issues=issues,
            severity=severity,
        )

    # ── Auto-fix ──────────────────────────────────────────────────────

    async def _attempt_auto_fixes(
        self,
        project_id: str,
        result: PlanValidationResult,
    ) -> list[str]:
        """Attempt to auto-fix simple validation issues.

        Currently handles:
        - Tasks without acceptance criteria (adds default template)

        Returns list of fix descriptions.
        """
        fixed: list[str] = []

        for dimension in result.dimensions:
            if dimension.name == "requirement_coverage" and not dimension.passed:
                # Auto-fix: add default acceptance criteria to tasks missing them
                tasks = await self._fetch_project_tasks(project_id, ("approved", "planning"))
                for task in tasks:
                    criteria = task.get("acceptance_criteria") or []
                    if not criteria and task.get("description"):
                        default_criteria = [
                            {"text": "Implementation matches task description", "category": "functional"},
                            {"text": "All existing tests continue to pass", "category": "regression"},
                        ]
                        try:
                            self._client.table(TASKS_TABLE).update({
                                "acceptance_criteria": default_criteria,
                            }).eq("id", task["id"]).execute()
                            fixed.append(
                                f"Added default acceptance criteria to '{task.get('title', task['id'])}'"
                            )
                        except Exception as e:
                            logger.warning(f"Auto-fix failed for task {task.get('id')}: {e}")

        return fixed

    # ── Helpers ────────────────────────────────────────────────────────

    async def _fetch_project_tasks(
        self,
        project_id: str,
        statuses: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        """Fetch tasks for a project filtered by status."""
        try:
            resp = (
                self._client.table(TASKS_TABLE)
                .select("id, title, description, status, acceptance_criteria, "
                        "blocked_by, allowed_paths, forbidden_paths, priority, "
                        "complexity, current_contract_id")
                .eq("project_id", project_id)
                .in_("status", list(statuses))
                .order("created_at")
                .execute()
            )
            return resp.data or []
        except Exception as e:
            logger.error(f"Failed to fetch project tasks: {e}")
            return []

    @staticmethod
    def _detect_cycle(adj: dict[str, list[str]]) -> list[str] | None:
        """Detect cycle in dependency graph using DFS. Returns cycle path or None."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {node: WHITE for node in adj}
        parent: dict[str, str | None] = {node: None for node in adj}

        def dfs(node: str) -> list[str] | None:
            color[node] = GRAY
            for neighbor in adj.get(node, []):
                if neighbor not in color:
                    continue
                if color[neighbor] == GRAY:
                    # Found cycle — reconstruct path
                    cycle = [neighbor, node]
                    current = node
                    while parent.get(current) and parent[current] != neighbor:
                        current = parent[current]
                        cycle.append(current)
                    cycle.append(neighbor)
                    return list(reversed(cycle))
                if color[neighbor] == WHITE:
                    parent[neighbor] = node
                    result = dfs(neighbor)
                    if result:
                        return result
            color[node] = BLACK
            return None

        for node in adj:
            if color[node] == WHITE:
                result = dfs(node)
                if result:
                    return result
        return None

    @staticmethod
    def _group_concurrent_tasks(
        tasks: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        """Group tasks into concurrency groups (tasks that can run in parallel).

        Tasks with no blocked_by or with identical blocked_by sets are concurrent.
        """
        groups: dict[str, list[dict[str, Any]]] = {}

        for task in tasks:
            blocked_by = task.get("blocked_by") or []
            if isinstance(blocked_by, str):
                blocked_by = [blocked_by]
            key = ",".join(sorted(blocked_by)) if blocked_by else "__root__"
            groups.setdefault(key, []).append(task)

        return list(groups.values())

    @staticmethod
    def _collect_issues(
        dimension: ValidationDimension,
        blockers: list[str],
        warnings: list[str],
        info: list[str],
    ) -> None:
        """Dispatch dimension issues to the appropriate severity bucket."""
        for issue in dimension.issues:
            if dimension.severity == "blocker":
                blockers.append(f"[{dimension.name}] {issue}")
            elif dimension.severity == "warning":
                warnings.append(f"[{dimension.name}] {issue}")
            else:
                info.append(f"[{dimension.name}] {issue}")
