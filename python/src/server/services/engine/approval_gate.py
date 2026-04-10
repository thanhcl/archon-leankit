"""
Approval Gate — Human-in-the-loop verification with rejection retry.

Adopted from upstream Archon v0.3.2 approval node pattern.
Pauses task execution until a human approves or rejects.
On rejection, injects structured $REJECTION_REASON into retry prompt.

Features:
- Configurable max rejection attempts before auto-escalation
- Structured rejection reasons passed to retry prompts
- Timeout-based auto-escalation
- Integration with Notifier for multi-channel approval requests

Usage:
    gate = ApprovalGate(notifier=notifier, lifecycle_service=lifecycle)
    result = await gate.request_approval(task_id, gate_config)
    if result.rejected:
        # Retry with rejection reason
        enhanced_prompt = gate.build_rejection_retry_prompt(
            original_prompt, result.rejection_reason
        )
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Default configuration
DEFAULT_MAX_REJECTIONS = 3
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 3600  # 1 hour


class ApprovalStatus(str, Enum):
    """Status of an approval gate."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    AUTO_ESCALATED = "auto_escalated"


@dataclass
class ApprovalGateConfig:
    """Configuration for an approval gate."""

    gate_message: str = "Please review and approve this task."
    max_rejections: int = DEFAULT_MAX_REJECTIONS
    timeout_seconds: int = DEFAULT_APPROVAL_TIMEOUT_SECONDS
    capture_response: bool = True
    on_reject_prompt_template: str | None = None
    auto_escalate_on_timeout: bool = True


@dataclass
class ApprovalResult:
    """Result of an approval gate evaluation."""

    status: ApprovalStatus = ApprovalStatus.PENDING
    rejection_reason: str = ""
    rejection_count: int = 0
    reviewer: str = ""
    response_text: str = ""
    decided_at: float = 0.0

    @property
    def approved(self) -> bool:
        return self.status == ApprovalStatus.APPROVED

    @property
    def rejected(self) -> bool:
        return self.status == ApprovalStatus.REJECTED

    @property
    def should_retry(self) -> bool:
        """Whether a retry with rejection feedback is possible."""
        return self.status == ApprovalStatus.REJECTED

    @property
    def should_escalate(self) -> bool:
        """Whether the gate outcome requires escalation."""
        return self.status in {ApprovalStatus.TIMED_OUT, ApprovalStatus.AUTO_ESCALATED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "rejection_reason": self.rejection_reason,
            "rejection_count": self.rejection_count,
            "reviewer": self.reviewer,
            "response_text": self.response_text,
            "decided_at": self.decided_at,
        }


@dataclass
class TaskApprovalState:
    """Tracks approval state across multiple rejection-retry cycles for a task."""

    task_id: str
    config: ApprovalGateConfig
    rejection_count: int = 0
    rejection_history: list[dict[str, Any]] = field(default_factory=list)
    current_status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: float = field(default_factory=time.time)

    @property
    def max_rejections_reached(self) -> bool:
        return self.rejection_count >= self.config.max_rejections

    def record_rejection(self, reason: str, reviewer: str = "") -> None:
        """Record a rejection and update state."""
        self.rejection_count += 1
        self.rejection_history.append({
            "reason": reason,
            "reviewer": reviewer,
            "attempt": self.rejection_count,
            "timestamp": time.time(),
        })
        self.current_status = ApprovalStatus.REJECTED

    def record_approval(self, reviewer: str = "") -> None:
        """Record an approval."""
        self.current_status = ApprovalStatus.APPROVED


class ApprovalGate:
    """Manages human-in-the-loop approval gates with rejection retry.

    Tracks approval state per task across multiple rejection-retry cycles.
    Integrates with Notifier for sending approval requests and with
    TaskEngine for retry prompt enhancement.
    """

    def __init__(self) -> None:
        self._active_gates: dict[str, TaskApprovalState] = {}

    def create_gate(
        self,
        task_id: str,
        config: ApprovalGateConfig | None = None,
    ) -> TaskApprovalState:
        """Create or retrieve an approval gate for a task."""
        if task_id in self._active_gates:
            return self._active_gates[task_id]

        gate_config = config or ApprovalGateConfig()
        state = TaskApprovalState(task_id=task_id, config=gate_config)
        self._active_gates[task_id] = state

        logger.info(
            f"Approval gate created | task_id={task_id} | "
            f"max_rejections={gate_config.max_rejections} | "
            f"timeout={gate_config.timeout_seconds}s"
        )
        return state

    def process_decision(
        self,
        task_id: str,
        approved: bool,
        reason: str = "",
        reviewer: str = "",
    ) -> ApprovalResult:
        """Process an approval or rejection decision.

        Args:
            task_id: The task being reviewed.
            approved: True for approval, False for rejection.
            reason: Rejection reason (required when approved=False).
            reviewer: Who made the decision.

        Returns:
            ApprovalResult indicating next action.
        """
        state = self._active_gates.get(task_id)
        if not state:
            logger.warning(f"No active approval gate for task_id={task_id}")
            return ApprovalResult(
                status=ApprovalStatus.APPROVED,
                reviewer=reviewer,
                decided_at=time.time(),
            )

        if approved:
            state.record_approval(reviewer)
            result = ApprovalResult(
                status=ApprovalStatus.APPROVED,
                reviewer=reviewer,
                decided_at=time.time(),
            )
            # Clean up completed gate
            self._active_gates.pop(task_id, None)
            logger.info(f"Approval gate APPROVED | task_id={task_id} | reviewer={reviewer}")
            return result

        # Rejection
        state.record_rejection(reason, reviewer)

        if state.max_rejections_reached:
            state.current_status = ApprovalStatus.AUTO_ESCALATED
            result = ApprovalResult(
                status=ApprovalStatus.AUTO_ESCALATED,
                rejection_reason=reason,
                rejection_count=state.rejection_count,
                reviewer=reviewer,
                decided_at=time.time(),
            )
            self._active_gates.pop(task_id, None)
            logger.warning(
                f"Approval gate AUTO-ESCALATED | task_id={task_id} | "
                f"rejections={state.rejection_count}/{state.config.max_rejections}"
            )
            return result

        result = ApprovalResult(
            status=ApprovalStatus.REJECTED,
            rejection_reason=reason,
            rejection_count=state.rejection_count,
            reviewer=reviewer,
            decided_at=time.time(),
        )
        logger.info(
            f"Approval gate REJECTED | task_id={task_id} | "
            f"rejections={state.rejection_count}/{state.config.max_rejections} | "
            f"reason={reason[:200]}"
        )
        return result

    def build_rejection_retry_prompt(
        self,
        original_prompt: str,
        rejection_reason: str,
        rejection_count: int = 1,
        max_rejections: int = DEFAULT_MAX_REJECTIONS,
        custom_template: str | None = None,
    ) -> str:
        """Build an enhanced retry prompt incorporating rejection feedback.

        Args:
            original_prompt: The original task execution prompt.
            rejection_reason: Why the reviewer rejected.
            rejection_count: Current rejection attempt number.
            max_rejections: Maximum allowed rejections.
            custom_template: Optional custom template with $REJECTION_REASON placeholder.

        Returns:
            Enhanced prompt with rejection context.
        """
        if custom_template:
            return custom_template.replace("$REJECTION_REASON", rejection_reason)

        rejection_section = (
            f"\n\n## Reviewer Feedback (Attempt {rejection_count}/{max_rejections})\n\n"
            f"The reviewer rejected your previous submission with the following feedback:\n\n"
            f"> {rejection_reason}\n\n"
            f"**IMPORTANT**: Address ALL points in the feedback above before resubmitting. "
            f"You have {max_rejections - rejection_count} attempt(s) remaining before "
            f"this task is escalated for human intervention.\n"
        )

        return original_prompt + rejection_section

    def get_gate_state(self, task_id: str) -> TaskApprovalState | None:
        """Get the current approval state for a task."""
        return self._active_gates.get(task_id)

    def get_rejection_history(self, task_id: str) -> list[dict[str, Any]]:
        """Get the full rejection history for a task."""
        state = self._active_gates.get(task_id)
        if not state:
            return []
        return state.rejection_history

    def cancel_gate(self, task_id: str) -> bool:
        """Cancel an active approval gate."""
        removed = self._active_gates.pop(task_id, None)
        if removed:
            logger.info(f"Approval gate cancelled | task_id={task_id}")
        return removed is not None

    @property
    def active_gate_count(self) -> int:
        """Number of tasks with active approval gates."""
        return len(self._active_gates)

    def list_active_gates(self) -> list[dict[str, Any]]:
        """List all active approval gates with their state."""
        return [
            {
                "task_id": state.task_id,
                "status": state.current_status.value,
                "rejection_count": state.rejection_count,
                "max_rejections": state.config.max_rejections,
                "created_at": state.created_at,
            }
            for state in self._active_gates.values()
        ]


# Module-level singleton
_default_gate: ApprovalGate | None = None


def get_approval_gate() -> ApprovalGate:
    """Get or create the module-level ApprovalGate singleton."""
    global _default_gate
    if _default_gate is None:
        _default_gate = ApprovalGate()
    return _default_gate
