"""Implementation Cockpit Service.

Aggregates data from plans, tasks, execution runs, and engine metrics
into a single unified response suitable for rendering the Virtual Office
cockpit dashboard in one request.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

_PLANS_TABLE = "project_implementation_plans"
_PHASES_TABLE = "project_implementation_phases"
_ITEMS_TABLE = "project_implementation_items"
_LINKS_TABLE = "project_implementation_item_task_links"
_TASKS_TABLE = "archon_tasks"
_RUNS_TABLE = "archon_execution_runs"
_REVIEW_FEEDBACK_TABLE = "archon_review_feedback"

# Item statuses excluded from progress calculation.
_EXCLUDED_FROM_PROGRESS = {"deferred", "cancelled"}

_ITEM_PROGRESS_WEIGHTS: dict[str, float] = {
    "planned": 0.0,
    "ready": 0.05,
    "in_progress": 0.50,
    "blocked": 0.50,
    "review": 0.90,
    "done": 1.00,
}

# Tasks stuck in executing longer than this are considered stale.
_STALE_EXECUTING_HOURS = 2

# Project-level progress signal thresholds.
_ACTIVE_PROJECT_HOURS = 24   # Any state change within this window → "active"
_STALE_PROJECT_HOURS = 48    # No state change beyond this window → "stale"

# Task statuses that are terminal (no longer in-progress).
_TERMINAL_STATUSES = {"done", "cancelled", "deferred"}

# Issue ledger thresholds.
_REPEAT_FAILURE_THRESHOLD = 5   # Aggregate retries per feature/module
_HIGH_RETRY_THRESHOLD = 3       # Individual task retry count
_REVIEW_CYCLE_THRESHOLD = 2     # Code-review cycles indicating persistent rejection

# Task statuses considered "in-flight" for stale lifecycle detection.
_IN_FLIGHT_STATUSES = {"executing", "architect-review", "code-review", "qa-eval"}

# Severity sort order — lower number = higher severity (critical first).
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


class CockpitService:
    """Builds the unified cockpit payload for a project."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    def get_cockpit(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """Return the full cockpit response for a project.

        Returns (success, payload) where payload contains all cockpit sections.
        """
        try:
            # ── Load project ───────────────────────────────────────────
            project = self._load_project(project_id)
            if project is None:
                return False, {"error": f"Project {project_id} not found"}

            # ── Load plan (first active or latest) ─────────────────────
            plan = self._load_plan(project_id)

            # ── Load tasks ─────────────────────────────────────────────
            tasks = self._load_tasks(project_id)

            # ── Load plan structure if plan exists ─────────────────────
            phases: list[dict[str, Any]] = []
            items: list[dict[str, Any]] = []
            item_links: list[dict[str, Any]] = []
            if plan:
                phases = self._load_phases(plan["id"])
                items = self._load_items(plan["id"])
                item_links = self._load_item_links([i["id"] for i in items])

            # ── Load execution runs and review feedback ────────────────
            task_ids = [t["id"] for t in tasks]
            runs = self._load_runs(task_ids)
            review_feedback = self._load_review_feedback(task_ids)

            # ── Build sections ─────────────────────────────────────────
            header = self._build_header(project, plan, items, tasks)
            workstreams = self._build_workstreams(tasks)
            phases_section = self._build_phases(phases, items)
            quality = self._build_quality(tasks, runs, review_feedback)
            issue_ledger = self._build_issue_ledger(tasks, items, item_links, review_feedback)
            throughput = self._build_throughput(tasks, runs)
            recent_alerts = self._build_recent_alerts(project_id)

            return True, {
                "project_id": project_id,
                "header": header,
                "workstreams": workstreams,
                "phases": phases_section,
                "quality": quality,
                "issue_ledger": issue_ledger,
                "throughput": throughput,
                "recent_alerts": recent_alerts,
            }

        except Exception as exc:
            logger.error(f"Failed to build cockpit | project_id={project_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    # ── Data loaders ───────────────────────────────────────────────────

    def _load_project(self, project_id: str) -> dict[str, Any] | None:
        result = (
            self.supabase_client.table("archon_projects")
            .select("id, title, description, created_at, updated_at")
            .eq("id", project_id)
            .maybe_single()
            .execute()
        )
        return result.data if result else None

    def _load_plan(self, project_id: str) -> dict[str, Any] | None:
        result = (
            self.supabase_client.table(_PLANS_TABLE)
            .select("id, title, status, description, created_at")
            .eq("project_id", project_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        data = result.data or []
        return data[0] if data else None

    def _load_phases(self, plan_id: str) -> list[dict[str, Any]]:
        result = (
            self.supabase_client.table(_PHASES_TABLE)
            .select("id, plan_id, title, phase_order")
            .eq("plan_id", plan_id)
            .order("phase_order")
            .execute()
        )
        return result.data or []

    def _load_items(self, plan_id: str) -> list[dict[str, Any]]:
        result = (
            self.supabase_client.table(_ITEMS_TABLE)
            .select("id, plan_id, phase_id, title, status, item_key, item_order")
            .eq("plan_id", plan_id)
            .execute()
        )
        return result.data or []

    def _load_tasks(self, project_id: str) -> list[dict[str, Any]]:
        result = (
            self.supabase_client.table(_TASKS_TABLE)
            .select(
                "id, title, status, retry_count, review_cycle, phase, feature, module, "
                "blocked_by, state_changed_at, created_at, updated_at, "
                "complexity, priority, model_exhaustion_count"
            )
            .eq("project_id", project_id)
            .or_("archived.is.null,archived.is.false")
            .execute()
        )
        return result.data or []

    def _load_item_links(self, item_ids: list[str]) -> list[dict[str, Any]]:
        """Load item-task links for the given item IDs."""
        if not item_ids:
            return []
        result = (
            self.supabase_client.table(_LINKS_TABLE)
            .select("item_id, task_id")
            .in_("item_id", item_ids)
            .execute()
        )
        return result.data or []

    def _load_review_feedback(self, task_ids: list[str]) -> list[dict[str, Any]]:
        if not task_ids:
            return []
        result = (
            self.supabase_client.table(_REVIEW_FEEDBACK_TABLE)
            .select("id, task_id, reviewer_identity, verdict, created_at")
            .in_("task_id", task_ids)
            .execute()
        )
        return result.data or []

    def _load_runs(self, task_ids: list[str]) -> list[dict[str, Any]]:
        if not task_ids:
            return []
        result = (
            self.supabase_client.table(_RUNS_TABLE)
            .select("id, task_id, status, stage, cost_usd, duration_seconds, started_at, finished_at")
            .in_("task_id", task_ids)
            .execute()
        )
        return result.data or []

    # ── Section builders ───────────────────────────────────────────────

    def _build_header(
        self,
        project: dict[str, Any],
        plan: dict[str, Any] | None,
        items: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        plan_title = plan["title"] if plan else project.get("title", "Untitled")

        # Completion from plan items (preferred) or tasks
        if items:
            active_items = [i for i in items if i.get("status") not in _EXCLUDED_FROM_PROGRESS]
            done_items = sum(1 for i in active_items if i.get("status") == "done")
            completion = round((done_items / len(active_items)) * 100, 1) if active_items else 0.0
        else:
            done_tasks = sum(1 for t in tasks if t.get("status") == "done")
            completion = round((done_tasks / len(tasks)) * 100, 1) if tasks else 0.0

        # Current phase: first phase with in-progress items, or first non-done phase
        current_phase = self._detect_current_phase(items)

        # Health badge and progress signal
        health = self._compute_health_badge(tasks)
        progress_signal, last_completed_at = self._compute_progress_signal(tasks)

        return {
            "plan_title": plan_title,
            "completion_percent": completion,
            "current_phase": current_phase,
            "health": health,
            "progress_signal": progress_signal,
            "last_completed_at": last_completed_at,
        }

    def _build_workstreams(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Group tasks by feature/module into workstream bars with done/total counts."""
        groups: dict[str, dict[str, int]] = {}
        for task in tasks:
            key = task.get("feature") or task.get("module") or "ungrouped"
            if key not in groups:
                groups[key] = {"done": 0, "total": 0}
            groups[key]["total"] += 1
            if task.get("status") == "done":
                groups[key]["done"] += 1

        return [
            {"name": name, "done": counts["done"], "total": counts["total"]}
            for name, counts in sorted(groups.items())
        ]

    def _build_phases(
        self,
        phases: list[dict[str, Any]],
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build phase timeline with progress per phase."""
        if not phases:
            return []

        items_by_phase: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            pid = item.get("phase_id")
            if pid:
                items_by_phase.setdefault(pid, []).append(item)

        result = []
        for phase in phases:
            phase_items = items_by_phase.get(phase["id"], [])
            active = [i for i in phase_items if i.get("status") not in _EXCLUDED_FROM_PROGRESS]
            done = sum(1 for i in active if i.get("status") == "done")
            total = len(active)
            progress = round((done / total) * 100, 1) if total else 0.0

            # Determine phase status
            if total == 0:
                status = "empty"
            elif done == total:
                status = "done"
            elif any(i.get("status") in ("in_progress", "review", "blocked") for i in active):
                status = "active"
            else:
                status = "pending"

            result.append({
                "phase_id": phase["id"],
                "title": phase.get("title") or f"Phase {phase.get('phase_order', 0) + 1}",
                "phase_order": phase["phase_order"],
                "done": done,
                "total": total,
                "progress_percent": progress,
                "status": status,
            })

        return result

    def _build_quality(
        self,
        tasks: list[dict[str, Any]],
        runs: list[dict[str, Any]],
        review_feedback: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Quality metrics aggregated from canonical backend data.

        Metrics:
        - first_pass_rate: fraction of done tasks completed with zero retries
        - avg_retries: mean retry_count across done tasks
        - failed_tasks_last_7d: tasks with status "failed" in the last 7 days
        - escalations: tasks currently in "escalated" status
        - evaluator_pass_rate: fraction of done tasks that avoided qa-evaluator rejection
        - code_review_rejection_rate: fraction of done tasks that had a code-reviewer rejection
        """
        review_feedback = review_feedback or []
        done_tasks = [t for t in tasks if t.get("status") == "done"]
        done_count = len(done_tasks)
        done_task_ids = {t["id"] for t in done_tasks}

        # Failed tasks in the last 7 days (by state_changed_at or updated_at)
        seven_days_ago = datetime.now() - timedelta(days=7)
        failed_last_7d = 0
        for task in tasks:
            if task.get("status") != "failed":
                continue
            changed_at = task.get("state_changed_at") or task.get("updated_at") or ""
            if isinstance(changed_at, str) and changed_at:
                try:
                    dt = datetime.fromisoformat(changed_at.replace("Z", "+00:00")).replace(tzinfo=None)
                    if dt >= seven_days_ago:
                        failed_last_7d += 1
                except ValueError:
                    pass

        # Escalations: tasks currently in "escalated" status
        escalations = sum(1 for t in tasks if t.get("status") == "escalated")

        if done_count == 0:
            return {
                "first_pass_rate": 0.0,
                "avg_retries": 0.0,
                "done_count": 0,
                "total_runs": len(runs),
                "failed_tasks_last_7d": failed_last_7d,
                "escalations": escalations,
                "evaluator_pass_rate": 0.0,
                "code_review_rejection_rate": 0.0,
            }

        first_pass = sum(1 for t in done_tasks if (t.get("retry_count") or 0) == 0)
        total_retries = sum(t.get("retry_count") or 0 for t in done_tasks)

        # Evaluator pass rate: fraction of done tasks without a qa-evaluator rejection
        qa_rejected_ids = {
            fb["task_id"]
            for fb in review_feedback
            if fb.get("reviewer_identity") == "qa-evaluator"
        }
        qa_rejected_done = len(done_task_ids & qa_rejected_ids)
        evaluator_pass_rate = round((done_count - qa_rejected_done) / done_count, 3)

        # Code-review rejection rate: fraction of done tasks with at least one code-reviewer rejection
        cr_rejected_ids = {
            fb["task_id"]
            for fb in review_feedback
            if fb.get("reviewer_identity") == "code-reviewer"
        }
        cr_rejected_done = len(done_task_ids & cr_rejected_ids)
        code_review_rejection_rate = round(cr_rejected_done / done_count, 3)

        return {
            "first_pass_rate": round(first_pass / done_count, 3),
            "avg_retries": round(total_retries / done_count, 2),
            "done_count": done_count,
            "total_runs": len(runs),
            "failed_tasks_last_7d": failed_last_7d,
            "escalations": escalations,
            "evaluator_pass_rate": evaluator_pass_rate,
            "code_review_rejection_rate": code_review_rejection_rate,
        }

    def _build_issue_ledger(
        self,
        tasks: list[dict[str, Any]],
        items: list[dict[str, Any]],
        item_links: list[dict[str, Any]],
        review_feedback: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build a flat issue ledger for the project.

        Each issue has: issue_type, severity, source, linked_scope, recommended_action.
        Sorted by severity (critical → warning → info).
        """
        issues: list[dict[str, Any]] = []
        stale_threshold = datetime.now() - timedelta(hours=_STALE_EXECUTING_HOURS)

        # ── 1. Blocked dependency ───────────────────────────────────────
        for task in tasks:
            blockers = task.get("blocked_by") or []
            if blockers and task.get("status") not in ("done", "cancelled"):
                issues.append({
                    "issue_type": "blocked_dependency",
                    "severity": "critical",
                    "source": "task.blocked_by",
                    "linked_scope": {
                        "task_id": task["id"],
                        "task_title": task.get("title"),
                        "feature": task.get("feature") or task.get("module"),
                        "blocker_ids": blockers,
                    },
                    "recommended_action": "Resolve blocker tasks before continuing work on this task.",
                })

        # ── 2. Stale lifecycle ──────────────────────────────────────────
        for task in tasks:
            if task.get("status") not in _IN_FLIGHT_STATUSES:
                continue
            changed_at = task.get("state_changed_at") or task.get("updated_at") or ""
            if not isinstance(changed_at, str) or not changed_at:
                continue
            try:
                dt = datetime.fromisoformat(changed_at.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                continue
            if dt < stale_threshold:
                hours_stuck = round((datetime.now() - dt).total_seconds() / 3600, 1)
                severity = "critical" if hours_stuck >= 4 else "warning"
                issues.append({
                    "issue_type": "stale_lifecycle",
                    "severity": severity,
                    "source": "task.state_changed_at",
                    "linked_scope": {
                        "task_id": task["id"],
                        "task_title": task.get("title"),
                        "feature": task.get("feature") or task.get("module"),
                        "stuck_status": task["status"],
                        "hours_stuck": hours_stuck,
                    },
                    "recommended_action": (
                        f"Task has been in '{task['status']}' for {hours_stuck}h — "
                        "inspect for hangs or missing transitions."
                    ),
                })

        # ── 3. Repeat failure hotspot (aggregate by feature/module) ────
        retry_by_group: dict[str, int] = {}
        for task in tasks:
            retries = task.get("retry_count") or 0
            if retries > 0:
                key = task.get("feature") or task.get("module") or "ungrouped"
                retry_by_group[key] = retry_by_group.get(key, 0) + retries

        for feature, total_retries in retry_by_group.items():
            if total_retries >= _REPEAT_FAILURE_THRESHOLD:
                severity = "critical" if total_retries >= _REPEAT_FAILURE_THRESHOLD * 2 else "warning"
                issues.append({
                    "issue_type": "repeat_failure_hotspot",
                    "severity": severity,
                    "source": "task.retry_count (aggregate)",
                    "linked_scope": {
                        "feature": feature,
                        "total_retries": total_retries,
                    },
                    "recommended_action": (
                        f"Workstream '{feature}' has {total_retries} total retries — "
                        "review patterns of failure across tasks in this area."
                    ),
                })

        # ── 4. High retry hotspot (individual task) ────────────────────
        for task in tasks:
            retry_count = task.get("retry_count") or 0
            if retry_count >= _HIGH_RETRY_THRESHOLD:
                severity = "critical" if retry_count >= _HIGH_RETRY_THRESHOLD * 2 else "warning"
                issues.append({
                    "issue_type": "high_retry_hotspot",
                    "severity": severity,
                    "source": "task.retry_count",
                    "linked_scope": {
                        "task_id": task["id"],
                        "task_title": task.get("title"),
                        "feature": task.get("feature") or task.get("module"),
                        "retry_count": retry_count,
                    },
                    "recommended_action": (
                        f"Task has been retried {retry_count} times — "
                        "investigate root cause before further attempts."
                    ),
                })

        # ── 5. Unmapped task gap (plan item with no linked task) ────────
        if items:
            linked_item_ids = {link["item_id"] for link in item_links}
            for item in items:
                if item.get("status") in _EXCLUDED_FROM_PROGRESS:
                    continue
                if item["id"] not in linked_item_ids:
                    issues.append({
                        "issue_type": "unmapped_task_gap",
                        "severity": "warning",
                        "source": "plan_item.task_links",
                        "linked_scope": {
                            "item_id": item["id"],
                            "item_title": item.get("title"),
                            "item_key": item.get("item_key"),
                            "item_status": item.get("status"),
                        },
                        "recommended_action": (
                            "This plan item has no linked task — "
                            "create or link a task to ensure work is tracked."
                        ),
                    })

        # ── 6. Failed evaluator / review ───────────────────────────────
        # Group feedback by task_id to avoid one issue per feedback row
        feedback_by_task: dict[str, list[dict[str, Any]]] = {}
        for fb in review_feedback:
            feedback_by_task.setdefault(fb["task_id"], []).append(fb)

        task_map = {t["id"]: t for t in tasks}
        for task_id, feedbacks in feedback_by_task.items():
            task = task_map.get(task_id)
            if task is None or task.get("status") in ("done", "cancelled"):
                continue

            qa_rejections = [f for f in feedbacks if f.get("reviewer_identity") == "qa-evaluator"]
            cr_rejections = [f for f in feedbacks if f.get("reviewer_identity") == "code-reviewer"]

            if qa_rejections:
                issues.append({
                    "issue_type": "failed_evaluator_review",
                    "severity": "warning",
                    "source": "review_feedback.qa-evaluator",
                    "linked_scope": {
                        "task_id": task_id,
                        "task_title": task.get("title"),
                        "feature": task.get("feature") or task.get("module"),
                        "reviewer": "qa-evaluator",
                        "rejection_count": len(qa_rejections),
                        "review_cycle": task.get("review_cycle") or 0,
                    },
                    "recommended_action": (
                        "QA evaluator rejected this task — address quality issues "
                        "flagged in review feedback before re-submitting."
                    ),
                })

            if cr_rejections:
                review_cycle = task.get("review_cycle") or 0
                severity = "critical" if review_cycle >= _REVIEW_CYCLE_THRESHOLD else "warning"
                issues.append({
                    "issue_type": "failed_evaluator_review",
                    "severity": severity,
                    "source": "review_feedback.code-reviewer",
                    "linked_scope": {
                        "task_id": task_id,
                        "task_title": task.get("title"),
                        "feature": task.get("feature") or task.get("module"),
                        "reviewer": "code-reviewer",
                        "rejection_count": len(cr_rejections),
                        "review_cycle": review_cycle,
                    },
                    "recommended_action": (
                        f"Code review has been rejected {len(cr_rejections)} time(s) — "
                        "resolve all code-reviewer comments before re-submitting."
                    ),
                })

        # Sort: critical → warning → info
        issues.sort(key=lambda i: _SEVERITY_ORDER.get(i["severity"], 99))
        return issues

    def _build_throughput(
        self,
        tasks: list[dict[str, Any]],
        runs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Throughput: items done and cost breakdown by workstream."""
        done_tasks = [t for t in tasks if t.get("status") == "done"]

        # Cost by workstream
        task_feature_map = {t["id"]: t.get("feature") or t.get("module") or "ungrouped" for t in tasks}

        cost_by_workstream: dict[str, float] = {}
        total_cost = 0.0
        for run in runs:
            cost = run.get("cost_usd")
            if cost is not None:
                total_cost += float(cost)
                ws = task_feature_map.get(run["task_id"], "ungrouped")
                cost_by_workstream[ws] = cost_by_workstream.get(ws, 0.0) + float(cost)

        workstream_breakdown = [
            {"name": name, "done": 0, "cost_usd": round(cost, 4)}
            for name, cost in sorted(cost_by_workstream.items())
        ]
        # Fill in done counts
        for item in workstream_breakdown:
            item["done"] = sum(
                1 for t in done_tasks
                if (t.get("feature") or t.get("module") or "ungrouped") == item["name"]
            )

        return {
            "items_done": len(done_tasks),
            "total_tasks": len(tasks),
            "total_cost_usd": round(total_cost, 4),
            "by_workstream": workstream_breakdown,
        }

    def _build_recent_alerts(self, project_id: str) -> list[dict[str, Any]]:
        """Derive recent alerts from task/engine state (lightweight snapshot)."""
        from ..engine.health_monitor import HealthMonitor
        from .task_service import TaskService

        try:
            task_service = TaskService(supabase_client=self.supabase_client)
            alerts = HealthMonitor.snapshot_alerts(task_service, project_id=project_id)
            alerts.sort(key=lambda a: a.get("timestamp", ""), reverse=True)
            return alerts[:10]
        except Exception as exc:
            logger.warning(f"Failed to compute alerts for cockpit | project_id={project_id} | error={exc}")
            return []

    # ── Helpers ─────────────────────────────────────────────────────────

    def _detect_current_phase(self, items: list[dict[str, Any]]) -> str | None:
        """Identify the current active phase from plan items."""
        if not items:
            return None

        phase_status: dict[str, dict[str, int]] = {}
        for item in items:
            pid = item.get("phase_id")
            if not pid:
                continue
            if pid not in phase_status:
                phase_status[pid] = {"active": 0, "done": 0, "total": 0}
            status = item.get("status", "planned")
            if status not in _EXCLUDED_FROM_PROGRESS:
                phase_status[pid]["total"] += 1
                if status == "done":
                    phase_status[pid]["done"] += 1
                if status in ("in_progress", "review", "blocked"):
                    phase_status[pid]["active"] += 1

        # Return the first phase with active items
        for pid, counts in phase_status.items():
            if counts["active"] > 0:
                return pid

        # If no active phase, return first incomplete phase
        for pid, counts in phase_status.items():
            if counts["total"] > 0 and counts["done"] < counts["total"]:
                return pid

        return None

    def _compute_health_badge(self, tasks: list[dict[str, Any]]) -> str:
        """Compute a simple health badge: healthy, warning, critical."""
        if not tasks:
            return "healthy"

        done_tasks = [t for t in tasks if t.get("status") == "done"]
        failed_escalated = sum(1 for t in tasks if t.get("status") in ("failed", "escalated"))
        stale_executing = 0

        stale_threshold = datetime.now() - timedelta(hours=_STALE_EXECUTING_HOURS)
        for task in tasks:
            if task.get("status") != "executing":
                continue
            changed_at = task.get("state_changed_at") or task.get("updated_at") or ""
            if isinstance(changed_at, str) and changed_at:
                try:
                    dt = datetime.fromisoformat(changed_at.replace("Z", "+00:00")).replace(tzinfo=None)
                    if dt < stale_threshold:
                        stale_executing += 1
                except ValueError:
                    pass

        total = len(tasks)
        if failed_escalated > total * 0.15 or stale_executing >= 3:
            return "critical"
        if failed_escalated > total * 0.05 or stale_executing >= 1:
            return "warning"

        # Check first-pass rate
        if done_tasks:
            first_pass = sum(1 for t in done_tasks if (t.get("retry_count") or 0) == 0)
            if first_pass / len(done_tasks) < 0.6:
                return "warning"

        return "healthy"

    def _compute_progress_signal(self, tasks: list[dict[str, Any]]) -> tuple[str, str | None]:
        """Compute project-level progress signal and last completion timestamp.

        Returns (progress_signal, last_completed_at) where progress_signal is one of:
        - "idle"    — no tasks or all tasks in terminal statuses
        - "blocked" — at least one unfinished task has active blockers
        - "stale"   — no task state change in the last STALE_PROJECT_HOURS hours
        - "active"  — at least one task state changed within ACTIVE_PROJECT_HOURS

        Priority order (highest to lowest): blocked > stale > active > idle.
        """
        if not tasks:
            return "idle", None

        # Find the most recent completion timestamp
        last_completed_at: str | None = None
        latest_completion_dt: datetime | None = None
        for task in tasks:
            if task.get("status") != "done":
                continue
            ts = task.get("state_changed_at") or task.get("updated_at")
            if not isinstance(ts, str) or not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                if latest_completion_dt is None or dt > latest_completion_dt:
                    latest_completion_dt = dt
                    last_completed_at = ts
            except ValueError:
                pass

        # If all tasks are terminal, project is idle
        unfinished = [t for t in tasks if t.get("status") not in _TERMINAL_STATUSES]
        if not unfinished:
            return "idle", last_completed_at

        # Blocked: any unfinished task has a non-empty blocked_by list
        has_blockers = any(
            bool(t.get("blocked_by"))
            for t in unfinished
        )

        # Determine most recent state change across all tasks
        most_recent_dt: datetime | None = None
        for task in tasks:
            ts = task.get("state_changed_at") or task.get("updated_at")
            if not isinstance(ts, str) or not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                if most_recent_dt is None or dt > most_recent_dt:
                    most_recent_dt = dt
            except ValueError:
                pass

        now = datetime.now()
        is_stale = most_recent_dt is None or (now - most_recent_dt).total_seconds() >= _STALE_PROJECT_HOURS * 3600

        if has_blockers:
            return "blocked", last_completed_at
        if is_stale:
            return "stale", last_completed_at
        return "active", last_completed_at
