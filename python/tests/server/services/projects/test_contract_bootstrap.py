"""Tests for auto-draft contract bootstrap logic.

Covers:
- TaskService.bootstrap_contract_draft(): AC source priority (plan_item > task_field > description)
- _parse_ac_bullets() and _normalize_raw_ac() helpers
- create_task() triggers bootstrap when status == 'proposed'
- execute_transition() triggers bootstrap when transitioning to 'proposed'
"""

from unittest.mock import MagicMock, patch

import pytest

from src.server.services.projects.task_lifecycle_service import TaskLifecycleService
from src.server.services.projects.task_service import (
    TaskService,
    _normalize_raw_ac,
    _parse_ac_bullets,
)

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
        "title": "Implement login",
        "description": "Some description",
        "status": "proposed",
        "acceptance_criteria": [],
        "plan_item_id": None,
        "current_contract_id": None,
        "allowed_paths": [],
        "state_history": [],
        "retry_count": 0,
    }
    base.update(overrides)
    return base


def _mock_client_for_task_service(task_data=None):
    """Return a minimal mock supabase client that satisfies TaskService needs."""
    client = MagicMock()

    # archon_tasks select (task existence check in create_contract)
    tasks_table = MagicMock()
    tasks_select = MagicMock()
    tasks_select.eq.return_value = tasks_select
    tasks_select.execute.return_value = MagicMock(data=[{"id": "task-001"}])
    tasks_table.select.return_value = tasks_select

    # archon_task_contracts select (version lookup)
    contracts_select = MagicMock()
    contracts_select.eq.return_value = contracts_select
    contracts_select.order.return_value = contracts_select
    contracts_select.limit.return_value = contracts_select
    contracts_select.execute.return_value = MagicMock(data=[])  # no existing contracts

    # archon_task_contracts insert
    contracts_insert = MagicMock()
    contracts_insert.execute.return_value = MagicMock(data=[{
        "id": "contract-new",
        "task_id": "task-001",
        "version": 1,
        "objective": "Implement login",
        "acceptance_criteria": [],
    }])

    contracts_table = MagicMock()
    contracts_table.select.return_value = contracts_select
    contracts_table.insert.return_value = contracts_insert

    # archon_tasks update (set current_contract_id)
    tasks_update = MagicMock()
    tasks_update.eq.return_value = tasks_update
    tasks_update.execute.return_value = MagicMock(data=[])
    tasks_table.update.return_value = tasks_update

    # project_implementation_items select (plan item lookup)
    items_select = MagicMock()
    items_select.eq.return_value = items_select
    items_select.maybe_single.return_value = items_select
    items_select.execute.return_value = MagicMock(data=None)
    items_table = MagicMock()
    items_table.select.return_value = items_select

    def _table_router(name):
        if name == "archon_tasks":
            return tasks_table
        if name == "archon_task_contracts":
            return contracts_table
        if name == "project_implementation_items":
            return items_table
        return MagicMock()

    client.table.side_effect = _table_router
    return client


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


class TestParseAcBullets:
    def test_extracts_bullets_from_ac_section(self):
        text = "Some description.\n\nAcceptance Criteria:\n- User can log in\n- Token expires in 1h"
        result = _parse_ac_bullets(text)
        assert len(result) == 2
        assert result[0] == {"name": "AC-1", "description": "User can log in", "threshold": None}
        assert result[1] == {"name": "AC-2", "description": "Token expires in 1h", "threshold": None}

    def test_case_insensitive_header(self):
        text = "acceptance criteria:\n- Do X\n- Do Y"
        result = _parse_ac_bullets(text)
        assert len(result) == 2

    def test_returns_empty_when_no_ac_section(self):
        text = "Just a description without any AC."
        result = _parse_ac_bullets(text)
        assert result == []

    def test_stops_at_new_section_header(self):
        text = "Acceptance Criteria:\n- Do X\n# New Section\n- Should not be included"
        result = _parse_ac_bullets(text)
        assert len(result) == 1
        assert result[0]["description"] == "Do X"

    def test_empty_text(self):
        assert _parse_ac_bullets("") == []

    def test_asterisk_bullets_accepted(self):
        text = "Acceptance Criteria:\n* First criterion\n* Second criterion"
        result = _parse_ac_bullets(text)
        assert len(result) == 2


class TestNormalizeRawAc:
    def test_string_items(self):
        result = _normalize_raw_ac(["Do X", "Do Y"])
        assert result == [
            {"name": "AC-1", "description": "Do X", "threshold": None},
            {"name": "AC-2", "description": "Do Y", "threshold": None},
        ]

    def test_dict_items_with_name_description(self):
        raw = [{"name": "Login works", "description": "User can authenticate", "threshold": "100% pass"}]
        result = _normalize_raw_ac(raw)
        assert result == [{"name": "Login works", "description": "User can authenticate", "threshold": "100% pass"}]

    def test_dict_items_without_name_uses_index(self):
        raw = [{"description": "Do something"}]
        result = _normalize_raw_ac(raw)
        assert result[0]["name"] == "AC-1"

    def test_empty_strings_skipped(self):
        result = _normalize_raw_ac(["", "   "])
        assert result == []

    def test_empty_list(self):
        assert _normalize_raw_ac([]) == []

    def test_mixed_types(self):
        raw = ["String criterion", {"name": "Dict criterion", "description": "Details"}]
        result = _normalize_raw_ac(raw)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Unit tests: bootstrap_contract_draft()
# ---------------------------------------------------------------------------


class TestBootstrapContractDraft:
    def test_skips_when_contract_already_set(self):
        client = MagicMock()
        service = TaskService(supabase_client=client)
        task = _make_task(current_contract_id="existing-contract-id")

        service.bootstrap_contract_draft(task)

        # No contract creation calls should have happened
        client.table.assert_not_called()

    def test_uses_task_acceptance_criteria_field(self):
        client = _mock_client_for_task_service()
        service = TaskService(supabase_client=client)
        task = _make_task(acceptance_criteria=["User can log in", "Token expires"])

        with patch.object(service, "create_contract", return_value=(True, {"contract": {}})) as mock_create:
            service.bootstrap_contract_draft(task)

        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert kwargs.get("source_type") == "task_field"
        assert len(kwargs.get("acceptance_criteria", [])) == 2

    def test_falls_back_to_description_extraction(self):
        client = _mock_client_for_task_service()
        service = TaskService(supabase_client=client)
        task = _make_task(
            acceptance_criteria=[],
            description="Something.\n\nAcceptance Criteria:\n- Do X\n- Do Y",
        )

        with patch.object(service, "create_contract", return_value=(True, {"contract": {}})) as mock_create:
            service.bootstrap_contract_draft(task)

        _, kwargs = mock_create.call_args
        assert kwargs.get("source_type") == "description_extraction"
        assert len(kwargs.get("acceptance_criteria", [])) == 2

    def test_uses_plan_item_ac_when_available(self):
        client = _mock_client_for_task_service()

        # Set up plan item to return a description with AC
        items_select = MagicMock()
        items_select.eq.return_value = items_select
        items_select.maybe_single.return_value = items_select
        items_select.execute.return_value = MagicMock(data={
            "description": "Plan item desc.\n\nAcceptance Criteria:\n- Plan AC 1\n- Plan AC 2"
        })
        items_table = MagicMock()
        items_table.select.return_value = items_select
        client.table.side_effect = lambda name: items_table if name == "project_implementation_items" else MagicMock()

        service = TaskService(supabase_client=client)
        task = _make_task(
            plan_item_id="item-001",
            acceptance_criteria=["Task AC (lower priority)"],
        )

        with patch.object(service, "create_contract", return_value=(True, {"contract": {}})) as mock_create:
            service.bootstrap_contract_draft(task)

        _, kwargs = mock_create.call_args
        assert kwargs.get("source_type") == "plan_item"
        assert len(kwargs.get("acceptance_criteria", [])) == 2

    def test_sets_correct_metadata_fields(self):
        client = _mock_client_for_task_service()
        service = TaskService(supabase_client=client)
        task = _make_task(
            title="Implement auth",
            allowed_paths=["src/auth/"],
        )

        with patch.object(service, "create_contract", return_value=(True, {"contract": {}})) as mock_create:
            service.bootstrap_contract_draft(task)

        _, kwargs = mock_create.call_args
        assert kwargs["task_id"] == "task-001"
        assert kwargs["objective"] == "Implement auth"
        assert kwargs["in_scope_paths"] == ["src/auth/"]
        assert kwargs["negotiated_by"] == "system"
        assert kwargs["source_stage"] == "proposed"
        assert kwargs["negotiation_status"] == "draft"

    def test_logs_warning_on_create_contract_failure(self, caplog):
        import logging
        client = _mock_client_for_task_service()
        service = TaskService(supabase_client=client)
        task = _make_task()

        with patch.object(service, "create_contract", return_value=(False, {"error": "DB error"})):
            with caplog.at_level(logging.WARNING):
                service.bootstrap_contract_draft(task)

        assert any("bootstrap_contract_draft failed" in r.message for r in caplog.records)

    def test_skips_when_no_task_id(self):
        client = MagicMock()
        service = TaskService(supabase_client=client)
        task = {"title": "No ID task"}  # missing "id"

        service.bootstrap_contract_draft(task)  # must not raise
        client.table.assert_not_called()


# ---------------------------------------------------------------------------
# Integration-style tests: execute_transition → bootstrap
# ---------------------------------------------------------------------------


class TestExecuteTransitionBootstrap:
    def _make_lifecycle_client(self, initial_task, updated_task):
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

    @pytest.mark.asyncio
    async def test_bootstrap_called_on_proposed_transition(self):
        initial = _make_task(status="draft")
        updated = _make_task(status="proposed", current_contract_id=None)
        client = self._make_lifecycle_client(initial, updated)

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            mock_ts_instance = MagicMock()
            mock_ts_class.return_value = mock_ts_instance

            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.execute_transition("task-001", "proposed")

        assert ok is True
        mock_ts_class.assert_called_once_with(client)
        mock_ts_instance.bootstrap_contract_draft.assert_called_once_with(updated)

    @pytest.mark.asyncio
    async def test_no_bootstrap_when_contract_already_exists(self):
        initial = _make_task(status="draft")
        updated = _make_task(status="proposed", current_contract_id="existing-contract")
        client = self._make_lifecycle_client(initial, updated)

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            service = TaskLifecycleService(supabase_client=client)
            ok, _ = await service.execute_transition("task-001", "proposed")

        assert ok is True
        mock_ts_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_bootstrap_for_non_proposed_transitions(self):
        # approved → planning does not trigger bootstrap (only proposed triggers bootstrap)
        initial = _make_task(status="approved")
        updated = _make_task(status="planning")
        client = self._make_lifecycle_client(initial, updated)

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            service = TaskLifecycleService(supabase_client=client)
            ok, _ = await service.execute_transition("task-001", "planning")

        assert ok is True
        mock_ts_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_bootstrap_failure_does_not_fail_transition(self):
        initial = _make_task(status="draft")
        updated = _make_task(status="proposed", current_contract_id=None)
        client = self._make_lifecycle_client(initial, updated)

        with patch(
            "src.server.services.projects.task_service.TaskService"
        ) as mock_ts_class:
            mock_ts_instance = MagicMock()
            mock_ts_instance.bootstrap_contract_draft.side_effect = RuntimeError("DB down")
            mock_ts_class.return_value = mock_ts_instance

            service = TaskLifecycleService(supabase_client=client)
            ok, result = await service.execute_transition("task-001", "proposed")

        # Transition succeeds even if bootstrap fails
        assert ok is True
        assert result["transition"]["to"] == "proposed"
