"""Tests for ApprovalGate — human-in-the-loop verification with rejection retry."""

import pytest

from src.server.services.engine.approval_gate import (
    ApprovalGate,
    ApprovalGateConfig,
    ApprovalResult,
    ApprovalStatus,
    TaskApprovalState,
    get_approval_gate,
)


@pytest.fixture
def gate() -> ApprovalGate:
    return ApprovalGate()


@pytest.fixture
def config() -> ApprovalGateConfig:
    return ApprovalGateConfig(
        gate_message="Please review this task.",
        max_rejections=3,
        capture_response=True,
    )


class TestCreateGate:
    """Test gate creation and retrieval."""

    def test_create_new_gate(self, gate: ApprovalGate, config: ApprovalGateConfig) -> None:
        state = gate.create_gate("t-1", config)
        assert state.task_id == "t-1"
        assert state.rejection_count == 0
        assert state.current_status == ApprovalStatus.PENDING

    def test_retrieve_existing_gate(self, gate: ApprovalGate, config: ApprovalGateConfig) -> None:
        state1 = gate.create_gate("t-1", config)
        state2 = gate.create_gate("t-1", config)
        assert state1 is state2  # Same object returned

    def test_default_config(self, gate: ApprovalGate) -> None:
        state = gate.create_gate("t-1")
        assert state.config.max_rejections == 3
        assert state.config.timeout_seconds == 3600


class TestProcessDecision:
    """Test approval and rejection decisions."""

    def test_approve(self, gate: ApprovalGate, config: ApprovalGateConfig) -> None:
        gate.create_gate("t-1", config)
        result = gate.process_decision("t-1", approved=True, reviewer="user-1")
        assert result.approved is True
        assert result.reviewer == "user-1"
        assert gate.active_gate_count == 0  # Gate cleaned up

    def test_reject_first_time(self, gate: ApprovalGate, config: ApprovalGateConfig) -> None:
        gate.create_gate("t-1", config)
        result = gate.process_decision("t-1", approved=False, reason="Fix the tests")
        assert result.rejected is True
        assert result.rejection_reason == "Fix the tests"
        assert result.rejection_count == 1
        assert result.should_retry is True

    def test_reject_multiple_times(self, gate: ApprovalGate, config: ApprovalGateConfig) -> None:
        gate.create_gate("t-1", config)
        gate.process_decision("t-1", approved=False, reason="Fix tests")
        gate.process_decision("t-1", approved=False, reason="Still broken")
        result = gate.process_decision("t-1", approved=False, reason="Give up")
        assert result.status == ApprovalStatus.AUTO_ESCALATED
        assert result.rejection_count == 3
        assert result.should_escalate is True
        assert gate.active_gate_count == 0  # Gate cleaned up

    def test_approve_after_rejection(self, gate: ApprovalGate, config: ApprovalGateConfig) -> None:
        gate.create_gate("t-1", config)
        gate.process_decision("t-1", approved=False, reason="Fix the tests")
        result = gate.process_decision("t-1", approved=True)
        assert result.approved is True
        assert gate.active_gate_count == 0

    def test_no_gate_returns_approved(self, gate: ApprovalGate) -> None:
        result = gate.process_decision("nonexistent", approved=True)
        assert result.approved is True


class TestRejectionHistory:
    """Test rejection history tracking."""

    def test_rejection_history(self, gate: ApprovalGate, config: ApprovalGateConfig) -> None:
        gate.create_gate("t-1", config)
        gate.process_decision("t-1", approved=False, reason="Bug 1", reviewer="alice")
        gate.process_decision("t-1", approved=False, reason="Bug 2", reviewer="bob")

        history = gate.get_rejection_history("t-1")
        assert len(history) == 2
        assert history[0]["reason"] == "Bug 1"
        assert history[0]["reviewer"] == "alice"
        assert history[1]["reason"] == "Bug 2"
        assert history[1]["attempt"] == 2

    def test_empty_history_for_unknown_task(self, gate: ApprovalGate) -> None:
        assert gate.get_rejection_history("nonexistent") == []


class TestBuildRejectionRetryPrompt:
    """Test prompt enhancement with rejection feedback."""

    def test_default_template(self, gate: ApprovalGate) -> None:
        prompt = gate.build_rejection_retry_prompt(
            original_prompt="Implement the auth module.",
            rejection_reason="Tests are missing for the login endpoint.",
            rejection_count=1,
            max_rejections=3,
        )
        assert "Implement the auth module." in prompt
        assert "Tests are missing for the login endpoint." in prompt
        assert "Attempt 1/3" in prompt
        assert "2 attempt(s) remaining" in prompt

    def test_custom_template(self, gate: ApprovalGate) -> None:
        prompt = gate.build_rejection_retry_prompt(
            original_prompt="Implement feature X.",
            rejection_reason="Missing error handling.",
            custom_template="Fix this: $REJECTION_REASON\nThen redo the task.",
        )
        assert prompt == "Fix this: Missing error handling.\nThen redo the task."

    def test_last_attempt_warning(self, gate: ApprovalGate) -> None:
        prompt = gate.build_rejection_retry_prompt(
            original_prompt="Fix the bug.",
            rejection_reason="Still broken.",
            rejection_count=2,
            max_rejections=3,
        )
        assert "1 attempt(s) remaining" in prompt


class TestTaskApprovalState:
    """Test TaskApprovalState behavior."""

    def test_max_rejections_reached(self) -> None:
        config = ApprovalGateConfig(max_rejections=2)
        state = TaskApprovalState(task_id="t-1", config=config)
        state.record_rejection("r1")
        assert state.max_rejections_reached is False
        state.record_rejection("r2")
        assert state.max_rejections_reached is True

    def test_record_approval(self) -> None:
        config = ApprovalGateConfig()
        state = TaskApprovalState(task_id="t-1", config=config)
        state.record_approval("alice")
        assert state.current_status == ApprovalStatus.APPROVED


class TestApprovalResult:
    """Test ApprovalResult properties."""

    def test_approved_result(self) -> None:
        result = ApprovalResult(status=ApprovalStatus.APPROVED)
        assert result.approved is True
        assert result.rejected is False
        assert result.should_retry is False
        assert result.should_escalate is False

    def test_rejected_result(self) -> None:
        result = ApprovalResult(status=ApprovalStatus.REJECTED)
        assert result.approved is False
        assert result.rejected is True
        assert result.should_retry is True
        assert result.should_escalate is False

    def test_escalated_result(self) -> None:
        result = ApprovalResult(status=ApprovalStatus.AUTO_ESCALATED)
        assert result.should_escalate is True
        assert result.should_retry is False

    def test_timed_out_result(self) -> None:
        result = ApprovalResult(status=ApprovalStatus.TIMED_OUT)
        assert result.should_escalate is True


class TestCancelGate:
    """Test gate cancellation."""

    def test_cancel_active_gate(self, gate: ApprovalGate) -> None:
        gate.create_gate("t-1")
        assert gate.cancel_gate("t-1") is True
        assert gate.active_gate_count == 0

    def test_cancel_nonexistent_gate(self, gate: ApprovalGate) -> None:
        assert gate.cancel_gate("nonexistent") is False


class TestListActiveGates:
    """Test listing active gates."""

    def test_list_gates(self, gate: ApprovalGate) -> None:
        gate.create_gate("t-1")
        gate.create_gate("t-2")
        gates = gate.list_active_gates()
        assert len(gates) == 2
        task_ids = {g["task_id"] for g in gates}
        assert task_ids == {"t-1", "t-2"}


class TestSingleton:
    """Test module-level singleton."""

    def test_singleton_returns_same_instance(self) -> None:
        g1 = get_approval_gate()
        g2 = get_approval_gate()
        assert g1 is g2
