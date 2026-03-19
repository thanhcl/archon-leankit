"""Tests for TaskLifecycleService — transition rules and cancellation."""

from unittest.mock import MagicMock

import pytest

from src.server.services.projects.task_lifecycle_service import (
    TERMINAL_STATES,
    TRANSITION_RULES,
    VALID_STATUSES,
    TaskLifecycleService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
        "status": "draft",
        "state_history": [],
        "retry_count": 0,
    }
    base.update(overrides)
    return base


def _mock_client(select_data=None, update_data=None):
    client = MagicMock()
    table = MagicMock()

    # select chain
    select = MagicMock()
    select.eq.return_value = select
    execute_result = MagicMock()
    execute_result.data = select_data if select_data is not None else []
    select.execute.return_value = execute_result
    table.select.return_value = select

    # update chain
    update = MagicMock()
    update.eq.return_value = update
    update_result = MagicMock()
    update_result.data = update_data if update_data is not None else [_make_task()]
    update.execute.return_value = update_result
    table.update.return_value = update

    client.table.return_value = table
    return client


# ---------------------------------------------------------------------------
# Tests: cancellation from every non-terminal state
# ---------------------------------------------------------------------------

# All states that are NOT terminal should allow → cancelled
NON_TERMINAL_STATES = [s for s in VALID_STATUSES if s not in TERMINAL_STATES]


class TestCancellationFromAllStates:
    """Owner must be able to cancel a task from any non-terminal state."""

    @pytest.mark.parametrize("state", NON_TERMINAL_STATES)
    def test_cancelled_is_valid_transition(self, state):
        """Every non-terminal state must include 'cancelled' in its transition set."""
        allowed = TRANSITION_RULES.get(state, set())
        assert "cancelled" in allowed, (
            f"State '{state}' is missing 'cancelled' in TRANSITION_RULES. "
            f"Current allowed: {sorted(allowed)}"
        )

    @pytest.mark.parametrize("state", NON_TERMINAL_STATES)
    def test_validate_transition_allows_cancel_with_reason(self, state):
        service = TaskLifecycleService(supabase_client=MagicMock())
        is_valid, error = service.validate_transition(state, "cancelled", reason="No longer needed")
        assert is_valid is True, f"Transition {state} → cancelled should be valid, got: {error}"

    @pytest.mark.parametrize("state", NON_TERMINAL_STATES)
    def test_cancel_requires_reason(self, state):
        """Cancellation from any state must require a reason."""
        service = TaskLifecycleService(supabase_client=MagicMock())
        is_valid, error = service.validate_transition(state, "cancelled", reason=None)
        assert is_valid is False, f"Transition {state} → cancelled without reason should fail"
        assert "requires a reason" in error


class TestTerminalStatesBlockTransition:
    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
    def test_cannot_transition_from_terminal(self, state):
        service = TaskLifecycleService(supabase_client=MagicMock())
        is_valid, error = service.validate_transition(state, "cancelled", reason="test")
        assert is_valid is False
        assert "terminal" in error.lower()


# ---------------------------------------------------------------------------
# Tests: execute_transition for cancel
# ---------------------------------------------------------------------------


class TestExecuteCancel:
    @pytest.mark.asyncio
    async def test_cancel_from_assigned(self):
        task = _make_task(status="assigned")
        updated_task = _make_task(status="cancelled")
        client = _mock_client(select_data=[task], update_data=[updated_task])
        service = TaskLifecycleService(supabase_client=client)

        ok, result = await service.execute_transition(
            "task-001", "cancelled", changed_by="Owner", reason="Scope changed"
        )

        assert ok is True
        assert result["transition"]["from"] == "assigned"
        assert result["transition"]["to"] == "cancelled"
        update_call = client.table().update.call_args[0][0]
        assert update_call["status"] == "cancelled"
        assert update_call["rejection_reason"] == "Scope changed"

    @pytest.mark.asyncio
    async def test_cancel_from_executing(self):
        task = _make_task(status="executing")
        updated_task = _make_task(status="cancelled")
        client = _mock_client(select_data=[task], update_data=[updated_task])
        service = TaskLifecycleService(supabase_client=client)

        ok, result = await service.execute_transition(
            "task-001", "cancelled", changed_by="Owner", reason="Blocked by dependency"
        )

        assert ok is True
        assert result["transition"]["from"] == "executing"
        assert result["transition"]["to"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_from_failed(self):
        task = _make_task(status="failed")
        updated_task = _make_task(status="cancelled")
        client = _mock_client(select_data=[task], update_data=[updated_task])
        service = TaskLifecycleService(supabase_client=client)

        ok, result = await service.execute_transition(
            "task-001", "cancelled", changed_by="Owner", reason="Will not fix"
        )

        assert ok is True

    @pytest.mark.asyncio
    async def test_cancel_rejected_without_reason(self):
        task = _make_task(status="assigned")
        client = _mock_client(select_data=[task])
        service = TaskLifecycleService(supabase_client=client)

        ok, result = await service.execute_transition(
            "task-001", "cancelled", changed_by="Owner", reason=None
        )

        assert ok is False
        assert "requires a reason" in result["error"]

    @pytest.mark.asyncio
    async def test_cancel_from_done_blocked(self):
        task = _make_task(status="done")
        client = _mock_client(select_data=[task])
        service = TaskLifecycleService(supabase_client=client)

        ok, result = await service.execute_transition(
            "task-001", "cancelled", changed_by="Owner", reason="Undo"
        )

        assert ok is False
        assert "terminal" in result["error"].lower()


# ---------------------------------------------------------------------------
# Tests: validate_transition basics
# ---------------------------------------------------------------------------


class TestValidateTransition:
    def test_valid_forward_transition(self):
        service = TaskLifecycleService(supabase_client=MagicMock())
        is_valid, error = service.validate_transition("draft", "proposed")
        assert is_valid is True

    def test_invalid_transition(self):
        service = TaskLifecycleService(supabase_client=MagicMock())
        is_valid, error = service.validate_transition("draft", "executing")
        assert is_valid is False
        assert "Invalid transition" in error

    def test_invalid_current_status(self):
        service = TaskLifecycleService(supabase_client=MagicMock())
        is_valid, error = service.validate_transition("bogus", "cancelled")
        assert is_valid is False
        assert "Invalid current status" in error

    def test_invalid_target_status(self):
        service = TaskLifecycleService(supabase_client=MagicMock())
        is_valid, error = service.validate_transition("draft", "bogus")
        assert is_valid is False
        assert "Invalid target status" in error


# ---------------------------------------------------------------------------
# Tests: get_valid_next_states
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: on-hold reachable from all active states
# ---------------------------------------------------------------------------

# States that must allow → on-hold
ON_HOLD_SOURCE_STATES = [
    "approved", "assigned", "executing", "architect-review",
    "review", "failed", "escalated",
]


class TestOnHoldFromAllActiveStates:
    """Owner must be able to put a task on-hold from any active state."""

    @pytest.mark.parametrize("state", ON_HOLD_SOURCE_STATES)
    def test_on_hold_is_valid_transition(self, state):
        allowed = TRANSITION_RULES.get(state, set())
        assert "on-hold" in allowed, (
            f"State '{state}' is missing 'on-hold' in TRANSITION_RULES. "
            f"Current allowed: {sorted(allowed)}"
        )

    @pytest.mark.parametrize("state", ON_HOLD_SOURCE_STATES)
    def test_validate_transition_allows_on_hold_with_reason(self, state):
        service = TaskLifecycleService(supabase_client=MagicMock())
        is_valid, error = service.validate_transition(state, "on-hold", reason="Waiting on dependency")
        assert is_valid is True, f"Transition {state} → on-hold should be valid, got: {error}"

    @pytest.mark.parametrize("state", ON_HOLD_SOURCE_STATES)
    def test_on_hold_requires_reason(self, state):
        service = TaskLifecycleService(supabase_client=MagicMock())
        is_valid, error = service.validate_transition(state, "on-hold", reason=None)
        assert is_valid is False, f"Transition {state} → on-hold without reason should fail"
        assert "requires a reason" in error


class TestOnHoldResume:
    """on-hold → assigned (resume) must work."""

    def test_on_hold_to_assigned_valid(self):
        service = TaskLifecycleService(supabase_client=MagicMock())
        is_valid, error = service.validate_transition("on-hold", "assigned")
        assert is_valid is True, f"on-hold → assigned should be valid, got: {error}"

    def test_on_hold_to_approved_still_valid(self):
        service = TaskLifecycleService(supabase_client=MagicMock())
        is_valid, error = service.validate_transition("on-hold", "approved")
        assert is_valid is True, f"on-hold → approved should still be valid, got: {error}"

    @pytest.mark.asyncio
    async def test_execute_on_hold_to_assigned_clears_hold_reason(self):
        task = _make_task(status="on-hold", hold_reason="Blocked")
        updated_task = _make_task(status="assigned")
        client = _mock_client(select_data=[task], update_data=[updated_task])
        service = TaskLifecycleService(supabase_client=client)

        ok, result = await service.execute_transition(
            "task-001", "assigned", changed_by="Owner"
        )

        assert ok is True
        assert result["transition"]["from"] == "on-hold"
        assert result["transition"]["to"] == "assigned"
        update_call = client.table().update.call_args[0][0]
        assert update_call["hold_reason"] is None


class TestExecuteOnHoldFromActiveStates:
    """Execute transitions to on-hold from various active states."""

    @pytest.mark.asyncio
    async def test_on_hold_from_executing(self):
        task = _make_task(status="executing")
        updated_task = _make_task(status="on-hold")
        client = _mock_client(select_data=[task], update_data=[updated_task])
        service = TaskLifecycleService(supabase_client=client)

        ok, result = await service.execute_transition(
            "task-001", "on-hold", changed_by="Owner", reason="Waiting for API access"
        )

        assert ok is True
        assert result["transition"]["from"] == "executing"
        assert result["transition"]["to"] == "on-hold"
        update_call = client.table().update.call_args[0][0]
        assert update_call["hold_reason"] == "Waiting for API access"

    @pytest.mark.asyncio
    async def test_on_hold_from_review(self):
        task = _make_task(status="review")
        updated_task = _make_task(status="on-hold")
        client = _mock_client(select_data=[task], update_data=[updated_task])
        service = TaskLifecycleService(supabase_client=client)

        ok, result = await service.execute_transition(
            "task-001", "on-hold", changed_by="Owner", reason="Reviewer unavailable"
        )

        assert ok is True
        assert result["transition"]["to"] == "on-hold"

    @pytest.mark.asyncio
    async def test_on_hold_from_failed(self):
        task = _make_task(status="failed")
        updated_task = _make_task(status="on-hold")
        client = _mock_client(select_data=[task], update_data=[updated_task])
        service = TaskLifecycleService(supabase_client=client)

        ok, result = await service.execute_transition(
            "task-001", "on-hold", changed_by="Owner", reason="Investigating root cause"
        )

        assert ok is True
        assert result["transition"]["to"] == "on-hold"


# ---------------------------------------------------------------------------
# Tests: get_valid_next_states
# ---------------------------------------------------------------------------


class TestGetValidNextStates:
    def test_terminal_returns_empty(self):
        service = TaskLifecycleService(supabase_client=MagicMock())
        assert service.get_valid_next_states("done") == []
        assert service.get_valid_next_states("cancelled") == []

    def test_assigned_includes_cancelled(self):
        service = TaskLifecycleService(supabase_client=MagicMock())
        next_states = service.get_valid_next_states("assigned")
        assert "cancelled" in next_states
        assert "executing" in next_states

    def test_executing_includes_cancelled(self):
        service = TaskLifecycleService(supabase_client=MagicMock())
        next_states = service.get_valid_next_states("executing")
        assert "cancelled" in next_states

    def test_failed_includes_cancelled(self):
        service = TaskLifecycleService(supabase_client=MagicMock())
        next_states = service.get_valid_next_states("failed")
        assert "cancelled" in next_states
