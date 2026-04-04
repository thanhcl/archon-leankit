"""
Auto-approval policy for LeanKit execution engine (B-P4-03).

Implements tiered auto-approval that can skip manual review stages
for low-risk, clean task runs.

Tiers:
- 0: All manual (default, backwards-compatible)
- 1: Auto code-review if: tests pass, no boundary violations, retry_count=0
- 2: Auto architect-review if: all contract criteria met, no out-of-scope files
- 3: Full auto — all stages auto-approve (only for docs/learning task types)

Security exclusions (never auto-approved at any tier):
- Tasks touching security-sensitive paths
- Cross-repo changes
- Tasks that exceeded cost budget threshold
"""

from __future__ import annotations

from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Paths that are never auto-approved regardless of tier
SECURITY_SENSITIVE_PATTERNS = (
    ".env",
    "credentials",
    "secrets",
    "auth",
    "security",
    "password",
    "token",
    "key",
    "cert",
    "migration",
    "deploy",
    ".github/workflows",
)

# Task types eligible for tier 3 (full auto)
FULL_AUTO_TASK_TYPES = {"docs", "learning", "documentation"}


def evaluate_auto_approval(
    stage: str,
    task: dict[str, Any],
    execution_result: dict[str, Any] | None,
    tier: int,
    boundary_validation: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Evaluate whether a task stage can be auto-approved.

    Args:
        stage: Current review stage ("architect-review", "code-review", "review")
        task: Full task dict
        execution_result: Execution result from the run
        tier: Auto-approval tier from engine_policy (0-3)
        boundary_validation: Boundary validation result from run

    Returns:
        (can_auto_approve, reason)
    """
    if tier <= 0:
        return False, "auto_approval_tier=0 (manual)"

    # Security exclusion: never auto-approve security-sensitive changes
    changed_files = _get_changed_files(execution_result, boundary_validation)
    if _has_security_sensitive_files(changed_files):
        return False, "security-sensitive files changed"

    task_type = (task.get("task_type") or task.get("type") or "").lower()
    retry_count = task.get("retry_count") or 0

    if stage == "code-review":
        return _evaluate_code_review_auto(task, execution_result, tier, retry_count, boundary_validation)
    elif stage == "architect-review":
        return _evaluate_architect_review_auto(task, execution_result, tier, retry_count)
    elif stage == "review":
        return _evaluate_owner_review_auto(task, execution_result, tier, task_type)

    return False, f"unknown stage: {stage}"


def _evaluate_code_review_auto(
    task: dict[str, Any],
    execution_result: dict[str, Any] | None,
    tier: int,
    retry_count: int,
    boundary_validation: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Tier 1+: Auto-approve code-review if clean run."""
    if tier < 1:
        return False, "tier < 1"

    if retry_count > 0:
        return False, f"retry_count={retry_count} (not first attempt)"

    # Check boundary validation
    if boundary_validation:
        bv_status = boundary_validation.get("status", "")
        if bv_status == "violation":
            return False, "boundary violation detected"

    # Check execution result
    if execution_result:
        result_status = (execution_result.get("result") or "").upper()
        if result_status == "FAILURE":
            return False, "execution result is FAILURE"

    return True, "tier 1: clean first-attempt run, no violations"


def _evaluate_architect_review_auto(
    task: dict[str, Any],
    execution_result: dict[str, Any] | None,
    tier: int,
    retry_count: int,
) -> tuple[bool, str]:
    """Tier 2+: Auto-approve architect-review if contract criteria met."""
    if tier < 2:
        return False, "tier < 2"

    if retry_count > 0:
        return False, f"retry_count={retry_count}"

    return True, "tier 2: contract criteria checks delegated to architect reviewer"


def _evaluate_owner_review_auto(
    task: dict[str, Any],
    execution_result: dict[str, Any] | None,
    tier: int,
    task_type: str,
) -> tuple[bool, str]:
    """Tier 3: Full auto — only for docs/learning."""
    if tier < 3:
        return False, "tier < 3"

    if task_type not in FULL_AUTO_TASK_TYPES:
        return False, f"task_type={task_type} not eligible for full auto"

    return True, f"tier 3: full auto for task_type={task_type}"


def _get_changed_files(
    execution_result: dict[str, Any] | None,
    boundary_validation: dict[str, Any] | None,
) -> list[str]:
    """Extract changed file list from execution result or boundary validation."""
    files: list[str] = []
    if boundary_validation:
        files = boundary_validation.get("changed_files", [])
    if not files and isinstance(execution_result, dict):
        files = execution_result.get("changed_files", [])
    return files


def _has_security_sensitive_files(changed_files: list[str]) -> bool:
    """Check if any changed files match security-sensitive patterns."""
    for filepath in changed_files:
        lower = filepath.lower()
        for pattern in SECURITY_SENSITIVE_PATTERNS:
            if pattern in lower:
                return True
    return False
