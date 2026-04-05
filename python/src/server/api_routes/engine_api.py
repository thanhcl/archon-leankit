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
from ..services.engine.analytics_service import EngineAnalyticsService
from ..services.engine.coordinator_service import CoordinatorService
from ..services.engine.health_monitor import HealthMonitor
from ..services.engine.runner_routing import get_runner_capabilities, get_runtime_default_runner_key
from ..services.engine.task_decomposer import TaskDecomposer
from ..services.projects import ProjectService, TaskService
from ..services.projects.execution_run_service import ExecutionRunService
from ..services.projects.task_lifecycle_service import TaskLifecycleService
from ..utils import get_supabase_client

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


def _coerce_config_object(value: Any) -> dict[str, Any]:
    """Normalize credential payloads stored as JSON strings or dicts."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


@router.get("/review-config")
async def get_review_config():
    """Get current review configuration."""
    try:
        stored = await credential_service.get_credential(_CONFIG_KEY)
        config = {**_DEFAULTS, **_coerce_config_object(stored)}
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
        current = {**_DEFAULTS, **_coerce_config_object(stored)}

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
        config = {**_CONCURRENCY_DEFAULTS, **_coerce_config_object(stored)}
        return config
    except Exception as e:
        logger.error(f"Failed to get concurrency config: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


@router.put("/concurrency-config")
async def update_concurrency_config(request: ConcurrencyConfigRequest):
    """Update engine concurrency limits (takes effect on next engine restart)."""
    try:
        stored = await credential_service.get_credential(_CONCURRENCY_CONFIG_KEY)
        current = {**_CONCURRENCY_DEFAULTS, **_coerce_config_object(stored)}

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
        config = _coerce_config_object(stored)
        if config:
            return config
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
        "default_runner": get_runtime_default_runner_key(),
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
        current: dict[str, Any] = _coerce_config_object(stored)

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
        concurrency_config.update(_coerce_config_object(stored))

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


def _run_last_activity_at(run: dict[str, Any]) -> datetime | None:
    """Return the freshest known activity timestamp for an execution run."""
    for field_name in ("heartbeat_at", "updated_at", "started_at"):
        raw_value = run.get(field_name)
        if not isinstance(raw_value, str) or not raw_value.strip():
            continue
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    return None


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
        concurrency_config.update(_coerce_config_object(stored_concurrency))

        stored_heartbeat = await credential_service.get_credential(_HEARTBEAT_CONFIG_KEY)
        heartbeat: dict[str, Any] = _coerce_config_object(stored_heartbeat)

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
                activity_at = _run_last_activity_at(run)
                if activity_at is None:
                    continue
                age_seconds = (now_utc - activity_at).total_seconds()
                if age_seconds > _ORPHAN_THRESHOLD_SECONDS:
                    orphaned_run_ids.append(run["id"])

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

        # Blocked task count (D-P2-01)
        ok_blocked, blocked_result = task_service.list_tasks(
            include_closed=False,
            include_archived=False,
            exclude_large_fields=True,
        )
        blocked_count = 0
        if ok_blocked:
            for t in blocked_result.get("tasks", []):
                if (t.get("blocked_by") or []) and t.get("status") not in {"done", "cancelled", "failed"}:
                    blocked_count += 1

        # Completed/failed today counts (D-P2-01)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        ok_completed, completed_result = run_service.list_runs(status="completed", limit=500)
        completed_today = 0
        failed_today = 0
        if ok_completed:
            for r in completed_result.get("runs", []):
                fin = r.get("finished_at") or ""
                if fin >= today_start:
                    completed_today += 1

        ok_failed_runs, failed_result = run_service.list_runs(status="failed", limit=500)
        if ok_failed_runs:
            for r in failed_result.get("runs", []):
                fin = r.get("finished_at") or ""
                if fin >= today_start:
                    failed_today += 1

        stalled_count = len(orphaned_run_ids)

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

        # Compute health_score (0.0–1.0) (D-P2-01)
        # Penalties: stalled runs, budget warnings, high blocked ratio
        health_score = 1.0
        if stalled_count > 0:
            health_score -= min(0.3, stalled_count * 0.1)
        if blocked_count > 5:
            health_score -= min(0.2, (blocked_count - 5) * 0.02)
        budget_warnings = sum(
            1 for p in project_health
            if p.get("budget", {}).get("status") in ("warning", "exceeded")
        )
        if budget_warnings > 0:
            health_score -= min(0.2, budget_warnings * 0.1)
        if failed_today > completed_today and completed_today > 0:
            health_score -= 0.1
        health_score = round(max(0.0, health_score), 2)

        return {
            "status": overall_status,
            "health_score": health_score,
            "active_runs": total_active,
            "queue_depth": total_queued,
            "blocked_tasks": blocked_count,
            "stalled_runs": stalled_count,
            "completed_today": completed_today,
            "failed_today": failed_today,
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
# Engine slots — lightweight capacity check (C-P5-02)
# ---------------------------------------------------------------------------


@router.get("/slots")
async def get_engine_slots():
    """Lightweight slot capacity view without full health computation.

    Returns per-project slot usage and global capacity.
    No auth required — internal service endpoint.
    """
    try:
        task_service = TaskService()
        project_service = ProjectService()

        ok, result = task_service.list_tasks(
            status="executing",
            include_closed=False,
            include_archived=False,
            exclude_large_fields=True,
        )
        executing_tasks = result.get("tasks", []) if ok else []

        per_project: dict[str, int] = {}
        for task in executing_tasks:
            pid = task.get("project_id", "unknown")
            per_project[pid] = per_project.get(pid, 0) + 1

        stored = await credential_service.get_credential(_CONCURRENCY_CONFIG_KEY)
        concurrency_config = {**_CONCURRENCY_DEFAULTS}
        concurrency_config.update(_coerce_config_object(stored))

        max_global = concurrency_config["max_parallel_global"]
        default_per_project = concurrency_config["max_parallel_default"]
        total_active = sum(per_project.values())

        ok_proj, proj_result = project_service.list_office_configs()
        project_slots = []
        if ok_proj:
            for proj in proj_result.get("projects", []):
                pid = proj["id"]
                settings = proj.get("office_settings") or {}
                try:
                    max_concurrent = int(settings.get("max_concurrent", default_per_project))
                except (TypeError, ValueError):
                    max_concurrent = default_per_project

                used = per_project.get(pid, 0)
                project_slots.append({
                    "project_id": pid,
                    "project_name": proj.get("title", ""),
                    "active": used,
                    "max": max_concurrent,
                    "available": max(0, max_concurrent - used),
                    "exhausted": used >= max_concurrent,
                })

        return {
            "global": {
                "total": max_global,
                "active": total_active,
                "available": max(0, max_global - total_active),
                "exhausted": total_active >= max_global,
            },
            "projects": project_slots,
        }

    except Exception as e:
        logger.error(f"Failed to get engine slots: {e}")
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


# ---------------------------------------------------------------------------
# Analytics endpoints (Batch 3: C-P6-03, C-P6-04, D-P2-03)
# ---------------------------------------------------------------------------


@router.get("/analytics/profiles")
async def get_profile_analytics(project_id: str | None = None, days: int = 30):
    """Per-profile success metrics with anomaly detection (C-P6-03).

    Returns success_rate, avg_cost, avg_retries per token profile.
    Flags profiles with consistently low success or over-provisioning.
    """
    try:
        service = EngineAnalyticsService()
        return service.get_profile_metrics(project_id=project_id, days=days)
    except Exception as e:
        logger.error(f"Failed to get profile analytics: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


@router.get("/analytics/models")
async def get_model_cost_analytics(project_id: str | None = None, days: int = 30):
    """Model routing cost breakdown with savings recommendations (C-P6-04).

    Returns cost per model × stage with suggestions to downgrade where appropriate.
    """
    try:
        service = EngineAnalyticsService()
        return service.get_model_cost_breakdown(project_id=project_id, days=days)
    except Exception as e:
        logger.error(f"Failed to get model cost analytics: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


@router.get("/analytics/cost")
async def get_cost_trending(project_id: str | None = None, days: int = 30):
    """Cost attribution and trending with anomaly detection (D-P2-03).

    Returns daily cost time series and flags runs with cost > 3x profile average.
    """
    try:
        service = EngineAnalyticsService()
        return service.get_cost_trending(project_id=project_id, days=days)
    except Exception as e:
        logger.error(f"Failed to get cost trending: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# Coordinator endpoints (C-P7)
# ---------------------------------------------------------------------------


class CoordinatorDecomposeRequest(BaseModel):
    task_id: str
    children: list[dict[str, Any]] | None = None  # explicit specs, or auto-decompose if None


@router.post("/coordinator/decompose")
async def decompose_task(request: CoordinatorDecomposeRequest):
    """Decompose a task into coordinator children.

    If children specs are provided, creates them directly.
    If not, uses TaskDecomposer to auto-generate specs from the task.
    """
    try:
        task_service = TaskService()
        lifecycle_service = TaskLifecycleService()

        ok, result = task_service.get_task(request.task_id)
        if not ok:
            raise HTTPException(status_code=404, detail={"error": f"Task {request.task_id} not found"})

        task = result["task"]

        if request.children:
            specs = request.children
        else:
            decomposer = TaskDecomposer()
            should, reason = decomposer.should_decompose(task)
            if not should:
                return {"decomposed": False, "reason": reason, "children": []}
            specs = decomposer.build_decomposition_specs(task)
            if not specs:
                return {"decomposed": False, "reason": "no decomposition specs generated", "children": []}

        coordinator = CoordinatorService(task_service, lifecycle_service)
        children = await coordinator.create_coordinator_children(
            parent_task_id=request.task_id,
            children_specs=specs,
        )

        return {
            "decomposed": True,
            "parent_task_id": request.task_id,
            "children_count": len(children),
            "children": [{"id": c.get("id"), "title": c.get("title"), "status": c.get("status")} for c in children],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to decompose task: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


@router.get("/coordinator/{task_id}/status")
async def get_coordinator_status(task_id: str):
    """Get coordinator task status including children summary."""
    try:
        task_service = TaskService()
        coordinator = CoordinatorService(task_service)

        ok, result = task_service.get_task(task_id)
        if not ok:
            raise HTTPException(status_code=404, detail={"error": f"Task {task_id} not found"})

        task = result["task"]
        if task.get("decomposition_mode") != "coordinator":
            return {
                "is_coordinator": False,
                "task_id": task_id,
                "decomposition_mode": task.get("decomposition_mode", "none"),
            }

        children = coordinator.get_coordinator_children(task_id)
        should_advance, details = await coordinator.evaluate_parent_status(task_id)

        return {
            "is_coordinator": True,
            "task_id": task_id,
            "task_status": task.get("status"),
            "children_count": len(children),
            "children": [
                {"id": c.get("id"), "title": c.get("title"), "status": c.get("status"), "priority": c.get("priority")}
                for c in children
            ],
            "should_advance": should_advance,
            "advancement_details": details,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get coordinator status: {e}")
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ── Metrics Aggregation (N2-0) ───────────────────────────────────────────


@router.get("/metrics")
async def get_engine_metrics():
    """Aggregated engine metrics across all projects.

    Returns per-project cost/quality breakdowns, global totals, and
    recent health alerts.  Alert data is transient (in-memory, reset on
    engine restart).
    """
    try:
        project_service = ProjectService()
        task_service = TaskService()
        budget_service = CostBudgetService()

        # List all projects
        ok, proj_result = project_service.list_office_configs()
        projects = proj_result.get("projects", []) if ok else []

        # Get all done tasks
        ok, task_result = task_service.list_tasks(status="done")
        all_done_tasks = task_result.get("tasks", []) if ok else []

        # Aggregate total costs from execution_runs
        supabase = get_supabase_client()
        cost_rows = (
            supabase.table("archon_execution_runs")
            .select("project_id, cost_usd")
            .not_.is_("cost_usd", "null")
            .execute()
        ).data or []

        # Group costs by project
        total_costs_by_project: dict[str, float] = {}
        for row in cost_rows:
            pid = row.get("project_id", "")
            cost = row.get("cost_usd", 0) or 0
            total_costs_by_project[pid] = total_costs_by_project.get(pid, 0) + cost

        # Build per-project metrics
        project_metrics = []
        global_today = 0.0
        global_week = 0.0
        global_done = 0
        global_first_pass = 0
        global_retries_sum = 0

        for project in projects:
            pid = project.get("id", "")
            pname = project.get("title", "Unknown")

            # Cost from budget service
            today_cost = 0.0
            week_cost = 0.0
            try:
                ok_budget, budget_data = budget_service.get_cost_status(pid)
                if ok_budget:
                    today_cost = (budget_data.get("today") or {}).get("cost_usd", 0.0)
                    week_cost = (budget_data.get("weekly") or {}).get("total_cost_usd", 0.0)
            except Exception:
                pass

            total_cost = total_costs_by_project.get(pid, 0.0)

            # Quality from done tasks
            proj_tasks = [t for t in all_done_tasks if t.get("project_id") == pid]
            done_count = len(proj_tasks)
            first_pass = sum(1 for t in proj_tasks if (t.get("retry_count") or 0) == 0)
            retries_sum = sum(t.get("retry_count", 0) for t in proj_tasks)

            first_pass_rate = (first_pass / done_count) if done_count > 0 else 1.0
            avg_retries = (retries_sum / done_count) if done_count > 0 else 0.0

            project_metrics.append({
                "project_id": pid,
                "project_name": pname,
                "cost": {
                    "today_usd": round(today_cost, 2),
                    "week_usd": round(week_cost, 2),
                    "total_usd": round(total_cost, 2),
                },
                "quality": {
                    "total_done": done_count,
                    "first_pass_count": first_pass,
                    "first_pass_rate": round(first_pass_rate, 3),
                    "avg_retries": round(avg_retries, 2),
                },
            })

            global_today += today_cost
            global_week += week_cost
            global_done += done_count
            global_first_pass += first_pass
            global_retries_sum += retries_sum

        # Global totals
        global_fpr = (global_first_pass / global_done) if global_done > 0 else 1.0
        global_avg_retries = (global_retries_sum / global_done) if global_done > 0 else 0.0

        # Health alerts (transient, in-memory)
        alerts = HealthMonitor.snapshot_alerts(max_alerts=10)
        # Sort descending by timestamp, cap at 10
        alerts.sort(key=lambda a: a.get("timestamp", ""), reverse=True)
        alerts = alerts[:10]

        return {
            "projects": project_metrics,
            "totals": {
                "cost": {
                    "today_usd": round(global_today, 2),
                    "week_usd": round(global_week, 2),
                },
                "quality": {
                    "total_done": global_done,
                    "first_pass_count": global_first_pass,
                    "first_pass_rate": round(global_fpr, 3),
                    "avg_retries": round(global_avg_retries, 2),
                },
            },
            "recent_alerts": alerts,
        }

    except Exception as e:
        logger.error(f"Failed to get engine metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e
