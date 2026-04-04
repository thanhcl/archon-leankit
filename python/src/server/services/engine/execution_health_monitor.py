"""
Execution Health Monitor for LeanKit V3 Task Engine.

Detects mid-run anomalies — tool-call loops and excessive total tool usage —
during active Claude Code sessions. Designed to be wired into the stream event
callback of the task runner and checked after each tool call.

Usage:
    monitor = ExecutionHealthMonitor()
    monitor.on_stream_event(task_id, stream_event)
    status = monitor.check_health(task_id)  # "healthy" | "loop_detected" | "budget_exceeded"
    if status != "healthy":
        reason = monitor.get_abort_reason(task_id)
"""

import os
from dataclasses import dataclass, field
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Health status constants
HEALTH_HEALTHY = "healthy"
HEALTH_LOOP_DETECTED = "loop_detected"
HEALTH_BUDGET_EXCEEDED = "budget_exceeded"

# Environment variable names
_ENV_SAME_TOOL_LIMIT = "LEANKIT_EXECUTION_HEALTH_SAME_TOOL_LIMIT"
_ENV_TOTAL_LIMIT = "LEANKIT_EXECUTION_HEALTH_TOTAL_LIMIT"

# Defaults
_DEFAULT_SAME_TOOL_LIMIT = 5
_DEFAULT_TOTAL_LIMIT = 30


def _read_int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        logger.warning(f"Invalid value for {name}, using default {default}")
        return default


@dataclass
class _TaskMetrics:
    """Per-task tool-call counters accumulated during a single execution run."""

    tool_counts: dict[str, int] = field(default_factory=dict)
    total_count: int = 0
    abort_reason: str | None = None

    def record_tool(self, tool_name: str) -> None:
        self.total_count += 1
        self.tool_counts[tool_name] = self.tool_counts.get(tool_name, 0) + 1


class ExecutionHealthMonitor:
    """Tracks tool-call counts per task and detects loop or budget violations.

    Thresholds are read from environment variables at construction time:
    - LEANKIT_EXECUTION_HEALTH_SAME_TOOL_LIMIT (default 5):
        Maximum times the same tool may be called in a single run before
        loop_detected is returned.
    - LEANKIT_EXECUTION_HEALTH_TOTAL_LIMIT (default 30):
        Maximum total tool calls allowed in a single run before
        budget_exceeded is returned.

    One monitor instance may be shared across concurrent tasks because state
    is keyed by task_id. Call reset(task_id) between retries.
    """

    def __init__(
        self,
        same_tool_limit: int | None = None,
        total_limit: int | None = None,
    ) -> None:
        self._same_tool_limit = (
            same_tool_limit
            if same_tool_limit is not None
            else _read_int_env(_ENV_SAME_TOOL_LIMIT, _DEFAULT_SAME_TOOL_LIMIT)
        )
        self._total_limit = (
            total_limit
            if total_limit is not None
            else _read_int_env(_ENV_TOTAL_LIMIT, _DEFAULT_TOTAL_LIMIT)
        )
        self._metrics: dict[str, _TaskMetrics] = {}
        self._task_total_limits: dict[str, int] = {}  # per-task overrides (C-P6-02)

    # ── Public properties ─────────────────────────────────────────────

    @property
    def same_tool_limit(self) -> int:
        return self._same_tool_limit

    @property
    def total_limit(self) -> int:
        return self._total_limit

    # ── Core API ──────────────────────────────────────────────────────

    def on_stream_event(self, task_id: str, event: dict[str, Any]) -> None:
        """Record a stream event for a task.

        Only tool_use events are tracked; all other event types are ignored.
        """
        if event.get("event") != "tool_use":
            return
        tool_name = event.get("tool_name", "")
        if not tool_name:
            return
        metrics = self._get_or_create(task_id)
        metrics.record_tool(tool_name)
        logger.debug(
            f"Tool call recorded | task_id={task_id} | tool={tool_name} | "
            f"same_tool_count={metrics.tool_counts[tool_name]} | total={metrics.total_count}"
        )

    def check_health(self, task_id: str) -> str:
        """Evaluate health status for the given task.

        Returns:
            HEALTH_HEALTHY ("healthy")         — within all limits
            HEALTH_LOOP_DETECTED ("loop_detected")   — a single tool repeated beyond same_tool_limit
            HEALTH_BUDGET_EXCEEDED ("budget_exceeded") — total tool calls exceeded total_limit
        """
        metrics = self._get_or_create(task_id)

        # Same-tool loop check (checked first — more actionable signal)
        for tool_name, count in metrics.tool_counts.items():
            if count > self._same_tool_limit:
                reason = (
                    f"Execution loop detected: tool '{tool_name}' called {count} times "
                    f"(limit {self._same_tool_limit})"
                )
                metrics.abort_reason = reason
                return HEALTH_LOOP_DETECTED

        # Total tool-call budget check (uses per-task override if set, C-P6-02)
        effective_limit = self._task_total_limits.get(task_id, self._total_limit)
        if metrics.total_count > effective_limit:
            reason = (
                f"Execution budget exceeded: {metrics.total_count} total tool calls "
                f"(limit {effective_limit})"
            )
            metrics.abort_reason = reason
            return HEALTH_BUDGET_EXCEEDED

        return HEALTH_HEALTHY

    def get_abort_reason(self, task_id: str) -> str:
        """Return the abort reason set by the most recent unhealthy check_health call."""
        metrics = self._metrics.get(task_id)
        if metrics and metrics.abort_reason:
            return metrics.abort_reason
        return "Execution health limit exceeded"

    def get_metrics_snapshot(self, task_id: str) -> dict[str, Any]:
        """Return a read-only snapshot of the current metrics for a task."""
        metrics = self._metrics.get(task_id)
        if not metrics:
            return {"tool_counts": {}, "total_count": 0}
        return {
            "tool_counts": dict(metrics.tool_counts),
            "total_count": metrics.total_count,
        }

    def reset(self, task_id: str) -> None:
        """Clear accumulated metrics for a task (e.g., between retries)."""
        self._metrics.pop(task_id, None)
        self._task_total_limits.pop(task_id, None)

    def set_task_total_limit(self, task_id: str, limit: int) -> None:
        """Override the total tool-call limit for a specific task.

        Used by the engine to apply per-profile budgets from engine_policy
        (C-P6-02). When set, this overrides the global total_limit for this task.
        """
        self._task_total_limits[task_id] = limit
        logger.info(
            f"Tool budget override set | task_id={task_id} | limit={limit}"
        )

    def get_task_total_limit(self, task_id: str) -> int:
        """Return the effective total tool-call limit for a task."""
        return self._task_total_limits.get(task_id, self._total_limit)

    # ── Internal ──────────────────────────────────────────────────────

    def _get_or_create(self, task_id: str) -> _TaskMetrics:
        if task_id not in self._metrics:
            self._metrics[task_id] = _TaskMetrics()
        return self._metrics[task_id]
