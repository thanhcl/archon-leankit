"""Tests for contract lock-and-revision rules.

Covers:
- lock_contract(): one active locked revision per task cycle
- lock_contract(): records locked_at and locked_by metadata
- update_contract(): cannot set negotiation_status='locked' directly
- supersede_contract(): marks contract as superseded (idempotent)
- execute_transition() proposed→approved: requires locked contract
- execute_transition() retry + new_acceptance_criteria: supersedes + creates new draft
- execute_transition() retry without new_acceptance_criteria: keeps existing contract
- re_plan(): supersedes locked contract and creates new draft
- re_plan(): skips contract revision when no current contract exists
"""

from unittest.mock import MagicMock, call, patch

import pytest

from src.server.services.projects.task_lifecycle_service import TaskLifecycleService
from src.server.services.projects.task_service import TaskService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
        "title": "Implement feature",
        "description": "Some description",
        "status": "proposed",
        "state_history": [],
        "retry_count": 0,
        "current_contract_id": None,
    }
    base.update(overrides)
    return base


def _make_contract(**overrides):
    base = {
        "id": "contract-001",
        "task_id": "task-001",
        "version": 1,
        "objective": "Implement feature",
        "in_scope_paths": ["src/feature/"],
        "acceptance_criteria": [{"name": "AC-1", "description": "Works", "threshold": None}],
        "negotiation_status": "draft",
        "locked_at": None,
        "locked_by": None,
        "supersedes_contract_id": None,
        "created_at": "2026-03-30T10:00:00",
        "updated_at": "2026-03-30T10:00:00",
    }
    base.update(overrides)
    return base


def _make_ts_client(contract_data=None, locked_contracts=None, update_result=None):
    """Mock client for TaskService with multi-call contracts table support."""
    client = MagicMock()

    # Tasks table
    tasks_table = MagicMock()
    tasks_select = MagicMock()
    tasks_select.eq.return_value = tasks_select
    tasks_select.execute.return_value = MagicMock(data=[{"id": "task-001"}])
    tasks_table.select.return_value = tasks_select
    tasks_update = MagicMock()
    tasks_update.eq.return_value = tasks_update
    tasks_update.execute.return_value = MagicMock(data=[_make_task()])
    tasks_table.update.return_value = tasks_update

    # Contracts table: supports chained .eq() calls
    contracts_table = MagicMock()

    # Track call index for select chains
    _call_state = {"fetch_count": 0}

    def _make_contracts_chain(result_data):
        chain = MagicMock()
        chain.eq.return_value = chain
        chain.select.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = MagicMock(data=result_data)
        return chain

    # select() side_effect: first call returns contract_data, second returns locked_check
    def contracts_select_side_effect(*args, **kwargs):
        _call_state["fetch_count"] += 1
        if _call_state["fetch_count"] == 1:
            return _make_contracts_chain(contract_data or [])
        else:
            return _make_contracts_chain(locked_contracts or [])

    contracts_table.select.side_effect = contracts_select_side_effect

    # update chain
    contracts_update = MagicMock()
    contracts_update.eq.return_value = contracts_update
    contracts_update.execute.return_value = MagicMock(
        data=[update_result or _make_contract(locked_at="2026-03-31T12:00:00", negotiation_status="locked")]
    )
    contracts_table.update.return_value = contracts_update

    # version query (for create_contract)
    contracts_table.insert = MagicMock()
    insert_chain = MagicMock()
    insert_chain.execute.return_value = MagicMock(data=[_make_contract(id="contract-002", version=2)])
    contracts_table.insert.return_value = insert_chain

    def _table_router(name):
        if name == "archon_task_contracts":
            return contracts_table
        if name == "archon_tasks":
            return tasks_table
        return MagicMock()

    client.table.side_effect = _table_router
    return client


def _make_lifecycle_client(initial_task, updated_task):
    """Mock client for TaskLifecycleService.execute_transition."""
    client = MagicMock()
    table = MagicMock()

    select = MagicMock()
    select.eq.return_value = select
    select.execute.return_value = MagicMock(data=[initial_task])
    table.select.return_value = select

    update = MagicMock()
    update.eq.return_value = update
    update.execute.return_value = MagicMock(data=[updated_task])
    table.update.return_value = update

    client.table.return_value = table
    return client


# ---------------------------------------------------------------------------
# Tests: lock_contract() — one active locked revision
# ---------------------------------------------------------------------------


class TestLockContractOneActiveRevision:
    def test_lock_succeeds_when_no_existing_locked_contract(self):
        contract = _make_contract(negotiation_status="draft")
        client = _make_ts_client(
            contract_data=[contract],
            locked_contracts=[],  # no existing locked
            update_result=_make_contract(locked_at="2026-03-31T12:00:00", negotiation_status="locked"),
        )
        service = TaskService(supabase_client=client)

        ok, result = service.lock_contract("contract-001")

        assert ok is True
        assert result["contract"]["locked_at"] is not None
        assert result["contract"]["negotiation_status"] == "locked"

    def test_lock_rejected_when_locked_contract_already_exists(self):
        contract = _make_contract(negotiation_status="draft")
        # Another contract is already locked for this task
        client = _make_ts_client(
            contract_data=[contract],
            locked_contracts=[{"id": "contract-old"}],
        )
        service = TaskService(supabase_client=client)

        ok, result = service.lock_contract("contract-001")

        assert ok is False
        assert "locked contract already exists" in result["error"]

    def test_lock_rejected_when_already_locked(self):
        already_locked = _make_contract(
            locked_at="2026-03-30T10:00:00", negotiation_status="locked"
        )
        client = _make_ts_client(contract_data=[already_locked])
        service = TaskService(supabase_client=client)

        ok, result = service.lock_contract("contract-001")

        assert ok is False
        assert "already locked" in result["error"]

    def test_lock_stores_locked_by(self):
        contract = _make_contract()
        locked_result = _make_contract(
            locked_at="2026-03-31T12:00:00",
            negotiation_status="locked",
            locked_by="owner",
        )
        client = _make_ts_client(
            contract_data=[contract],
            locked_contracts=[],
            update_result=locked_result,
        )
        service = TaskService(supabase_client=client)

        ok, result = service.lock_contract("contract-001", locked_by="owner")

        assert ok is True
        assert result["contract"]["locked_by"] == "owner"
        # Verify locked_by was included in the update call
        update_call = client.table("archon_task_contracts").update.call_args[0][0]
        assert update_call["locked_by"] == "owner"

    def test_lock_not_found(self):
        client = _make_ts_client(contract_data=[])
        service = TaskService(supabase_client=client)

        ok, result = service.lock_contract("missing-contract")

        assert ok is False
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# Tests: supersede_contract()
# ---------------------------------------------------------------------------


class TestSupersededContract:
    def test_supersede_locked_contract(self):
        locked_contract = _make_contract(negotiation_status="locked")
        superseded_result = _make_contract(negotiation_status="superseded")
        client = MagicMock()

        contracts_table = MagicMock()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.execute.return_value = MagicMock(data=[locked_contract])
        contracts_table.select.return_value = select_chain

        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock(data=[superseded_result])
        contracts_table.update.return_value = update_chain

        client.table.return_value = contracts_table
        service = TaskService(supabase_client=client)

        ok, result = service.supersede_contract("contract-001")

        assert ok is True
        assert result["contract"]["negotiation_status"] == "superseded"

    def test_supersede_already_superseded_is_idempotent(self):
        already_superseded = _make_contract(negotiation_status="superseded")
        client = MagicMock()

        contracts_table = MagicMock()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.execute.return_value = MagicMock(data=[already_superseded])
        contracts_table.select.return_value = select_chain
        client.table.return_value = contracts_table

        service = TaskService(supabase_client=client)
        ok, result = service.supersede_contract("contract-001")

        assert ok is True
        # No update call should have been made
        contracts_table.update.assert_not_called()

    def test_supersede_not_found(self):
        client = MagicMock()
        contracts_table = MagicMock()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.execute.return_value = MagicMock(data=[])
        contracts_table.select.return_value = select_chain
        client.table.return_value = contracts_table

        service = TaskService(supabase_client=client)
        ok, result = service.supersede_contract("missing")

        assert ok is False
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# Tests: update_contract() cannot set negotiation_status='locked' directly
# ---------------------------------------------------------------------------


class TestUpdateContractLockedGuard:
    def test_setting_locked_via_update_is_rejected(self):
        draft_contract = _make_contract()
        client = MagicMock()

        contracts_table = MagicMock()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.execute.return_value = MagicMock(data=[draft_contract])
        contracts_table.select.return_value = select_chain
        client.table.return_value = contracts_table

        service = TaskService(supabase_client=client)
        ok, result = service.update_contract("contract-001", {"negotiation_status": "locked"})

        assert ok is False
        assert "lock_contract()" in result["error"]

    def test_setting_negotiated_via_update_is_allowed(self):
        draft_contract = _make_contract()
        updated_contract = _make_contract(negotiation_status="negotiated")
        client = MagicMock()

        contracts_table = MagicMock()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.execute.return_value = MagicMock(data=[draft_contract])
        contracts_table.select.return_value = select_chain

        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock(data=[updated_contract])
        contracts_table.update.return_value = update_chain
        client.table.return_value = contracts_table

        service = TaskService(supabase_client=client)
        ok, result = service.update_contract("contract-001", {"negotiation_status": "negotiated"})

        assert ok is True
        assert result["contract"]["negotiation_status"] == "negotiated"


# ---------------------------------------------------------------------------
# Tests: execute_transition() proposed → approved requires locked contract
# ---------------------------------------------------------------------------


class TestApprovalRequiresLockedContract:
    @pytest.mark.asyncio
    async def test_non_contract_task_passes_through_proposed_to_approved(self):
        """Tasks without current_contract_id are not contract-managed and pass through freely."""
        task = _make_task(status="proposed", current_contract_id=None)
        updated_task = _make_task(status="approved")
        client = _make_lifecycle_client(task, updated_task)
        service = TaskLifecycleService(supabase_client=client)

        ok, result = await service.execute_transition("task-001", "approved")

        assert ok is True
        assert result["transition"]["to"] == "approved"

    @pytest.mark.asyncio
    async def test_approved_blocked_when_contract_not_locked(self):
        draft_contract = _make_contract(locked_at=None, negotiation_status="draft")
        task = _make_task(status="proposed", current_contract_id="contract-001")
        client = _make_lifecycle_client(task, _make_task(status="approved"))

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            mock_ts = MagicMock()
            mock_ts.get_contract.return_value = (True, {"contract": draft_contract})
            mock_ts_class.return_value = mock_ts

            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.execute_transition("task-001", "approved")

        assert ok is False
        assert "locked" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_approved_succeeds_when_contract_is_locked(self):
        locked_contract = _make_contract(
            locked_at="2026-03-31T10:00:00", negotiation_status="locked"
        )
        task = _make_task(status="proposed", current_contract_id="contract-001")
        updated_task = _make_task(status="approved", current_contract_id="contract-001")
        client = _make_lifecycle_client(task, updated_task)

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            mock_ts = MagicMock()
            mock_ts.get_contract.return_value = (True, {"contract": locked_contract})
            mock_ts_class.return_value = mock_ts

            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.execute_transition("task-001", "approved")

        assert ok is True
        assert result["transition"]["to"] == "approved"

    @pytest.mark.asyncio
    async def test_approved_blocked_when_contract_fetch_fails(self):
        task = _make_task(status="proposed", current_contract_id="contract-001")
        client = _make_lifecycle_client(task, _make_task(status="approved"))

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            mock_ts = MagicMock()
            mock_ts.get_contract.return_value = (False, {"error": "DB error"})
            mock_ts_class.return_value = mock_ts

            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.execute_transition("task-001", "approved")

        assert ok is False
        assert "contract not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_non_proposed_to_approved_transition_skips_contract_check(self):
        """draft → approved skips the locked contract check (not a proposed→approved transition)."""
        task = _make_task(status="draft", current_contract_id=None)
        updated_task = _make_task(status="approved")
        client = _make_lifecycle_client(task, updated_task)

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.execute_transition("task-001", "approved")

        # draft → approved doesn't enforce contract lock
        assert ok is True
        mock_ts_class.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: execute_transition() retry with new_acceptance_criteria
# ---------------------------------------------------------------------------


class TestRetryContractRevision:
    @pytest.mark.parametrize("review_state", [
        "architect-review", "code-review", "qa-eval", "review", "failed"
    ])
    @pytest.mark.asyncio
    async def test_retry_with_new_ac_creates_new_draft(self, review_state):
        locked_contract = _make_contract(
            locked_at="2026-03-30T10:00:00", negotiation_status="locked"
        )
        task = _make_task(status=review_state, current_contract_id="contract-001")
        updated_task = _make_task(
            status="assigned",
            current_contract_id="contract-001",
            retry_count=1,
        )
        client = _make_lifecycle_client(task, updated_task)
        new_ac = [{"name": "AC-2", "description": "Must handle errors", "threshold": "100%"}]

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            mock_ts = MagicMock()
            mock_ts.get_contract.return_value = (True, {"contract": locked_contract})
            mock_ts.supersede_contract.return_value = (True, {"contract": {}})
            mock_ts.create_contract.return_value = (True, {"contract": {}})
            mock_ts_class.return_value = mock_ts

            reason = "Needs error handling" if review_state in {"architect-review", "code-review", "qa-eval", "review"} else None
            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.execute_transition(
                "task-001",
                "assigned",
                reason=reason,
                new_acceptance_criteria=new_ac,
            )

        assert ok is True
        mock_ts.supersede_contract.assert_called_once_with("contract-001")
        mock_ts.create_contract.assert_called_once()
        create_kwargs = mock_ts.create_contract.call_args[1]
        assert create_kwargs["acceptance_criteria"] == new_ac
        assert create_kwargs["source_stage"] == "retry"
        assert create_kwargs["supersedes_contract_id"] == "contract-001"
        assert create_kwargs["negotiation_status"] == "draft"

    @pytest.mark.asyncio
    async def test_retry_without_new_ac_keeps_existing_contract(self):
        task = _make_task(status="architect-review", current_contract_id="contract-001")
        updated_task = _make_task(status="assigned", current_contract_id="contract-001", retry_count=1)
        client = _make_lifecycle_client(task, updated_task)

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.execute_transition(
                "task-001",
                "assigned",
                reason="Try again",
                new_acceptance_criteria=None,  # No AC change
            )

        assert ok is True
        # Contract service should NOT have been called for contract revision
        mock_ts_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_contract_revision_failure_does_not_fail_transition(self):
        locked_contract = _make_contract(
            locked_at="2026-03-30T10:00:00", negotiation_status="locked"
        )
        task = _make_task(status="code-review", current_contract_id="contract-001")
        updated_task = _make_task(status="assigned", retry_count=1)
        client = _make_lifecycle_client(task, updated_task)
        new_ac = [{"name": "AC-new", "description": "New requirement", "threshold": None}]

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            mock_ts = MagicMock()
            mock_ts.get_contract.side_effect = RuntimeError("DB down")
            mock_ts_class.return_value = mock_ts

            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.execute_transition(
                "task-001",
                "assigned",
                reason="Retry",
                new_acceptance_criteria=new_ac,
            )

        # Transition succeeds even if contract revision fails
        assert ok is True
        assert result["transition"]["to"] == "assigned"


# ---------------------------------------------------------------------------
# Tests: re_plan() supersedes locked contract and creates new draft
# ---------------------------------------------------------------------------


class TestRePlanContractRevision:
    @pytest.mark.asyncio
    async def test_replan_supersedes_locked_contract_and_creates_draft(self):
        locked_contract = _make_contract(
            locked_at="2026-03-30T10:00:00", negotiation_status="locked"
        )
        task = _make_task(status="failed", current_contract_id="contract-001")
        updated_task = _make_task(status="planning", current_contract_id="contract-001")
        client = _make_lifecycle_client(task, updated_task)

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            mock_ts = MagicMock()
            mock_ts.get_contract.return_value = (True, {"contract": locked_contract})
            mock_ts.supersede_contract.return_value = (True, {"contract": {}})
            mock_ts.create_contract.return_value = (True, {"contract": {}})
            mock_ts_class.return_value = mock_ts

            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.re_plan("task-001", changed_by="Owner")

        assert ok is True
        mock_ts.supersede_contract.assert_called_once_with("contract-001")
        mock_ts.create_contract.assert_called_once()
        create_kwargs = mock_ts.create_contract.call_args[1]
        assert create_kwargs["source_stage"] == "re-plan"
        assert create_kwargs["supersedes_contract_id"] == "contract-001"
        assert create_kwargs["negotiation_status"] == "draft"
        # Inherits objective and AC from locked contract
        assert create_kwargs["objective"] == locked_contract["objective"]
        assert create_kwargs["acceptance_criteria"] == locked_contract["acceptance_criteria"]

    @pytest.mark.asyncio
    async def test_replan_skips_contract_revision_when_no_current_contract(self):
        task = _make_task(status="on-hold", current_contract_id=None)
        updated_task = _make_task(status="planning")
        client = _make_lifecycle_client(task, updated_task)

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.re_plan("task-001", changed_by="Owner")

        assert ok is True
        mock_ts_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_replan_skips_contract_revision_when_contract_is_draft(self):
        draft_contract = _make_contract(negotiation_status="draft")
        task = _make_task(status="planning", current_contract_id="contract-001")
        updated_task = _make_task(status="planning")
        client = _make_lifecycle_client(task, updated_task)

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            mock_ts = MagicMock()
            mock_ts.get_contract.return_value = (True, {"contract": draft_contract})
            mock_ts_class.return_value = mock_ts

            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.re_plan("task-001", changed_by="Owner")

        assert ok is True
        # Only fetch was called; no supersede or create
        mock_ts.supersede_contract.assert_not_called()
        mock_ts.create_contract.assert_not_called()

    @pytest.mark.asyncio
    async def test_replan_contract_revision_failure_does_not_fail_replan(self):
        task = _make_task(status="failed", current_contract_id="contract-001")
        updated_task = _make_task(status="planning")
        client = _make_lifecycle_client(task, updated_task)

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            mock_ts = MagicMock()
            mock_ts.get_contract.side_effect = RuntimeError("DB error")
            mock_ts_class.return_value = mock_ts

            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.re_plan("task-001", changed_by="Owner")

        assert ok is True
        assert result["transition"]["to"] == "planning"

    @pytest.mark.asyncio
    async def test_replan_never_mutates_locked_contract(self):
        """The supersede call marks old as superseded; locked contract itself is not updated."""
        locked_contract = _make_contract(
            locked_at="2026-03-30T10:00:00", negotiation_status="locked"
        )
        task = _make_task(status="escalated", current_contract_id="contract-001")
        updated_task = _make_task(status="planning")
        client = _make_lifecycle_client(task, updated_task)

        supersede_calls = []
        create_calls = []

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            mock_ts = MagicMock()
            mock_ts.get_contract.return_value = (True, {"contract": locked_contract})
            mock_ts.supersede_contract.side_effect = lambda cid: supersede_calls.append(cid) or (True, {})
            mock_ts.create_contract.side_effect = lambda **kw: create_calls.append(kw) or (True, {})
            mock_ts_class.return_value = mock_ts

            service = TaskLifecycleService(supabase_client=client)
            await service.re_plan("task-001", changed_by="Owner")

        # Exactly one supersede (the old contract) and one create (the new draft)
        assert supersede_calls == ["contract-001"]
        assert len(create_calls) == 1
        # The new draft is a NEW contract (different supersedes_contract_id linkage)
        assert create_calls[0]["supersedes_contract_id"] == "contract-001"
