"""Rollup service for project implementation plans.

Computes per-item, phase-level, and plan-level aggregations from linked tasks
and execution runs. Progress is derived from item status using configurable
weights; cost data is fail-open (absent cost_usd returns None, not an error).
"""

from __future__ import annotations

from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

_PLANS_TABLE = "project_implementation_plans"
_PHASES_TABLE = "project_implementation_phases"
_ITEMS_TABLE = "project_implementation_items"
_LINKS_TABLE = "project_implementation_item_task_links"
_RUNS_TABLE = "archon_execution_runs"
_TASKS_TABLE = "archon_tasks"

# Progress heuristic weights per item status (0.0 – 1.0).
# Deferred/cancelled items are excluded from phase/plan progress denominators.
ITEM_PROGRESS_WEIGHTS: dict[str, float] = {
    "planned": 0.0,
    "ready": 0.05,
    "in_progress": 0.50,
    "blocked": 0.50,
    "review": 0.90,
    "done": 1.00,
    "deferred": 0.0,
    "cancelled": 0.0,
}

# Statuses excluded from progress denominator (they don't represent active work).
_EXCLUDED_FROM_PROGRESS = {"deferred", "cancelled"}


def _item_progress(status: str) -> float:
    """Return progress fraction (0-1) for a single item based on its status."""
    return ITEM_PROGRESS_WEIGHTS.get(status, 0.0)


def _aggregate_progress(items: list[dict[str, Any]]) -> float:
    """Compute mean progress percent across active items (excludes deferred/cancelled).

    Returns 0.0 when there are no active items.
    """
    active = [i for i in items if i.get("status") not in _EXCLUDED_FROM_PROGRESS]
    if not active:
        return 0.0
    total = sum(_item_progress(i.get("status", "planned")) for i in active)
    return round((total / len(active)) * 100, 2)


def _merge_status_distribution(distributions: list[dict[str, int]]) -> dict[str, int]:
    """Merge multiple status distribution dicts into one."""
    merged: dict[str, int] = {}
    for dist in distributions:
        for status, count in dist.items():
            merged[status] = merged.get(status, 0) + count
    return merged


def _sum_cost(costs: list[float | None]) -> float | None:
    """Sum cost values; returns None when no values are available."""
    known = [c for c in costs if c is not None]
    return sum(known) if known else None


class PlanRollupService:
    """Compute rollup statistics for plans, phases, and items."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    # ── Internal batch loaders ──────────────────────────────────────────

    def _load_items(self, plan_id: str, phase_id: str | None = None) -> list[dict[str, Any]]:
        query = self.supabase_client.table(_ITEMS_TABLE).select("id, plan_id, phase_id, title, status, item_key, item_order")
        query = query.eq("plan_id", plan_id)
        if phase_id:
            query = query.eq("phase_id", phase_id)
        result = query.execute()
        return result.data or []

    def _load_phases(self, plan_id: str) -> list[dict[str, Any]]:
        result = (
            self.supabase_client.table(_PHASES_TABLE)
            .select("id, plan_id, title, phase_order")
            .eq("plan_id", plan_id)
            .order("phase_order")
            .execute()
        )
        return result.data or []

    def _load_task_links_for_items(self, item_ids: list[str]) -> list[dict[str, Any]]:
        if not item_ids:
            return []
        result = (
            self.supabase_client.table(_LINKS_TABLE)
            .select("item_id, task_id")
            .in_("item_id", item_ids)
            .execute()
        )
        return result.data or []

    def _load_task_statuses(self, task_ids: list[str]) -> dict[str, str]:
        """Return {task_id: status} for the given task IDs."""
        if not task_ids:
            return {}
        result = (
            self.supabase_client.table(_TASKS_TABLE)
            .select("id, status")
            .in_("id", task_ids)
            .execute()
        )
        return {row["id"]: row["status"] for row in (result.data or [])}

    def _load_runs_for_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        """Return execution run rows for the given task IDs."""
        if not task_ids:
            return []
        result = (
            self.supabase_client.table(_RUNS_TABLE)
            .select("task_id, cost_usd")
            .in_("task_id", task_ids)
            .execute()
        )
        return result.data or []

    # ── Public API ──────────────────────────────────────────────────────

    def get_item_rollup(self, item_id: str) -> tuple[bool, dict[str, Any]]:
        """Compute rollup stats for a single plan item."""
        try:
            item_result = (
                self.supabase_client.table(_ITEMS_TABLE)
                .select("id, plan_id, phase_id, title, status, item_key, item_order")
                .eq("id", item_id)
                .maybe_single()
                .execute()
            )
            if item_result is None or not item_result.data:
                return False, {"error": f"Item {item_id} not found"}

            item = item_result.data
            links = self._load_task_links_for_items([item_id])
            task_ids = [lnk["task_id"] for lnk in links]

            task_statuses = self._load_task_statuses(task_ids)
            runs = self._load_runs_for_tasks(task_ids)

            status_dist: dict[str, int] = {}
            for ts in task_statuses.values():
                status_dist[ts] = status_dist.get(ts, 0) + 1

            run_count = len(runs)
            cost_usd = _sum_cost([r.get("cost_usd") for r in runs])
            progress = round(_item_progress(item["status"]) * 100, 2)

            return True, {
                "rollup": {
                    "item_id": item_id,
                    "item_key": item.get("item_key"),
                    "title": item["title"],
                    "status": item["status"],
                    "task_count": len(task_ids),
                    "status_distribution": status_dist,
                    "run_count": run_count,
                    "cost_usd": cost_usd,
                    "progress_percent": progress,
                }
            }
        except Exception as exc:
            logger.error(f"Failed to compute item rollup | item_id={item_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def get_phase_rollup(self, plan_id: str, phase_id: str) -> tuple[bool, dict[str, Any]]:
        """Compute rollup stats for a single phase, aggregating child item rollups."""
        try:
            phase_result = (
                self.supabase_client.table(_PHASES_TABLE)
                .select("id, plan_id, title, phase_order")
                .eq("id", phase_id)
                .eq("plan_id", plan_id)
                .maybe_single()
                .execute()
            )
            if phase_result is None or not phase_result.data:
                return False, {"error": f"Phase {phase_id} not found in plan {plan_id}"}

            phase = phase_result.data
            items = self._load_items(plan_id, phase_id=phase_id)
            rollup = self._build_phase_rollup(phase, items)
            return True, {"rollup": rollup}
        except Exception as exc:
            logger.error(
                f"Failed to compute phase rollup | plan_id={plan_id} | phase_id={phase_id} | error={exc}",
                exc_info=True,
            )
            return False, {"error": str(exc)}

    def get_plan_rollup(self, plan_id: str) -> tuple[bool, dict[str, Any]]:
        """Compute rollup stats for an entire plan, including all phases and unphased items."""
        try:
            plan_result = (
                self.supabase_client.table(_PLANS_TABLE)
                .select("id, title, status")
                .eq("id", plan_id)
                .maybe_single()
                .execute()
            )
            if plan_result is None or not plan_result.data:
                return False, {"error": f"Plan {plan_id} not found"}

            plan = plan_result.data
            phases = self._load_phases(plan_id)
            all_items = self._load_items(plan_id)

            # Batch-load task links and runs for all items at once.
            item_ids = [i["id"] for i in all_items]
            links = self._load_task_links_for_items(item_ids)

            task_ids = list({lnk["task_id"] for lnk in links})
            task_statuses = self._load_task_statuses(task_ids)
            runs = self._load_runs_for_tasks(task_ids)

            # Index links by item_id for efficient lookup.
            links_by_item: dict[str, list[str]] = {}
            for lnk in links:
                links_by_item.setdefault(lnk["item_id"], []).append(lnk["task_id"])

            # Index runs by task_id.
            runs_by_task: dict[str, list[dict[str, Any]]] = {}
            for run in runs:
                runs_by_task.setdefault(run["task_id"], []).append(run)

            def _build_item_rollup(item: dict[str, Any]) -> dict[str, Any]:
                iid = item["id"]
                linked_tasks = links_by_item.get(iid, [])
                item_runs = [r for tid in linked_tasks for r in runs_by_task.get(tid, [])]
                item_status_dist: dict[str, int] = {}
                for tid in linked_tasks:
                    ts = task_statuses.get(tid)
                    if ts:
                        item_status_dist[ts] = item_status_dist.get(ts, 0) + 1
                return {
                    "item_id": iid,
                    "item_key": item.get("item_key"),
                    "title": item["title"],
                    "status": item["status"],
                    "task_count": len(linked_tasks),
                    "status_distribution": item_status_dist,
                    "run_count": len(item_runs),
                    "cost_usd": _sum_cost([r.get("cost_usd") for r in item_runs]),
                    "progress_percent": round(_item_progress(item["status"]) * 100, 2),
                }

            phase_ids_set = {p["id"] for p in phases}
            items_by_phase: dict[str | None, list[dict[str, Any]]] = {p["id"]: [] for p in phases}
            items_by_phase[None] = []

            for item in all_items:
                pid = item.get("phase_id")
                if pid not in phase_ids_set:
                    pid = None
                items_by_phase.setdefault(pid, []).append(item)

            phase_rollups: list[dict[str, Any]] = []
            for phase in phases:
                phase_items = items_by_phase.get(phase["id"], [])
                item_rollups = [_build_item_rollup(i) for i in phase_items]
                phase_rollups.append(self._aggregate_phase(phase, phase_items, item_rollups))

            unphased_items = items_by_phase.get(None, [])
            unphased_rollups = [_build_item_rollup(i) for i in unphased_items]

            all_item_rollups = [r for pr in phase_rollups for r in pr["items"]] + unphased_rollups
            plan_task_count = sum(r["task_count"] for r in all_item_rollups)
            plan_run_count = sum(r["run_count"] for r in all_item_rollups)
            plan_cost = _sum_cost([r["cost_usd"] for r in all_item_rollups])

            all_status_dist = _merge_status_distribution([r["status_distribution"] for r in all_item_rollups])
            plan_progress = _aggregate_progress(all_items)

            # N1-1: Compute done/total counts and blocked items for frontend
            done_count = sum(1 for i in all_items if i.get("status") == "done")
            total_count = len(all_items)

            blocked_items_list = [
                {
                    "item_id": i["id"],
                    "item_key": i.get("item_key"),
                    "title": i["title"],
                    "status": i["status"],
                }
                for i in all_items
                if i.get("status") == "blocked"
            ]

            # N1-1: Build workstream aggregation from item_key prefixes
            # Item keys follow the pattern "{Workstream}-P{Phase}-{Seq}" (e.g. "C-P1-01")
            workstream_map: dict[str, dict[str, int]] = {}
            for item in all_items:
                key = item.get("item_key") or ""
                ws_prefix = key.split("-")[0] if "-" in key else ""
                if not ws_prefix:
                    continue
                if ws_prefix not in workstream_map:
                    workstream_map[ws_prefix] = {"done": 0, "total": 0}
                workstream_map[ws_prefix]["total"] += 1
                if item.get("status") == "done":
                    workstream_map[ws_prefix]["done"] += 1

            workstreams = [
                {"key": ws, "done": counts["done"], "total": counts["total"]}
                for ws, counts in sorted(workstream_map.items())
            ]

            return True, {
                "rollup": {
                    "plan_id": plan_id,
                    "title": plan["title"],
                    "plan_title": plan["title"],
                    "phase_count": len(phases),
                    "item_count": total_count,
                    "done_count": done_count,
                    "total_count": total_count,
                    "task_count": plan_task_count,
                    "status_distribution": all_status_dist,
                    "run_count": plan_run_count,
                    "cost_usd": plan_cost,
                    "progress_percent": plan_progress,
                    "phases": phase_rollups,
                    "unphased_items": unphased_rollups,
                    "blocked_items": blocked_items_list,
                    "workstreams": workstreams,
                }
            }
        except Exception as exc:
            logger.error(f"Failed to compute plan rollup | plan_id={plan_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    # ── Internal aggregation helpers ────────────────────────────────────

    def _build_phase_rollup(self, phase: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a phase rollup dict given pre-loaded items (no batch context)."""
        item_ids = [i["id"] for i in items]
        links = self._load_task_links_for_items(item_ids)
        task_ids = list({lnk["task_id"] for lnk in links})
        task_statuses = self._load_task_statuses(task_ids)
        runs = self._load_runs_for_tasks(task_ids)

        links_by_item: dict[str, list[str]] = {}
        for lnk in links:
            links_by_item.setdefault(lnk["item_id"], []).append(lnk["task_id"])

        runs_by_task: dict[str, list[dict[str, Any]]] = {}
        for run in runs:
            runs_by_task.setdefault(run["task_id"], []).append(run)

        item_rollups: list[dict[str, Any]] = []
        for item in items:
            iid = item["id"]
            linked_tasks = links_by_item.get(iid, [])
            item_runs = [r for tid in linked_tasks for r in runs_by_task.get(tid, [])]
            item_status_dist: dict[str, int] = {}
            for tid in linked_tasks:
                ts = task_statuses.get(tid)
                if ts:
                    item_status_dist[ts] = item_status_dist.get(ts, 0) + 1
            item_rollups.append({
                "item_id": iid,
                "item_key": item.get("item_key"),
                "title": item["title"],
                "status": item["status"],
                "task_count": len(linked_tasks),
                "status_distribution": item_status_dist,
                "run_count": len(item_runs),
                "cost_usd": _sum_cost([r.get("cost_usd") for r in item_runs]),
                "progress_percent": round(_item_progress(item["status"]) * 100, 2),
            })

        return self._aggregate_phase(phase, items, item_rollups)

    def _aggregate_phase(
        self,
        phase: dict[str, Any],
        items: list[dict[str, Any]],
        item_rollups: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate item rollup dicts into a phase-level rollup dict."""
        task_count = sum(r["task_count"] for r in item_rollups)
        run_count = sum(r["run_count"] for r in item_rollups)
        cost_usd = _sum_cost([r["cost_usd"] for r in item_rollups])
        status_dist = _merge_status_distribution([r["status_distribution"] for r in item_rollups])
        progress = _aggregate_progress(items)

        return {
            "phase_id": phase["id"],
            "title": phase["title"],
            "phase_order": phase["phase_order"],
            "item_count": len(items),
            "task_count": task_count,
            "status_distribution": status_dist,
            "run_count": run_count,
            "cost_usd": cost_usd,
            "progress_percent": progress,
            "items": item_rollups,
        }
