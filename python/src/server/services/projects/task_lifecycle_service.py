"""
Task Lifecycle Service for LeanKit V3 Task Engine.

Manages the 15-state lifecycle for tasks:
    draft → proposed → approved → planning → owner-qa → assigned →
    executing → architect-review → code-review → review → done

With branching paths:
    failed → assigned (retry) | escalated | on-hold
    escalated → assigned | on-hold | cancelled
    on-hold → approved | assigned (resume)
    done, cancelled → terminal (no transitions out)
    on-hold reachable from: approved, assigned, executing, architect-review, code-review, review, failed, escalated
"""

from datetime import datetime
from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# All valid lifecycle states
VALID_STATUSES = [
    "draft", "proposed", "approved", "planning", "owner-qa",
    "assigned", "executing", "architect-review", "code-review",
    "review", "done", "failed", "escalated", "on-hold", "cancelled",
]

# Terminal states: no transitions allowed out of these
TERMINAL_STATES = {"done", "cancelled"}

# Transition rules: maps current_status → set of allowed next statuses
TRANSITION_RULES: dict[str, set[str]] = {
    "draft": {"proposed", "approved", "cancelled"},
    "proposed": {"approved", "cancelled"},
    "approved": {"planning", "assigned", "on-hold", "cancelled"},
    "planning": {"owner-qa", "assigned", "cancelled"},
    "owner-qa": {"assigned", "cancelled"},
    "assigned": {"executing", "on-hold", "cancelled"},
    "executing": {"architect-review", "failed", "on-hold", "cancelled"},
    "architect-review": {"code-review", "assigned", "escalated", "on-hold", "cancelled"},
    "code-review": {"review", "assigned", "escalated", "on-hold", "cancelled"},
    "review": {"done", "assigned", "on-hold", "cancelled"},
    "failed": {"assigned", "escalated", "on-hold", "cancelled"},
    "escalated": {"assigned", "on-hold", "cancelled"},
    "on-hold": {"approved", "assigned", "cancelled"},
    # done and cancelled are terminal — no transitions out
}

# Transitions that require a reason field
REASON_REQUIRED_TRANSITIONS: set[tuple[str, str]] = {
    ("architect-review", "assigned"),  # retry with feedback
    ("code-review", "assigned"),       # code review requested changes
    ("review", "assigned"),            # owner reject with feedback
    ("executing", "failed"),           # failure reason
    ("failed", "escalated"),           # escalation reason
    ("architect-review", "escalated"), # escalation reason
    ("code-review", "escalated"),      # code review escalation
    ("approved", "on-hold"),            # hold reason
    ("assigned", "on-hold"),            # hold reason
    ("executing", "on-hold"),           # hold reason
    ("architect-review", "on-hold"),    # hold reason
    ("code-review", "on-hold"),         # hold reason
    ("review", "on-hold"),             # hold reason
    ("failed", "on-hold"),             # hold reason
    ("escalated", "on-hold"),          # hold reason
    # Cancellation always requires a reason
    ("draft", "cancelled"),
    ("proposed", "cancelled"),
    ("approved", "cancelled"),
    ("planning", "cancelled"),
    ("owner-qa", "cancelled"),
    ("assigned", "cancelled"),
    ("executing", "cancelled"),
    ("architect-review", "cancelled"),
    ("code-review", "cancelled"),
    ("review", "cancelled"),
    ("failed", "cancelled"),
    ("escalated", "cancelled"),
    ("on-hold", "cancelled"),
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

            # For contract-managed tasks: enforce locked contract before approved → assigned.
            # Tasks without current_contract_id are not contract-managed and pass through freely.
            if current_status == "approved" and new_status == "assigned":
                contract_id = task.get("current_contract_id")
                if contract_id:
                    from .task_service import TaskService
                    ts = TaskService(self.supabase_client)
                    ok_c, contract_res = ts.get_contract(contract_id)
                    if not ok_c:
                        return False, {
                            "error": "Current contract not found. Lock the contract before assigning.",
                            "contract_gate": True,
                            "contract_id": contract_id,
                            "contract_status": "missing",
                            "blocked_transition": "approved → assigned",
                        }
                    if not contract_res["contract"].get("locked_at"):
                        return False, {
                            "error": (
                                "Contract must be locked before a contract-managed task can be assigned "
                                "(approved → assigned)."
                            ),
                            "contract_gate": True,
                            "contract_id": contract_id,
                            "contract_status": "unlocked",
                            "blocked_transition": "approved → assigned",
                        }

            # For contract-managed tasks: enforce locked contract before leaving proposed.
            # Tasks without current_contract_id are not contract-managed and pass through freely.
            if current_status == "proposed" and new_status in ("approved", "assigned", "executing"):
                contract_id = task.get("current_contract_id")
                if contract_id:
                    from .task_service import TaskService
                    ts = TaskService(self.supabase_client)
                    ok_c, contract_res = ts.get_contract(contract_id)
                    if not ok_c:
                        return False, {
                            "error": "Current contract not found. Lock the contract before proceeding.",
                            "contract_gate": True,
                            "contract_id": contract_id,
                            "contract_status": "missing",
                            "blocked_transition": f"proposed → {new_status}",
                        }
                    if not contract_res["contract"].get("locked_at"):
                        return False, {
                            "error": (
                                "Contract must be locked before a contract-managed task can leave "
                                f"proposed state (proposed → {new_status})."
                            ),
                            "contract_gate": True,
                            "contract_id": contract_id,
                            "contract_status": "unlocked",
                            "blocked_transition": f"proposed → {new_status}",
                        }

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
            elif new_status == "assigned" and current_status in ("architect-review", "code-review", "review"):
                # Retry: increment retry_count and store feedback
                update_data["retry_count"] = (task.get("retry_count") or 0) + 1
                update_data["rejection_reason"] = reason
                if current_status == "code-review":
                    update_data["review_cycle"] = (task.get("review_cycle") or 0) + 1
            elif new_status == "assigned" and current_status == "failed":
                # Retry after failure
                update_data["retry_count"] = (task.get("retry_count") or 0) + 1
            elif new_status == "escalated":
                update_data["rejection_reason"] = reason
            elif current_status == "on-hold" and new_status in ("approved", "assigned"):
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

    async def re_plan(
        self,
        task_id: str,
        updated_description: str | None = None,
        changed_by: str = "system",
        reason: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Reset a task to 'planning' state, preserving its ID, plan_item_id, and history.

        Intended for operators who need to re-plan a failed/stalled task without
        losing lineage. The task description may be updated with new context.

        Args:
            task_id: UUID of the task to re-plan
            updated_description: Optional new description to replace the current one
            changed_by: Actor performing the action
            reason: Optional note about why re-planning is needed

        Returns:
            Tuple of (success, result_dict)
        """
        try:
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

            if current_status in TERMINAL_STATES:
                return False, {
                    "error": f"Cannot re-plan task in terminal state '{current_status}'. "
                             "Cancel it first or create a new task."
                }

            history_entry: dict[str, Any] = {
                "from_status": current_status,
                "to_status": "planning",
                "changed_by": changed_by,
                "changed_at": datetime.now().isoformat(),
                "action": "re-plan",
            }
            if reason:
                history_entry["reason"] = reason

            existing_history = task.get("state_history") or []
            if not isinstance(existing_history, list):
                existing_history = []

            now = datetime.now().isoformat()
            update_data: dict[str, Any] = {
                "status": "planning",
                "state_changed_at": now,
                "state_changed_by": changed_by,
                "state_history": existing_history + [history_entry],
                "updated_at": now,
                # Clear execution artifacts so the new planning cycle starts fresh
                "rejection_reason": None,
                "hold_reason": None,
            }

            if updated_description is not None:
                update_data["description"] = updated_description

            update_response = (
                self.supabase_client.table("archon_tasks")
                .update(update_data)
                .eq("id", task_id)
                .execute()
            )

            if not update_response.data:
                return False, {"error": f"Failed to re-plan task {task_id}"}

            updated_task = update_response.data[0]

            logger.info(
                f"Task re-planned | task_id={task_id} | "
                f"{current_status} → planning | by={changed_by}"
            )

            return True, {
                "task": updated_task,
                "transition": {
                    "from": current_status,
                    "to": "planning",
                    "changed_by": changed_by,
                    "action": "re-plan",
                    "reason": reason,
                },
            }

        except Exception as e:
            logger.error(f"Error re-planning task {task_id}: {e}", exc_info=True)
            return False, {"error": f"Error re-planning task: {str(e)}"}

    async def continue_after_clarification(
        self,
        task_id: str,
        guidance: str,
        changed_by: str = "system",
    ) -> tuple[bool, dict[str, Any]]:
        """
        Resume task execution by appending operator guidance and transitioning to 'assigned'.

        For tasks that are paused (on-hold, failed, escalated) or blocked (planning,
        owner-qa), this action appends the guidance text to the description and moves
        the task to 'assigned' so the engine can pick it up again.

        Args:
            task_id: UUID of the task to continue
            guidance: Clarification or additional instructions from the operator
            changed_by: Actor performing the action

        Returns:
            Tuple of (success, result_dict)
        """
        try:
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

            if current_status in TERMINAL_STATES:
                return False, {
                    "error": f"Cannot continue task in terminal state '{current_status}'."
                }

            # Continue is valid from paused/blocked states
            CONTINUABLE_STATES = {
                "on-hold", "failed", "escalated", "planning", "owner-qa",
                "assigned", "draft", "proposed", "approved",
            }
            if current_status not in CONTINUABLE_STATES:
                return False, {
                    "error": f"Cannot continue task from state '{current_status}'. "
                             f"Continue is valid from: {', '.join(sorted(CONTINUABLE_STATES))}"
                }

            history_entry: dict[str, Any] = {
                "from_status": current_status,
                "to_status": "assigned",
                "changed_by": changed_by,
                "changed_at": datetime.now().isoformat(),
                "action": "continue",
                "guidance": guidance,
            }

            existing_history = task.get("state_history") or []
            if not isinstance(existing_history, list):
                existing_history = []

            # Append guidance to existing description so the engine has full context
            current_description = task.get("description") or ""
            appended_description = (
                current_description.rstrip()
                + f"\n\n---\nOperator guidance ({datetime.now().strftime('%Y-%m-%d %H:%M')} UTC):\n{guidance}"
            )

            now = datetime.now().isoformat()
            update_data: dict[str, Any] = {
                "status": "assigned",
                "state_changed_at": now,
                "state_changed_by": changed_by,
                "state_history": existing_history + [history_entry],
                "description": appended_description,
                "updated_at": now,
                # Clear paused-state metadata
                "hold_reason": None,
                "rejection_reason": None,
            }

            update_response = (
                self.supabase_client.table("archon_tasks")
                .update(update_data)
                .eq("id", task_id)
                .execute()
            )

            if not update_response.data:
                return False, {"error": f"Failed to continue task {task_id}"}

            updated_task = update_response.data[0]

            logger.info(
                f"Task continued | task_id={task_id} | "
                f"{current_status} → assigned | by={changed_by}"
            )

            return True, {
                "task": updated_task,
                "transition": {
                    "from": current_status,
                    "to": "assigned",
                    "changed_by": changed_by,
                    "action": "continue",
                    "guidance": guidance,
                },
            }

        except Exception as e:
            logger.error(f"Error continuing task {task_id}: {e}", exc_info=True)
            return False, {"error": f"Error continuing task: {str(e)}"}

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
