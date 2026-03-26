"""
Engine API endpoints — review config, concurrency config, status, health, and queue for LeanKit V3 Task Engine.
"""

import json
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config.logfire_config import get_logger
from ..services.cost_budget_service import CostBudgetService
from ..services.credential_service import credential_service
from ..services.engine.runner_routing import get_runner_capabilities
from ..services.projects import ProjectService, TaskService
from ..services.projects.execution_run_service import ExecutionRunService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/engine", tags=["engine"])

# Default config stored in archon_settings under "engine" category
_CONFIG_KEY = "REVIEW_CONFIG"
_CONCURRENCY_CONFIG_KEY = "CONCURRENCY_CONFIG"
_CONFIG_CATEGORY = "engine"


class ReviewConfigRequest(BaseModel):
    review_mode: str | None = None
    security_override_to_api: bool | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int | None = None
    api_fallback_to_self_review: bool | None = None
    independent_review_enabled: bool | None = None
    confidence_approve_threshold: float | None = None
    confidence_retry_threshold: float | None = None


_DEFAULTS: dict[str, Any] = {
    "review_mode": "self-review",
    "security_override_to_api": True,
    "provider": "anthropic",
    "model": "",
    "temperature": 0.3,
    "max_tokens": 2000,
    "timeout": 60,
    "api_fallback_to_self_review": True,
    "independent_review_enabled": True,
    "confidence_approve_threshold": 0.8,
    "confidence_retry_threshold": 0.5,
}


@router.get("/review-config")
async def get_review_config():
    """Get current review configuration."""
    try:
        stored = await credential_service.get_credential(_CONFIG_KEY)
        if stored and isinstance(stored, dict):
            config = {**_DEFAULTS, **stored}
        else:
            config = dict(_DEFAULTS)
        return config
    except Exception as e:
        logger.error(f"Failed to get review config: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


@router.put("/review-config")
async def update_review_config(request: ReviewConfigRequest):
    """Update review configuration (partial updates supported)."""
    try:
        # Get current config
        stored = await credential_service.get_credential(_CONFIG_KEY)
        current = {**_DEFAULTS}
        if stored and isinstance(stored, dict):
            current.update(stored)

        # Apply updates
        updates = request.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Validate mode
        valid_modes = {"self-review", "api", "multi-perspective"}
        if "review_mode" in updates and updates["review_mode"] not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid review_mode: must be one of {valid_modes}",
            )

        valid_providers = {"anthropic", "openai", "google"}
        if "provider" in updates and updates["provider"] not in valid_providers:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid provider: must be one of {valid_providers}",
            )

        current.update(updates)

        # Store as JSON string in settings
        await credential_service.set_credential(
            _CONFIG_KEY,
            json.dumps(current),
            category=_CONFIG_CATEGORY,
            description="Task engine review configuration",
        )

        logger.info(f"Review config updated | fields={list(updates.keys())}")
        return {"message": "Review config updated", "config": current}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update review config: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# Concurrency configuration
# ---------------------------------------------------------------------------

_CONCURRENCY_DEFAULTS: dict[str, Any] = {
    "max_parallel_default": int(os.environ.get("MAX_PARALLEL", "3")),
    "max_parallel_global": int(os.environ.get("MAX_PARALLEL_GLOBAL", "10")),
}


class ConcurrencyConfigRequest(BaseModel):
    max_parallel_default: int | None = None
    max_parallel_global: int | None = None


@router.get("/concurrency-config")
async def get_concurrency_config():
    """Get current engine concurrency limits."""
    try:
        stored = await credential_service.get_credential(_CONCURRENCY_CONFIG_KEY)
        if stored and isinstance(stored, dict):
            config = {**_CONCURRENCY_DEFAULTS, **stored}
        else:
            config = dict(_CONCURRENCY_DEFAULTS)
        return config
    except Exception as e:
        logger.error(f"Failed to get concurrency config: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


@router.put("/concurrency-config")
async def update_concurrency_config(request: ConcurrencyConfigRequest):
    """Update engine concurrency limits (takes effect on next engine restart)."""
    try:
        stored = await credential_service.get_credential(_CONCURRENCY_CONFIG_KEY)
        current = {**_CONCURRENCY_DEFAULTS}
        if stored and isinstance(stored, dict):
            current.update(stored)

        updates = request.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Validate limits
        for field in ("max_parallel_default", "max_parallel_global"):
            if field in updates:
                val = updates[field]
                if not isinstance(val, int) or val < 1 or val > 50:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{field} must be an integer between 1 and 50",
                    )

        current.update(updates)

        await credential_service.set_credential(
            _CONCURRENCY_CONFIG_KEY,
            json.dumps(current),
            category=_CONFIG_CATEGORY,
            description="Task engine concurrency configuration",
        )

        logger.info(f"Concurrency config updated | fields={list(updates.keys())}")
        return {"message": "Concurrency config updated (restart engine to apply)", "config": current}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update concurrency config: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# Agent pool configuration — shared pools with per-project slot limits
# ---------------------------------------------------------------------------

_AGENT_POOLS_CONFIG_KEY = "AGENT_POOLS_CONFIG"

_AGENT_POOL_DEFAULTS: dict[str, Any] = {
    "pools": []
}


class AgentPoolDefinition(BaseModel):
    pool_id: str
    total_slots: int
    priority_reserve: int = 1
    default_project_slots: int = 1


class AgentPoolsConfigRequest(BaseModel):
    pools: list[AgentPoolDefinition]


@router.get("/agent-pools")
async def get_agent_pools():
    """List all configured shared agent pools.

    Pools define cross-project execution slot budgets. Each pool has a
    ``total_slots`` ceiling and an optional ``priority_reserve`` — slots
    exclusively available to tasks with ``critical`` or ``high`` priority.

    Projects opt into a pool via ``capacity_policy.pool_id`` in their engine
    policy, and declare how many pool slots they may hold simultaneously via
    ``capacity_policy.pool_slots``.
    """
    try:
        stored = await credential_service.get_credential(_AGENT_POOLS_CONFIG_KEY)
        if stored and isinstance(stored, dict):
            return stored
        return dict(_AGENT_POOL_DEFAULTS)
    except Exception as e:
        logger.error(f"Failed to get agent pools config: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


@router.put("/agent-pools")
async def update_agent_pools(request: AgentPoolsConfigRequest):
    """Create or replace the set of shared agent pools.

    Pool changes take effect on the next engine restart.

    ``pool_id`` must be unique. ``total_slots`` must be >= 1.
    ``priority_reserve`` must be < ``total_slots``.
    """
    try:
        seen_ids: set[str] = set()
        for pool in request.pools:
            if not pool.pool_id.strip():
                raise HTTPException(status_code=400, detail="pool_id must be a non-empty string")
            if pool.pool_id in seen_ids:
                raise HTTPException(status_code=400, detail=f"Duplicate pool_id: '{pool.pool_id}'")
            seen_ids.add(pool.pool_id)
            if pool.total_slots < 1:
                raise HTTPException(status_code=400, detail=f"Pool '{pool.pool_id}': total_slots must be >= 1")
            if pool.priority_reserve < 0 or pool.priority_reserve >= pool.total_slots:
                raise HTTPException(
                    status_code=400,
                    detail=f"Pool '{pool.pool_id}': priority_reserve must be >= 0 and < total_slots",
                )
            if pool.default_project_slots < 1:
                raise HTTPException(
                    status_code=400,
                    detail=f"Pool '{pool.pool_id}': default_project_slots must be >= 1",
                )

        payload = {"pools": [p.model_dump() for p in request.pools]}

        await credential_service.set_credential(
            _AGENT_POOLS_CONFIG_KEY,
            json.dumps(payload),
            category=_CONFIG_CATEGORY,
            description="Shared agent pool configuration",
        )
        logger.info(f"Agent pools config updated | pools={[p.pool_id for p in request.pools]}")
        return {"message": "Agent pools saved (restart engine to apply)", "config": payload}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update agent pools config: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


@router.get("/runner-capabilities")
async def get_runner_capability_matrix():
    """Return the current runner capability matrix and default routing baseline."""
    return {
        "default_runner": "claude-code-cli",
        "runners": get_runner_capabilities(),
    }


# ---------------------------------------------------------------------------
# Engine heartbeat — tracks engine process start time and last poll timestamp
# ---------------------------------------------------------------------------

_HEARTBEAT_CONFIG_KEY = "ENGINE_HEARTBEAT"


class HeartbeatRequest(BaseModel):
    started_at: str | None = None
    last_poll_at: str | None = None


@router.post("/heartbeat")
async def register_engine_heartbeat(request: HeartbeatRequest):
    """Register or update engine process heartbeat for uptime tracking.

    Called by the engine process on startup (to record started_at) and
    optionally on each poll cycle (to update last_poll_at).
    No auth required — internal service endpoint.
    """
    try:
        stored = await credential_service.get_credential(_HEARTBEAT_CONFIG_KEY)
        current: dict[str, Any] = stored if isinstance(stored, dict) else {}

        if request.started_at is not None:
            current["started_at"] = request.started_at
        if request.last_poll_at is not None:
            current["last_poll_at"] = request.last_poll_at

        await credential_service.set_credential(
            _HEARTBEAT_CONFIG_KEY,
            json.dumps(current),
            category=_CONFIG_CATEGORY,
            description="Engine process heartbeat for uptime tracking",
        )

        return {"message": "Heartbeat registered", "heartbeat": current}

    except Exception as e:
        logger.error(f"Failed to register engine heartbeat: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# Engine status — derived from task statuses in database
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_engine_status():
    """Show engine execution slot usage per project.

    Derives status from tasks currently in 'executing' state,
    combined with configured concurrency limits from project settings.
    """
    try:
        task_service = TaskService()
        project_service = ProjectService()
        budget_service = CostBudgetService()

        # Get all executing tasks
        ok, result = task_service.list_tasks(
            status="executing",
            include_closed=False,
            include_archived=False,
        )
        executing_tasks = result.get("tasks", []) if ok else []

        # Count executing tasks per project
        per_project_executing: dict[str, int] = {}
        for task in executing_tasks:
            pid = task.get("project_id", "unknown")
            per_project_executing[pid] = per_project_executing.get(pid, 0) + 1

        # Get concurrency config
        stored = await credential_service.get_credential(_CONCURRENCY_CONFIG_KEY)
        concurrency_config = {**_CONCURRENCY_DEFAULTS}
        if stored and isinstance(stored, dict):
            concurrency_config.update(stored)

        max_global = concurrency_config["max_parallel_global"]
        default_per_project = concurrency_config["max_parallel_default"]

        # Get per-project limits from office_settings
        ok, proj_result = project_service.list_office_configs()
        project_slots = []
        if ok:
            for proj in proj_result.get("projects", []):
                pid = proj["id"]
                settings = proj.get("office_settings") or {}
                max_concurrent = settings.get("max_concurrent", default_per_project)
                try:
                    max_concurrent = int(max_concurrent)
                except (TypeError, ValueError):
                    max_concurrent = default_per_project

                used = per_project_executing.get(pid, 0)

                # Fetch budget status for this project
                budget_ok, budget_status = budget_service.get_cost_status(pid)
                budget_summary: dict[str, Any] = {"status": "unknown"}
                if budget_ok:
                    budget_summary = {
                        "status": budget_status.get("status", "ok"),
                        "today_cost_usd": budget_status.get("today", {}).get("cost_usd", 0),
                        "today_budget_usd": budget_status.get("today", {}).get("budget_usd", 0),
                        "weekly_cost_usd": budget_status.get("weekly", {}).get("total_cost_usd", 0),
                        "weekly_budget_usd": budget_status.get("weekly", {}).get("budget_usd", 0),
                        "today_usage_pct": budget_status.get("today", {}).get("usage_pct", 0),
                        "weekly_usage_pct": budget_status.get("weekly", {}).get("usage_pct", 0),
                    }

                project_slots.append({
                    "project_id": pid,
                    "project_name": proj.get("title", ""),
                    "max_concurrent": max_concurrent,
                    "slots_used": used,
                    "slots_available": max(0, max_concurrent - used),
                    "budget": budget_summary,
                })

        total_executing = sum(per_project_executing.values())

        return {
            "global": {
                "max_parallel_global": max_global,
                "total_executing": total_executing,
                "global_slots_available": max(0, max_global - total_executing),
            },
            "default_per_project": default_per_project,
            "projects": project_slots,
        }

    except Exception as e:
        logger.error(f"Failed to get engine status: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# Engine health — structured health + PID watchdog status
# ---------------------------------------------------------------------------

# Stale execution run threshold: runs in "running" state older than this
# are considered potentially orphaned (engine crash / watchdog missed)
_ORPHAN_THRESHOLD_SECONDS = int(os.environ.get("ENGINE_ORPHAN_THRESHOLD_SECONDS", "2400"))


@router.get("/health")
async def get_engine_health():
    """Structured engine health including active runs, queue depth, capacity, budget, and PID watchdog status.

    All state is derived from the database since the engine runs as an external process.
    No auth required — internal service endpoint.
    """
    try:
        task_service = TaskService()
        project_service = ProjectService()
        budget_service = CostBudgetService()
        run_service = ExecutionRunService()

        # Fetch executing and assigned tasks in parallel via two DB calls
        ok_exec, exec_result = task_service.list_tasks(
            status="executing",
            include_closed=False,
            include_archived=False,
            exclude_large_fields=True,
        )
        executing_tasks = exec_result.get("tasks", []) if ok_exec else []

        ok_queue, queue_result = task_service.list_tasks(
            status="assigned",
            include_closed=False,
            include_archived=False,
            exclude_large_fields=True,
        )
        queued_tasks = queue_result.get("tasks", []) if ok_queue else []

        # Get concurrency config and heartbeat state
        stored_concurrency = await credential_service.get_credential(_CONCURRENCY_CONFIG_KEY)
        concurrency_config = {**_CONCURRENCY_DEFAULTS}
        if stored_concurrency and isinstance(stored_concurrency, dict):
            concurrency_config.update(stored_concurrency)

        stored_heartbeat = await credential_service.get_credential(_HEARTBEAT_CONFIG_KEY)
        heartbeat: dict[str, Any] = stored_heartbeat if isinstance(stored_heartbeat, dict) else {}

        max_global = concurrency_config["max_parallel_global"]
        default_per_project = concurrency_config["max_parallel_default"]

        # Per-project breakdown
        per_project_executing: dict[str, int] = {}
        for task in executing_tasks:
            pid = task.get("project_id", "unknown")
            per_project_executing[pid] = per_project_executing.get(pid, 0) + 1

        per_project_queued: dict[str, int] = {}
        for task in queued_tasks:
            pid = task.get("project_id", "unknown")
            per_project_queued[pid] = per_project_queued.get(pid, 0) + 1

        # Check for potentially orphaned execution runs (running for too long)
        now_utc = datetime.now(UTC)
        ok_runs, runs_result = run_service.list_runs(status="running", limit=100)
        orphaned_run_ids: list[str] = []
        if ok_runs:
            for run in runs_result.get("runs", []):
                started_at_raw = run.get("started_at")
                if not started_at_raw:
                    continue
                try:
                    started_at = datetime.fromisoformat(started_at_raw.replace("Z", "+00:00"))
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=UTC)
                    age_seconds = (now_utc - started_at).total_seconds()
                    if age_seconds > _ORPHAN_THRESHOLD_SECONDS:
                        orphaned_run_ids.append(run["id"])
                except (ValueError, TypeError):
                    pass

        pid_watchdog_status = "orphaned_detected" if orphaned_run_ids else "ok"

        # Get per-project configs and build project health summaries
        ok_proj, proj_result = project_service.list_office_configs()
        project_health: list[dict[str, Any]] = []
        if ok_proj:
            for proj in proj_result.get("projects", []):
                pid = proj["id"]
                settings = proj.get("office_settings") or {}
                max_concurrent = default_per_project
                try:
                    max_concurrent = int(settings.get("max_concurrent", default_per_project))
                except (TypeError, ValueError):
                    pass

                used = per_project_executing.get(pid, 0)
                queued = per_project_queued.get(pid, 0)

                budget_ok, budget_status = budget_service.get_cost_status(pid)
                budget_summary: dict[str, Any] = {"status": "unknown"}
                if budget_ok:
                    budget_summary = {
                        "status": budget_status.get("status", "ok"),
                        "today_cost_usd": budget_status.get("today", {}).get("cost_usd", 0),
                        "today_budget_usd": budget_status.get("today", {}).get("budget_usd", 0),
                        "weekly_cost_usd": budget_status.get("weekly", {}).get("total_cost_usd", 0),
                        "weekly_budget_usd": budget_status.get("weekly", {}).get("budget_usd", 0),
                        "today_usage_pct": budget_status.get("today", {}).get("usage_pct", 0),
                        "weekly_usage_pct": budget_status.get("weekly", {}).get("usage_pct", 0),
                    }

                project_health.append({
                    "project_id": pid,
                    "project_name": proj.get("title", ""),
                    "active_runs": used,
                    "queue_depth": queued,
                    "capacity": {
                        "used": used,
                        "max": max_concurrent,
                        "available": max(0, max_concurrent - used),
                    },
                    "budget": budget_summary,
                })

        total_active = sum(per_project_executing.values())
        total_queued = sum(per_project_queued.values())

        # Compute uptime from heartbeat started_at
        engine_started_at: str | None = heartbeat.get("started_at")
        last_poll_at: str | None = heartbeat.get("last_poll_at")
        uptime_seconds: float | None = None
        if engine_started_at:
            try:
                started_dt = datetime.fromisoformat(engine_started_at.replace("Z", "+00:00"))
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=UTC)
                uptime_seconds = (now_utc - started_dt).total_seconds()
            except (ValueError, TypeError):
                pass

        # Derive overall health status
        if orphaned_run_ids:
            overall_status = "degraded"
        elif total_active > 0 or total_queued > 0:
            overall_status = "healthy"
        else:
            overall_status = "idle"

        return {
            "status": overall_status,
            "active_runs": total_active,
            "queue_depth": total_queued,
            "started_at": engine_started_at,
            "uptime_seconds": uptime_seconds,
            "last_poll_at": last_poll_at,
            "global": {
                "max_parallel": max_global,
                "active_runs": total_active,
                "slots_available": max(0, max_global - total_active),
            },
            "projects": project_health,
            "pid_watchdog": {
                "status": pid_watchdog_status,
                "orphaned_run_ids": orphaned_run_ids,
                "orphan_threshold_seconds": _ORPHAN_THRESHOLD_SECONDS,
            },
        }

    except Exception as e:
        logger.error(f"Failed to get engine health: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# Engine queue — pending tasks by project with priority and blocked status
# ---------------------------------------------------------------------------


@router.get("/queue")
async def get_engine_queue():
    """Return queued (assigned) tasks grouped by project, ordered by priority.

    No auth required — internal service endpoint.
    """
    try:
        task_service = TaskService()
        project_service = ProjectService()

        ok, result = task_service.list_tasks(
            status="assigned",
            include_closed=False,
            include_archived=False,
            exclude_large_fields=True,
        )
        assigned_tasks = result.get("tasks", []) if ok else []

        # Build project name lookup
        project_names: dict[str, str] = {}
        ok_proj, proj_result = project_service.list_office_configs()
        if ok_proj:
            for proj in proj_result.get("projects", []):
                project_names[proj["id"]] = proj.get("title", "")

        # Group tasks by project
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        tasks_by_project: dict[str, list[dict[str, Any]]] = {}
        for task in assigned_tasks:
            pid = task.get("project_id", "unknown")
            if pid not in tasks_by_project:
                tasks_by_project[pid] = []
            tasks_by_project[pid].append(task)

        # Sort each project's queue by priority then task_order
        project_queues: list[dict[str, Any]] = []
        for pid, tasks in tasks_by_project.items():
            tasks.sort(key=lambda t: (
                priority_order.get(t.get("priority", "medium"), 2),
                -(t.get("task_order") or 0),
            ))

            task_entries = [
                {
                    "task_id": t["id"],
                    "title": t.get("title", ""),
                    "priority": t.get("priority", "medium"),
                    "status": t.get("status", "assigned"),
                    "is_blocked": bool(t.get("blocked_by")),
                    "blocked_by": t.get("blocked_by"),
                    "queued_since": t.get("updated_at") or t.get("created_at"),
                }
                for t in tasks
            ]

            project_queues.append({
                "project_id": pid,
                "project_name": project_names.get(pid, ""),
                "queue_depth": len(task_entries),
                "tasks": task_entries,
            })

        # Sort project list by queue depth descending
        project_queues.sort(key=lambda p: p["queue_depth"], reverse=True)

        return {
            "total_queued": len(assigned_tasks),
            "projects": project_queues,
        }

    except Exception as e:
        logger.error(f"Failed to get engine queue: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e
