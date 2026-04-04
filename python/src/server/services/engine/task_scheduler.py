"""
Priority-weighted task scheduling for the LeanKit execution engine.

Replaces FIFO ordering with a scoring function that considers:
- Task priority (critical/high/medium/low)
- Blocking impact (how many downstream tasks this unblocks)
- Queue age (how long the task has waited)
- Retry penalty (tasks that failed before are deprioritized)

Usage:
    from .task_scheduler import score_and_sort_tasks

    sorted_tasks = score_and_sort_tasks(tasks, all_tasks, mode="priority_weighted")

Phase: C-P5-01
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Priority weights — higher numeric score = picked first
PRIORITY_WEIGHT: dict[str, int] = {
    "critical": 40,
    "high": 30,
    "medium": 20,
    "low": 10,
}

# Scoring coefficients
BLOCKING_IMPACT_MULTIPLIER = 5  # per downstream blocked task
AGE_MINUTES_MULTIPLIER = 0.1   # per minute in queue
RETRY_PENALTY_MULTIPLIER = 3   # per previous retry


def compute_task_score(
    task: dict[str, Any],
    blocking_counts: dict[str, int],
) -> float:
    """Compute a priority score for a single task.

    Higher score = should be picked first.

    Score = priority_weight
          + (blocking_count * BLOCKING_IMPACT_MULTIPLIER)
          + (age_minutes * AGE_MINUTES_MULTIPLIER)
          - (retry_count * RETRY_PENALTY_MULTIPLIER)
    """
    task_id = task.get("id", "")
    priority = task.get("priority", "medium")
    retry_count = task.get("retry_count") or 0

    # Base priority score
    score = float(PRIORITY_WEIGHT.get(priority, PRIORITY_WEIGHT["medium"]))

    # Blocking impact: how many other tasks are waiting on this one
    blocking_count = blocking_counts.get(task_id, 0)
    score += blocking_count * BLOCKING_IMPACT_MULTIPLIER

    # Age bonus: reward tasks that have waited longer
    created_at = task.get("created_at")
    if created_at:
        age_minutes = _compute_age_minutes(created_at)
        score += age_minutes * AGE_MINUTES_MULTIPLIER

    # Retry penalty: deprioritize tasks that have already failed
    score -= retry_count * RETRY_PENALTY_MULTIPLIER

    return round(score, 2)


def compute_blocking_counts(
    all_tasks: list[dict[str, Any]],
) -> dict[str, int]:
    """Count how many tasks each task is blocking.

    For each task T that has blocked_by = [A, B, C],
    increment the blocking count for A, B, and C.

    Only counts tasks that are not yet in terminal states,
    since terminal tasks no longer benefit from being unblocked.
    """
    terminal_statuses = {"done", "cancelled", "failed"}
    counts: dict[str, int] = {}

    for task in all_tasks:
        status = task.get("status", "")
        if status in terminal_statuses:
            continue

        blocked_by = task.get("blocked_by") or []
        for blocker_id in blocked_by:
            if blocker_id:
                counts[blocker_id] = counts.get(blocker_id, 0) + 1

    return counts


def score_and_sort_tasks(
    executable_tasks: list[dict[str, Any]],
    all_project_tasks: list[dict[str, Any]] | None = None,
    mode: str = "fifo",
) -> list[dict[str, Any]]:
    """Sort tasks by scheduling mode.

    Args:
        executable_tasks: Tasks ready to execute (already filtered for blocked_by).
        all_project_tasks: All tasks in the project (for blocking_count computation).
            If None, blocking_count is zero for all tasks.
        mode: "fifo" (default, backwards-compatible) or "priority_weighted".

    Returns:
        Sorted list of tasks, highest priority first.
    """
    if mode == "fifo" or not executable_tasks:
        return _sort_fifo(executable_tasks)

    if mode == "priority_weighted":
        return _sort_priority_weighted(executable_tasks, all_project_tasks)

    logger.warning(f"Unknown scheduling mode '{mode}', falling back to fifo")
    return _sort_fifo(executable_tasks)


def _sort_fifo(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Original FIFO sorting: by priority bucket then created_at."""
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(
        tasks,
        key=lambda t: (
            priority_order.get(t.get("priority", "medium"), 2),
            t.get("created_at", ""),
        ),
    )


def _sort_priority_weighted(
    tasks: list[dict[str, Any]],
    all_project_tasks: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Priority-weighted sorting: by computed score descending."""
    blocking_counts = compute_blocking_counts(all_project_tasks or [])

    scored: list[tuple[float, dict[str, Any]]] = []
    for task in tasks:
        score = compute_task_score(task, blocking_counts)
        task["_scheduling_score"] = score  # attach for observability
        scored.append((score, task))

    # Sort by score descending (highest first), then by created_at ascending (older first) as tiebreaker
    scored.sort(key=lambda pair: (-pair[0], pair[1].get("created_at", "")))

    if scored:
        top = scored[0]
        logger.info(
            f"Priority-weighted scheduling | top_task={top[1].get('id')} | "
            f"score={top[0]} | total_candidates={len(scored)}"
        )

    return [pair[1] for pair in scored]


def _compute_age_minutes(created_at: str) -> float:
    """Parse ISO timestamp and return age in minutes from now."""
    try:
        if created_at.endswith("Z"):
            created_at = created_at[:-1] + "+00:00"
        dt = datetime.fromisoformat(created_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        return max(0.0, delta.total_seconds() / 60.0)
    except (ValueError, TypeError):
        return 0.0
