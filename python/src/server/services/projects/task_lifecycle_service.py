"""
Task Lifecycle Service for LeanKit V3 Task Engine.

Manages the 14-state lifecycle for tasks:
    draft → proposed → approved → planning → owner-qa → assigned →
    executing → architect-review → review → done

With branching paths:
    failed → assigned (retry) | escalated
    escalated → assigned | on-hold | cancelled
    on-hold → approved (resume)
    done, cancelled → terminal (no transitions out)
"""

from datetime import datetime
from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# All valid lifecycle states
VALID_STATUSES = [
    "draft", "proposed", "approved", "planning", "owner-qa",
    "assigned", "executing", "architect-review", "review",
    "done", "failed", "escalated", "on-hold", "cancelled",
]

# Terminal states: no transitions allowed out of these
TERMINAL_STATES = {"done", "cancelled"}

# Transition rules: maps current_status → set of allowed next statuses
TRANSITION_RULES: dict[str, set[str]] = {
    "draft": {"proposed", "approved", "cancelled"},
    "proposed": {"approved", "cancelled"},
    "approved": {"planning", "assigned"},
    "planning": {"owner-qa", "assigned"},
    "owner-qa": {"assigned"},
    "assigned": {"executing"},
    "executing": {"architect-review", "failed"},
    "architect-review": {"review", "assigned", "escalated"},
    "review": {"done", "assigned"},
    "failed": {"assigned", "escalated"},
    "escalated": {"assigned", "on-hold", "cancelled"},
    "on-hold": {"approved"},
    # done and cancelled are terminal — no transitions out
}

# Transitions that require a reason field
REASON_REQUIRED_TRANSITIONS: set[tuple[str, str]] = {
    ("architect-review", "assigned"),  # retry with feedback
    ("review", "assigned"),            # owner reject with feedback
    ("executing", "failed"),           # failure reason
    ("failed", "escalated"),           # escalation reason
    ("architect-review", "escalated"), # escalation reason
    ("escalated", "on-hold"),          # hold reason
    ("escalated", "cancelled"),        # cancellation reason
    ("draft", "cancelled"),            # cancellation reason
    ("proposed", "cancelled"),         # cancellation reason
}


class TaskLifecycleService:
    """Manages task state transitions with validation and audit trail."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    def get_valid_next_states(self, current_status: str) -> list[str]:
        """Return the list of valid states this task can transition to."""
        if current_status in TERMINAL_STATES:
            return []
        return sorted(TRANSITION_RULES.get(current_status, set()))

    def validate_transition(
        self,
        current_status: str,
        new_status: str,
        reason: str | None = None,
    ) -> tuple[bool, str]:
        """
        Validate whether a state transition is allowed.

        Returns:
            Tuple of (is_valid, error_message). error_message is empty if valid.
        """
        if current_status not in VALID_STATUSES:
            return False, f"Invalid current status '{current_status}'"

        if new_status not in VALID_STATUSES:
            return False, f"Invalid target status '{new_status}'. Must be one of: {', '.join(VALID_STATUSES)}"

        if current_status in TERMINAL_STATES:
            return False, f"Cannot transition from terminal state '{current_status}'"

        allowed = TRANSITION_RULES.get(current_status, set())
        if new_status not in allowed:
            return (
                False,
                f"Invalid transition: '{current_status}' → '{new_status}'. "
                f"Allowed transitions: {', '.join(sorted(allowed)) if allowed else 'none'}",
            )

        # Check if reason is required
        if (current_status, new_status) in REASON_REQUIRED_TRANSITIONS and not reason:
            return (
                False,
                f"Transition '{current_status}' → '{new_status}' requires a reason",
            )

        return True, ""

    async def execute_transition(
        self,
        task_id: str,
        new_status: str,
        changed_by: str = "system",
        reason: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Execute a state transition: validate, update task, and record audit trail.

        Args:
            task_id: UUID of the task to transition
            new_status: Target lifecycle state
            changed_by: Actor performing the transition (e.g., "User", "Archon", "api")
            reason: Required for certain transitions (rejections, failures, etc.)

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            # Fetch current task
            response = (
                self.supabase_client.table("archon_tasks")
                .select("*")
                .eq("id", task_id)
                .execute()
            )

            if not response.data:
                return False, {"error": f"Task {task_id} not found"}

            task = response.data[0]
            current_status = task["status"]

            # Validate transition
            is_valid, error_msg = self.validate_transition(current_status, new_status, reason)
            if not is_valid:
                return False, {"error": error_msg}

            # Build state history entry
            history_entry = {
                "from_status": current_status,
                "to_status": new_status,
                "changed_by": changed_by,
                "changed_at": datetime.now().isoformat(),
            }
            if reason:
                history_entry["reason"] = reason

            # Append to existing state_history
            existing_history = task.get("state_history") or []
            if not isinstance(existing_history, list):
                existing_history = []
            updated_history = existing_history + [history_entry]

            # Build update payload
            now = datetime.now().isoformat()
            update_data: dict[str, Any] = {
                "status": new_status,
                "state_changed_at": now,
                "state_changed_by": changed_by,
                "state_history": updated_history,
                "updated_at": now,
            }

            # Handle specific transition side effects
            if new_status == "failed":
                update_data["rejection_reason"] = reason
            elif new_status == "on-hold":
                update_data["hold_reason"] = reason
            elif new_status == "cancelled":
                update_data["rejection_reason"] = reason
            elif new_status == "assigned" and current_status in ("architect-review", "review"):
                # Retry: increment retry_count and store feedback
                update_data["retry_count"] = (task.get("retry_count") or 0) + 1
                update_data["rejection_reason"] = reason
            elif new_status == "assigned" and current_status == "failed":
                # Retry after failure
                update_data["retry_count"] = (task.get("retry_count") or 0) + 1
            elif new_status == "escalated":
                update_data["rejection_reason"] = reason
            elif new_status == "approved" and current_status == "on-hold":
                # Resume from hold: clear hold reason
                update_data["hold_reason"] = None

            # Auto-classify on approved → planning/assigned based on complexity
            # (caller can override by specifying the target directly)

            # Execute update
            update_response = (
                self.supabase_client.table("archon_tasks")
                .update(update_data)
                .eq("id", task_id)
                .execute()
            )

            if not update_response.data:
                return False, {"error": f"Failed to update task {task_id}"}

            updated_task = update_response.data[0]

            logger.info(
                f"Task transition | task_id={task_id} | "
                f"{current_status} → {new_status} | by={changed_by}"
            )

            return True, {
                "task": updated_task,
                "transition": {
                    "from": current_status,
                    "to": new_status,
                    "changed_by": changed_by,
                    "reason": reason,
                },
            }

        except Exception as e:
            logger.error(f"Error executing transition for task {task_id}: {e}", exc_info=True)
            return False, {"error": f"Error executing transition: {str(e)}"}

    def get_state_history(self, task_id: str) -> tuple[bool, dict[str, Any]]:
        """
        Retrieve the full state transition history for a task.

        Returns:
            Tuple of (success, result_dict) with state_history array
        """
        try:
            response = (
                self.supabase_client.table("archon_tasks")
                .select("id, status, state_history, state_changed_at, state_changed_by, created_at")
                .eq("id", task_id)
                .execute()
            )

            if not response.data:
                return False, {"error": f"Task {task_id} not found"}

            task = response.data[0]
            history = task.get("state_history") or []

            return True, {
                "task_id": task["id"],
                "current_status": task["status"],
                "state_changed_at": task.get("state_changed_at"),
                "state_changed_by": task.get("state_changed_by"),
                "created_at": task["created_at"],
                "history": history,
                "transition_count": len(history),
            }

        except Exception as e:
            logger.error(f"Error fetching state history for task {task_id}: {e}", exc_info=True)
            return False, {"error": f"Error fetching state history: {str(e)}"}
