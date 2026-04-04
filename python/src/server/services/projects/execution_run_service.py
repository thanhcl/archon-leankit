"""
Execution Run Service for Archon.

Stores and retrieves execution run records as first-class runtime history.
"""

from datetime import datetime
from typing import Any

from src.server.models.api_contracts import ExecutionRunStage, ExecutionRunStatus
from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger

logger = get_logger(__name__)


class ExecutionRunService:
    """Service class for execution run operations."""

    VALID_STATUSES = [s.value for s in ExecutionRunStatus]
    VALID_STAGES = [s.value for s in ExecutionRunStage]

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    @staticmethod
    def _is_missing_heartbeat_column_error(error: Exception | str) -> bool:
        message = str(error)
        return "heartbeat_at" in message and "archon_execution_runs" in message

    @staticmethod
    def _normalize_run_record(run: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(run)
        if normalized.get("heartbeat_at") is None:
            metadata = normalized.get("metadata")
            if isinstance(metadata, dict):
                fallback = metadata.get("heartbeat_at")
                if isinstance(fallback, str) and fallback.strip():
                    normalized["heartbeat_at"] = fallback
        return normalized

    def _merge_metadata_heartbeat(
        self,
        metadata: dict[str, Any] | None,
        heartbeat_at: str | None,
    ) -> dict[str, Any]:
        merged = dict(metadata or {})
        if heartbeat_at:
            merged["heartbeat_at"] = heartbeat_at
        return merged

    def validate_status(self, status: str) -> tuple[bool, str]:
        if status not in self.VALID_STATUSES:
            return (
                False,
                f"Invalid execution run status '{status}'. Must be one of: {', '.join(self.VALID_STATUSES)}",
            )
        return True, ""

    def validate_stage(self, stage: str) -> tuple[bool, str]:
        if stage not in self.VALID_STAGES:
            return (
                False,
                f"Invalid execution run stage '{stage}'. Must be one of: {', '.join(self.VALID_STAGES)}",
            )
        return True, ""

    async def create_run(
        self,
        task_id: str,
        project_id: str,
        status: str = "queued",
        stage: str = "execute",
        engine_id: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        retry_index: int = 0,
        started_at: str | None = None,
        finished_at: str | None = None,
        heartbeat_at: str | None = None,
        duration_seconds: float | None = None,
        token_input: int | None = None,
        token_output: int | None = None,
        total_tokens: int | None = None,
        thinking_tokens: int | None = None,
        cost_usd: float | None = None,
        result_summary: str | None = None,
        error_summary: str | None = None,
        workspace_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Create a new execution run."""
        try:
            if not task_id:
                return False, {"error": "Task ID is required"}
            if not project_id:
                return False, {"error": "Project ID is required"}

            is_valid, error_msg = self.validate_status(status)
            if not is_valid:
                return False, {"error": error_msg}

            is_valid, error_msg = self.validate_stage(stage)
            if not is_valid:
                return False, {"error": error_msg}

            now = datetime.now().isoformat()
            run_data: dict[str, Any] = {
                "task_id": task_id,
                "project_id": project_id,
                "status": status,
                "stage": stage,
                "retry_index": retry_index,
                "started_at": started_at or now,
                "heartbeat_at": heartbeat_at or started_at or now,
                "metadata": metadata or {},
                "created_at": now,
                "updated_at": now,
            }

            optional_fields = {
                "engine_id": engine_id,
                "session_id": session_id,
                "model": model,
                "finished_at": finished_at,
                "duration_seconds": duration_seconds,
                "token_input": token_input,
                "token_output": token_output,
                "total_tokens": total_tokens,
                "thinking_tokens": thinking_tokens,
                "cost_usd": cost_usd,
                "result_summary": result_summary,
                "error_summary": error_summary,
                "workspace_path": workspace_path,
            }
            for key, value in optional_fields.items():
                if value is not None:
                    run_data[key] = value

            try:
                response = self.supabase_client.table("archon_execution_runs").insert(run_data).execute()
            except Exception as e:
                if not self._is_missing_heartbeat_column_error(e):
                    raise
                fallback_data = dict(run_data)
                heartbeat_value = fallback_data.pop("heartbeat_at", None)
                fallback_data["metadata"] = self._merge_metadata_heartbeat(
                    fallback_data.get("metadata"),
                    heartbeat_value,
                )
                response = self.supabase_client.table("archon_execution_runs").insert(fallback_data).execute()

            if response.data:
                return True, {"run": self._normalize_run_record(response.data[0])}

            return False, {"error": "Failed to create execution run"}
        except Exception as e:
            logger.error(f"Error creating execution run: {e}", exc_info=True)
            return False, {"error": str(e)}

    def get_run(self, run_id: str) -> tuple[bool, dict[str, Any]]:
        """Get a single execution run by ID."""
        try:
            response = (
                self.supabase_client.table("archon_execution_runs")
                .select("*")
                .eq("id", run_id)
                .execute()
            )
            if response.data:
                return True, {"run": self._normalize_run_record(response.data[0])}
            return False, {"error": f"Execution run {run_id} not found"}
        except Exception as e:
            logger.error(f"Error fetching execution run {run_id}: {e}", exc_info=True)
            return False, {"error": str(e)}

    def list_runs(
        self,
        task_id: str | None = None,
        project_id: str | None = None,
        bootstrap_plan_id: str | None = None,
        status: str | None = None,
        stage: str | None = None,
        limit: int = 50,
    ) -> tuple[bool, dict[str, Any]]:
        """List execution runs with optional filtering."""
        try:
            query = self.supabase_client.table("archon_execution_runs").select("*")
            filters_applied: list[str] = []

            if task_id:
                query = query.eq("task_id", task_id)
                filters_applied.append(f"task_id={task_id}")
            if project_id:
                query = query.eq("project_id", project_id)
                filters_applied.append(f"project_id={project_id}")
            if bootstrap_plan_id:
                filters_applied.append(f"bootstrap_plan_id={bootstrap_plan_id}")
            if status:
                is_valid, error_msg = self.validate_status(status)
                if not is_valid:
                    return False, {"error": error_msg}
                query = query.eq("status", status)
                filters_applied.append(f"status={status}")
            if stage:
                is_valid, error_msg = self.validate_stage(stage)
                if not is_valid:
                    return False, {"error": error_msg}
                query = query.eq("stage", stage)
                filters_applied.append(f"stage={stage}")

            response = query.order("started_at", desc=True).limit(limit).execute()
            runs = [self._normalize_run_record(run) for run in (response.data or [])]
            if bootstrap_plan_id:
                runs = [run for run in runs if self._matches_bootstrap_plan_id(run, bootstrap_plan_id)]
            return True, {
                "runs": runs,
                "total_count": len(runs),
                "filters_applied": ", ".join(filters_applied) if filters_applied else "none",
            }
        except Exception as e:
            logger.error(f"Error listing execution runs: {e}", exc_info=True)
            return False, {"error": str(e)}

    @staticmethod
    def _matches_bootstrap_plan_id(run: dict[str, Any], bootstrap_plan_id: str) -> bool:
        """Check whether a run belongs to a bootstrap plan using normalized metadata."""
        direct = run.get("bootstrap_plan_id") or run.get("bootstrapPlanId")
        if isinstance(direct, str) and direct == bootstrap_plan_id:
            return True

        metadata = run.get("metadata")
        if isinstance(metadata, dict):
            nested = metadata.get("bootstrap_plan_id") or metadata.get("bootstrapPlanId")
            if isinstance(nested, str) and nested == bootstrap_plan_id:
                return True

        return False

    async def update_run(self, run_id: str, update_fields: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Update an execution run."""
        try:
            if not update_fields:
                return False, {"error": "No update fields provided"}

            if "status" in update_fields:
                is_valid, error_msg = self.validate_status(str(update_fields["status"]))
                if not is_valid:
                    return False, {"error": error_msg}

            if "stage" in update_fields:
                is_valid, error_msg = self.validate_stage(str(update_fields["stage"]))
                if not is_valid:
                    return False, {"error": error_msg}

            update_payload = dict(update_fields)
            update_payload["updated_at"] = datetime.now().isoformat()

            try:
                response = (
                    self.supabase_client.table("archon_execution_runs")
                    .update(update_payload)
                    .eq("id", run_id)
                    .execute()
                )
            except Exception as e:
                if not self._is_missing_heartbeat_column_error(e):
                    raise
                heartbeat_value = update_payload.pop("heartbeat_at", None)
                ok_existing, existing = self.get_run(run_id)
                existing_metadata = {}
                if ok_existing:
                    current_run = existing.get("run") or {}
                    metadata = current_run.get("metadata")
                    if isinstance(metadata, dict):
                        existing_metadata = metadata
                update_payload["metadata"] = self._merge_metadata_heartbeat(existing_metadata, heartbeat_value)
                response = (
                    self.supabase_client.table("archon_execution_runs")
                    .update(update_payload)
                    .eq("id", run_id)
                    .execute()
                )

            if response.data:
                return True, {"run": self._normalize_run_record(response.data[0])}
            return False, {"error": f"Execution run {run_id} not found"}
        except Exception as e:
            logger.error(f"Error updating execution run {run_id}: {e}", exc_info=True)
            return False, {"error": str(e)}
