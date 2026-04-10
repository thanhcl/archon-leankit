"""
Lifecycle-context preservation hooks for execution runs.

Captures structured summaries at key lifecycle boundaries:
- run_start: initial context snapshot (task, contract, model, profile)
- compaction: context window management events
- run_stop: final state summary (result, files, duration)
- run_timeout: partial work preservation for retry continuity

These summaries are stored in execution_run metadata and used by:
- Retry prompts: to give the next attempt a head start
- Observability: to track what happened during each lifecycle phase
- Analytics: to identify patterns in run behavior

Usage:
    hooks = LifecycleHooks()
    start_ctx = hooks.capture_run_start(task, model, profile, contract)
    stop_ctx = hooks.capture_run_stop(result, metrics)
    timeout_ctx = hooks.capture_run_timeout(result, elapsed)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)


class LifecycleHooks:
    """Captures structured lifecycle context at run boundaries."""

    @staticmethod
    def capture_run_start(
        task: dict[str, Any],
        model: str | None = None,
        token_profile: str | None = None,
        contract: dict[str, Any] | None = None,
        runner_key: str | None = None,
    ) -> dict[str, Any]:
        """Capture initial context when a run begins.

        Stored in execution_run.metadata.lifecycle.start
        """
        context: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": task.get("id"),
            "task_title": task.get("title"),
            "task_type": task.get("task_type"),
            "priority": task.get("priority"),
            "complexity": task.get("complexity"),
            "retry_count": task.get("retry_count", 0),
            "model": model,
            "token_profile": token_profile,
            "runner_key": runner_key,
        }

        # Contract snapshot
        if contract and isinstance(contract, dict):
            criteria = contract.get("acceptance_criteria") or []
            context["contract"] = {
                "version": contract.get("version"),
                "status": contract.get("negotiation_status"),
                "criteria_count": len(criteria) if isinstance(criteria, list) else 0,
            }

        # Editing boundaries
        allowed = task.get("allowed_paths") or []
        forbidden = task.get("forbidden_paths") or []
        if allowed or forbidden:
            context["boundaries"] = {
                "allowed_paths": allowed[:10],
                "forbidden_paths": forbidden[:10],
            }

        # Dependencies
        blocked_by = task.get("blocked_by") or []
        if blocked_by:
            context["dependencies"] = blocked_by

        return context

    @staticmethod
    def capture_run_stop(
        result: dict[str, Any] | None = None,
        runner_result: Any = None,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Capture final context when a run completes normally.

        Stored in execution_run.metadata.lifecycle.stop
        """
        context: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if result and isinstance(result, dict):
            context["result_status"] = result.get("result")
            context["files_changed"] = result.get("files_changed", 0)
            context["summary"] = (result.get("summary") or "")[:200]

        if runner_result is not None:
            context["success"] = getattr(runner_result, "success", None)
            context["exit_code"] = getattr(runner_result, "exit_code", None)
            context["duration_seconds"] = getattr(runner_result, "duration_seconds", None)
            context["timed_out"] = getattr(runner_result, "timed_out", False)

            # RTK token savings (A-2): capture from parsed result metadata
            parsed = getattr(runner_result, "parsed", None)
            if isinstance(parsed, dict):
                rtk_stats = parsed.get("rtk_session_stats")
                if rtk_stats and isinstance(rtk_stats, dict):
                    context["rtk_session_stats"] = rtk_stats
                rtk_tee = parsed.get("rtk_tee_path")
                if rtk_tee:
                    context["rtk_tee_path"] = rtk_tee

        if metrics and isinstance(metrics, dict):
            context["token_input"] = metrics.get("token_input")
            context["token_output"] = metrics.get("token_output")
            context["cost_usd"] = metrics.get("cost_usd")
            context["total_tool_calls"] = metrics.get("total_tool_calls")

        return context

    @staticmethod
    def capture_run_timeout(
        runner_result: Any = None,
        elapsed_seconds: float = 0,
        partial_files: list[str] | None = None,
        partial_output: str | None = None,
    ) -> dict[str, Any]:
        """Capture context when a run times out for retry continuity.

        Stored in execution_run.metadata.lifecycle.timeout
        Used by retry prompts to give the next attempt a head start.
        """
        context: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed_seconds,
            "reason": "timeout",
        }

        if partial_files:
            context["files_modified"] = partial_files[:20]
            context["files_modified_count"] = len(partial_files)

        if partial_output:
            context["last_output"] = partial_output[:500]

        if runner_result is not None:
            context["exit_code"] = getattr(runner_result, "exit_code", None)
            context["duration_seconds"] = getattr(runner_result, "duration_seconds", None)

        return context

    @staticmethod
    def capture_compaction_event(
        task_id: str,
        stage: str,
        token_count_before: int | None = None,
        token_count_after: int | None = None,
    ) -> dict[str, Any]:
        """Capture a context compaction event.

        Stored in execution_run.metadata.lifecycle.compactions[]
        """
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "stage": stage,
            "token_count_before": token_count_before,
            "token_count_after": token_count_after,
            "reduction_pct": (
                round((1 - token_count_after / token_count_before) * 100, 1)
                if token_count_before and token_count_after and token_count_before > 0
                else None
            ),
        }
