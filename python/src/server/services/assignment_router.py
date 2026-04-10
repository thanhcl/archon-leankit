"""
Assignment Router for polymorphic task assignment.

Routes task assignments based on assignee_type:
- agent: Enqueue into execution pipeline (existing flow)
- human: Skip execution, update status + send notification
- unassigned: Clear assignment, return to pool

Handles reassignment mid-execution: agent→human cancels running execution.

Adopted from Multica polymorphic assignment pattern (Workstream M-P3-02).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..config.logfire_config import get_logger
from .event_bus import (
    TOPIC_ASSIGNMENT_CHANGED,
    TOPIC_TASK_ASSIGNED,
    EventBus,
)

logger = get_logger(__name__)

ASSIGNEE_TYPE_AGENT = "agent"
ASSIGNEE_TYPE_HUMAN = "human"
ASSIGNEE_TYPE_UNASSIGNED = "unassigned"
VALID_ASSIGNEE_TYPES = (ASSIGNEE_TYPE_AGENT, ASSIGNEE_TYPE_HUMAN, ASSIGNEE_TYPE_UNASSIGNED)


class AssignmentRouter:
    """Routes task assignments based on assignee type.

    Usage:
        router = AssignmentRouter(task_service, runtime_service, notifier, bus)
        await router.assign(task_id, assignee_type="human", assignee_id="user-123")
        await router.reassign(task_id, new_type="agent", new_id="agent-456")
    """

    def __init__(
        self,
        task_service: Any = None,
        runtime_service: Any = None,
        notifier: Any = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._task_service = task_service
        self._runtime_service = runtime_service
        self._notifier = notifier
        self._bus = event_bus or EventBus.get_instance()

    def _get_task_service(self) -> Any:
        if self._task_service is None:
            from .projects.task_service import TaskService
            self._task_service = TaskService()
        return self._task_service

    def _get_runtime_service(self) -> Any:
        if self._runtime_service is None:
            from .agent_runtime_service import AgentRuntimeService
            self._runtime_service = AgentRuntimeService()
        return self._runtime_service

    async def assign(
        self,
        task_id: str,
        assignee_type: str,
        assignee_id: str | None = None,
        reason: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Assign a task to a human or agent.

        Args:
            task_id: Task to assign.
            assignee_type: "agent", "human", or "unassigned".
            assignee_id: UUID of the assignee (required for agent/human).
            reason: Optional reason for assignment.

        Returns:
            (success, result_dict)
        """
        if assignee_type not in VALID_ASSIGNEE_TYPES:
            return False, {"error": f"Invalid assignee_type '{assignee_type}'. Must be one of: {', '.join(VALID_ASSIGNEE_TYPES)}"}

        if assignee_type in (ASSIGNEE_TYPE_AGENT, ASSIGNEE_TYPE_HUMAN) and not assignee_id:
            return False, {"error": f"assignee_id required for assignee_type '{assignee_type}'"}

        task_service = self._get_task_service()

        # Get current task state
        success, task_data = task_service.get_task(task_id)
        if not success:
            return False, {"error": f"Task {task_id} not found"}

        task = task_data.get("task") or task_data
        current_type = task.get("assignee_type")
        current_id = task.get("assignee_id")

        # Check if this is a reassignment (different assignee)
        is_reassignment = current_type is not None and (
            current_type != assignee_type or current_id != assignee_id
        )

        # Handle reassignment: cancel current execution if agent→human
        if is_reassignment and current_type == ASSIGNEE_TYPE_AGENT:
            await self._cancel_agent_execution(task_id, task)

        # Update task assignment fields
        now = datetime.now(timezone.utc).isoformat()
        update_fields: dict[str, Any] = {
            "assignee_type": assignee_type,
            "assignee_id": assignee_id,
            "updated_at": now,
        }

        # Clear runtime_id when assigning to human or unassigned
        if assignee_type != ASSIGNEE_TYPE_AGENT:
            update_fields["runtime_id"] = None

        success, result = await task_service.update_task(task_id, update_fields)
        if not success:
            return False, result

        # Route based on assignee type
        if assignee_type == ASSIGNEE_TYPE_AGENT:
            await self._route_to_agent(task_id, assignee_id, task)
        elif assignee_type == ASSIGNEE_TYPE_HUMAN:
            await self._route_to_human(task_id, assignee_id, task)
        else:
            await self._route_to_unassigned(task_id, task)

        # Emit event
        await self._bus.emit(TOPIC_ASSIGNMENT_CHANGED, {
            "task_id": task_id,
            "assignee_type": assignee_type,
            "assignee_id": assignee_id,
            "previous_type": current_type,
            "previous_id": current_id,
            "is_reassignment": is_reassignment,
            "reason": reason,
        })

        logger.info(f"Task assigned: task_id={task_id} assignee_type={assignee_type} assignee_id={assignee_id} is_reassignment={is_reassignment}")

        return True, {
            "task_id": task_id,
            "assignee_type": assignee_type,
            "assignee_id": assignee_id,
            "routed": True,
        }

    async def reassign(
        self,
        task_id: str,
        new_type: str,
        new_id: str | None = None,
        reason: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Reassign a task. Convenience wrapper for assign() that handles
        mid-execution cancellation."""
        return await self.assign(task_id, new_type, new_id, reason)

    # ── Routing Logic ─────────────────────────────────────────────────────

    async def _route_to_agent(
        self,
        task_id: str,
        agent_id: str | None,
        task: dict[str, Any],
    ) -> None:
        """Route task to agent: enters execution pipeline.

        The task is already in 'assigned' status — the engine's poll cycle
        will pick it up and begin execution.
        """
        await self._bus.emit(TOPIC_TASK_ASSIGNED, {
            "task_id": task_id,
            "assignee_type": ASSIGNEE_TYPE_AGENT,
            "assignee_id": agent_id,
            "title": task.get("title", ""),
        })

        logger.info(f"Task routed to agent execution pipeline: task_id={task_id} agent_id={agent_id}")

    async def _route_to_human(
        self,
        task_id: str,
        human_id: str | None,
        task: dict[str, Any],
    ) -> None:
        """Route task to human: notification only, no execution pipeline."""
        # Emit event for notification listeners
        await self._bus.emit(TOPIC_TASK_ASSIGNED, {
            "task_id": task_id,
            "assignee_type": ASSIGNEE_TYPE_HUMAN,
            "assignee_id": human_id,
            "title": task.get("title", ""),
            "notification_required": True,
        })

        # Send notification via notifier if available
        if self._notifier:
            try:
                await self._notifier.emit(
                    "task_assigned_to_human",
                    task_id=task_id,
                    data={
                        "assignee_id": human_id,
                        "title": task.get("title", ""),
                        "description": task.get("description", ""),
                    },
                )
            except Exception as e:
                logger.warning(f"Human assignment notification failed: {e}")

        logger.info(f"Task routed to human (notification only): task_id={task_id} human_id={human_id}")

    async def _route_to_unassigned(
        self,
        task_id: str,
        task: dict[str, Any],
    ) -> None:
        """Route task to unassigned: return to pool."""
        await self._bus.emit(TOPIC_TASK_ASSIGNED, {
            "task_id": task_id,
            "assignee_type": ASSIGNEE_TYPE_UNASSIGNED,
            "title": task.get("title", ""),
        })

        logger.info(f"Task unassigned (returned to pool): task_id={task_id}")

    async def _cancel_agent_execution(
        self,
        task_id: str,
        task: dict[str, Any],
    ) -> None:
        """Cancel running execution when reassigning from agent to human/unassigned."""
        runtime_id = task.get("runtime_id")
        if not runtime_id:
            return

        runtime_service = self._get_runtime_service()

        # Release the task from the runtime
        try:
            await runtime_service.release_task(runtime_id, task_id)
            logger.info(f"Agent execution cancelled for reassignment: task_id={task_id} runtime_id={runtime_id}")
        except Exception as e:
            logger.warning(f"Failed to cancel agent execution: task_id={task_id} runtime_id={runtime_id} error={e}")
