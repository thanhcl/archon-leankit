"""
Agent Runtime Service for Archon.

Manages agent runtime registration, heartbeat, task claiming, and lifecycle.
Adopted from Multica daemon self-registration pattern (Workstream M-P1, M-P2).
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from src.server.utils import get_supabase_client

from ..config.logfire_config import get_logger

logger = get_logger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 30
DEGRADED_AFTER_MISSED = 2
OFFLINE_AFTER_MISSED = 3

VALID_STATUSES = ("online", "offline", "degraded")
VALID_ASSIGNEE_TYPES = ("agent", "human", "unassigned")


class AgentRuntimeService:
    """Service for agent runtime registration, heartbeat, and task claiming."""

    TABLE = "archon_agent_runtimes"

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    # ── Registration ──────────────────────────────────────────────────────

    async def register_runtime(
        self,
        device_name: str,
        capabilities: list[str],
        supported_runners: list[str],
        max_concurrent_tasks: int = 1,
        agent_id: str | None = None,
        version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Register a new agent runtime. Returns (success, runtime_record_or_error)."""
        try:
            if not device_name:
                return False, {"error": "device_name is required"}
            if not capabilities:
                return False, {"error": "capabilities list cannot be empty"}
            if not supported_runners:
                return False, {"error": "supported_runners list cannot be empty"}
            if max_concurrent_tasks < 1:
                return False, {"error": "max_concurrent_tasks must be >= 1"}

            now = datetime.now(timezone.utc).isoformat()
            runtime_id = str(uuid4())

            record = {
                "id": runtime_id,
                "device_name": device_name,
                "capabilities": capabilities,
                "supported_runners": supported_runners,
                "max_concurrent_tasks": max_concurrent_tasks,
                "current_task_count": 0,
                "status": "online",
                "last_heartbeat": now,
                "registered_at": now,
                "metadata": metadata or {},
            }
            if agent_id:
                record["agent_id"] = agent_id
            if version:
                record["version"] = version

            result = (
                self.supabase_client.table(self.TABLE)
                .insert(record)
                .execute()
            )

            if not result.data:
                return False, {"error": "Failed to insert runtime record"}

            logger.info(f"Runtime registered: runtime_id={runtime_id} device={device_name} runners={supported_runners}")
            return True, {"runtime": result.data[0]}

        except Exception as e:
            logger.error(f"Runtime registration failed: {e}", exc_info=True)
            return False, {"error": str(e)}

    async def unregister_runtime(self, runtime_id: str) -> tuple[bool, dict[str, Any]]:
        """Gracefully unregister a runtime."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            result = (
                self.supabase_client.table(self.TABLE)
                .update({
                    "status": "offline",
                    "unregistered_at": now,
                    "updated_at": now,
                })
                .eq("id", runtime_id)
                .execute()
            )

            if not result.data:
                return False, {"error": f"Runtime {runtime_id} not found"}

            logger.info(f"Runtime unregistered: runtime_id={runtime_id}")
            return True, {"runtime": result.data[0]}

        except Exception as e:
            logger.error(f"Runtime unregistration failed: {e}", exc_info=True)
            return False, {"error": str(e)}

    # ── Heartbeat ─────────────────────────────────────────────────────────

    async def heartbeat(
        self,
        runtime_id: str,
        current_task_count: int | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Record a heartbeat from a runtime. Returns updated runtime."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            update: dict[str, Any] = {
                "last_heartbeat": now,
                "status": "online",
                "updated_at": now,
            }
            if current_task_count is not None:
                update["current_task_count"] = current_task_count

            result = (
                self.supabase_client.table(self.TABLE)
                .update(update)
                .eq("id", runtime_id)
                .is_("unregistered_at", "null")
                .execute()
            )

            if not result.data:
                return False, {"error": f"Runtime {runtime_id} not found or already unregistered"}

            return True, {"runtime": result.data[0]}

        except Exception as e:
            logger.error(f"Heartbeat failed for runtime {runtime_id}: {e}", exc_info=True)
            return False, {"error": str(e)}

    async def check_stale_runtimes(self) -> tuple[bool, dict[str, Any]]:
        """Mark runtimes as degraded/offline based on missed heartbeats.

        Called periodically by the engine or a background task.
        """
        try:
            now = datetime.now(timezone.utc)
            degraded_threshold = now - timedelta(
                seconds=HEARTBEAT_INTERVAL_SECONDS * DEGRADED_AFTER_MISSED
            )
            offline_threshold = now - timedelta(
                seconds=HEARTBEAT_INTERVAL_SECONDS * OFFLINE_AFTER_MISSED
            )

            # Mark offline: missed 3+ heartbeats
            offline_result = (
                self.supabase_client.table(self.TABLE)
                .update({
                    "status": "offline",
                    "updated_at": now.isoformat(),
                })
                .eq("status", "online")
                .lt("last_heartbeat", offline_threshold.isoformat())
                .is_("unregistered_at", "null")
                .execute()
            )

            # Also mark degraded ones offline
            offline_degraded = (
                self.supabase_client.table(self.TABLE)
                .update({
                    "status": "offline",
                    "updated_at": now.isoformat(),
                })
                .eq("status", "degraded")
                .lt("last_heartbeat", offline_threshold.isoformat())
                .is_("unregistered_at", "null")
                .execute()
            )

            # Mark degraded: missed 2 heartbeats but not yet offline
            degraded_result = (
                self.supabase_client.table(self.TABLE)
                .update({
                    "status": "degraded",
                    "updated_at": now.isoformat(),
                })
                .eq("status", "online")
                .lt("last_heartbeat", degraded_threshold.isoformat())
                .gte("last_heartbeat", offline_threshold.isoformat())
                .is_("unregistered_at", "null")
                .execute()
            )

            offline_count = len(offline_result.data or []) + len(offline_degraded.data or [])
            degraded_count = len(degraded_result.data or [])

            if offline_count or degraded_count:
                logger.info(f"Stale runtime check: newly_offline={offline_count} newly_degraded={degraded_count}")

            return True, {
                "newly_offline": offline_count,
                "newly_degraded": degraded_count,
            }

        except Exception as e:
            logger.error(f"Stale runtime check failed: {e}", exc_info=True)
            return False, {"error": str(e)}

    # ── Query ─────────────────────────────────────────────────────────────

    def list_runtimes(
        self,
        status: str | None = None,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> tuple[bool, dict[str, Any]]:
        """List registered runtimes with optional filters."""
        try:
            query = (
                self.supabase_client.table(self.TABLE)
                .select("*")
                .order("registered_at", desc=True)
                .limit(limit)
            )
            if status:
                if status not in VALID_STATUSES:
                    return False, {"error": f"Invalid status '{status}'. Must be one of: {', '.join(VALID_STATUSES)}"}
                query = query.eq("status", status)
            if agent_id:
                query = query.eq("agent_id", agent_id)

            result = query.execute()
            return True, {
                "runtimes": result.data or [],
                "total_count": len(result.data or []),
                "filters_applied": f"status={status}" if status else "none",
            }

        except Exception as e:
            logger.error(f"List runtimes failed: {e}", exc_info=True)
            return False, {"error": str(e)}

    def get_runtime(self, runtime_id: str) -> tuple[bool, dict[str, Any]]:
        """Get a single runtime by ID."""
        try:
            result = (
                self.supabase_client.table(self.TABLE)
                .select("*")
                .eq("id", runtime_id)
                .execute()
            )
            if not result.data:
                return False, {"error": f"Runtime {runtime_id} not found"}
            return True, {"runtime": result.data[0]}

        except Exception as e:
            logger.error(f"Get runtime failed: runtime_id={runtime_id} error={e}", exc_info=True)
            return False, {"error": str(e)}

    def get_online_runtimes_for_runner(self, runner_key: str) -> list[dict[str, Any]]:
        """Get all online runtimes that support a specific runner key."""
        try:
            result = (
                self.supabase_client.table(self.TABLE)
                .select("*")
                .eq("status", "online")
                .is_("unregistered_at", "null")
                .contains("supported_runners", [runner_key])
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Get runtimes for runner failed: runner_key={runner_key} error={e}", exc_info=True)
            return []

    # ── Task Claim ────────────────────────────────────────────────────────

    async def claim_task(
        self,
        runtime_id: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Attempt to claim the next available task for this runtime.

        Atomic claim: updates task.runtime_id and task.status in one operation
        to prevent double-claim.
        """
        try:
            # Get runtime info
            ok, runtime_data = self.get_runtime(runtime_id)
            if not ok:
                return False, runtime_data

            runtime = runtime_data["runtime"]
            if runtime["status"] != "online":
                return False, {"error": "Runtime is not online"}
            if runtime["current_task_count"] >= runtime["max_concurrent_tasks"]:
                return False, {"error": "Runtime at max capacity"}

            supported_runners = runtime.get("supported_runners", [])
            if not supported_runners:
                return False, {"error": "Runtime has no supported runners"}

            # Find claimable tasks: status='assigned', no runtime_id, matching runner
            tasks_result = (
                self.supabase_client.table("archon_tasks")
                .select("*")
                .eq("status", "assigned")
                .is_("runtime_id", "null")
                .order("priority", desc=True)
                .limit(10)
                .execute()
            )

            if not tasks_result.data:
                return True, {"task": None, "message": "No tasks available"}

            # Try to claim the first compatible task
            now = datetime.now(timezone.utc).isoformat()
            for task in tasks_result.data:
                # Atomic claim: set runtime_id (only if still null)
                claim_result = (
                    self.supabase_client.table("archon_tasks")
                    .update({
                        "runtime_id": runtime_id,
                        "updated_at": now,
                    })
                    .eq("id", task["id"])
                    .eq("status", "assigned")
                    .is_("runtime_id", "null")
                    .execute()
                )

                if claim_result.data:
                    # Increment runtime task count
                    self.supabase_client.table(self.TABLE).update({
                        "current_task_count": runtime["current_task_count"] + 1,
                        "updated_at": now,
                    }).eq("id", runtime_id).execute()

                    logger.info(f"Task claimed: task_id={task['id']} runtime_id={runtime_id}")
                    return True, {"task": claim_result.data[0]}

            return True, {"task": None, "message": "No compatible tasks or all claimed"}

        except Exception as e:
            logger.error(f"Task claim failed: runtime_id={runtime_id} error={e}", exc_info=True)
            return False, {"error": str(e)}

    async def release_task(
        self,
        runtime_id: str,
        task_id: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Release a claimed task (on completion or failure). Decrements task count."""
        try:
            now = datetime.now(timezone.utc).isoformat()

            # Clear runtime_id on the task
            self.supabase_client.table("archon_tasks").update({
                "runtime_id": None,
                "updated_at": now,
            }).eq("id", task_id).eq("runtime_id", runtime_id).execute()

            # Decrement runtime task count
            ok, runtime_data = self.get_runtime(runtime_id)
            if ok:
                runtime = runtime_data["runtime"]
                new_count = max(0, runtime.get("current_task_count", 1) - 1)
                self.supabase_client.table(self.TABLE).update({
                    "current_task_count": new_count,
                    "updated_at": now,
                }).eq("id", runtime_id).execute()

            logger.info(f"Task released: task_id={task_id} runtime_id={runtime_id}")
            return True, {"released": True}

        except Exception as e:
            logger.error(f"Task release failed: task_id={task_id} error={e}", exc_info=True)
            return False, {"error": str(e)}

    # ── Progress ──────────────────────────────────────────────────────────

    async def report_progress(
        self,
        task_id: str,
        runtime_id: str,
        step_count: int | None = None,
        percentage: float | None = None,
        current_action: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Report execution progress for a task."""
        try:
            now = datetime.now(timezone.utc).isoformat()

            # Update task metadata with progress info
            progress_data: dict[str, Any] = {"updated_at": now}

            # Store progress in metadata
            task_result = (
                self.supabase_client.table("archon_tasks")
                .select("metadata")
                .eq("id", task_id)
                .eq("runtime_id", runtime_id)
                .execute()
            )

            if not task_result.data:
                return False, {"error": "Task not found or not claimed by this runtime"}

            metadata = task_result.data[0].get("metadata") or {}
            metadata["progress"] = {
                "step_count": step_count,
                "percentage": percentage,
                "current_action": current_action,
                "reported_at": now,
            }

            self.supabase_client.table("archon_tasks").update({
                "metadata": metadata,
                "updated_at": now,
            }).eq("id", task_id).execute()

            return True, {"progress_reported": True}

        except Exception as e:
            logger.error(f"Progress report failed: task_id={task_id} error={e}", exc_info=True)
            return False, {"error": str(e)}
