"""Tests for PlanService CRUD operations."""

from unittest.mock import MagicMock

import pytest

from src.server.services.projects.plan_service import PlanService


def _make_client(data=None, error=None):
    """Build a mock Supabase client with a chainable query builder."""
    client = MagicMock()
    execute_result = MagicMock()
    execute_result.data = data

    chain = MagicMock()
    chain.execute.return_value = execute_result
    chain.eq.return_value = chain
    chain.select.return_value = chain
    chain.insert.return_value = chain
    chain.update.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.maybe_single.return_value = chain

    if error:
        chain.execute.side_effect = error

    client.table.return_value = chain
    return client, chain


# ── Plans ─────────────────────────────────────────────────────────────────────


class TestListPlans:
    def test_returns_plans(self):
        rows = [{"id": "pl-1", "project_id": "proj-1", "title": "Plan A"}]
        client, _ = _make_client(data=rows)
        svc = PlanService(supabase_client=client)
        ok, result = svc.list_plans("proj-1")
        assert ok is True
        assert result["plans"] == rows
        assert result["total_count"] == 1

    def test_returns_empty_when_none(self):
        client, _ = _make_client(data=None)
        svc = PlanService(supabase_client=client)
        ok, result = svc.list_plans("proj-1")
        assert ok is True
        assert result["plans"] == []

    def test_returns_error_on_exception(self):
        client, _ = _make_client(error=RuntimeError("db error"))
        svc = PlanService(supabase_client=client)
        ok, result = svc.list_plans("proj-1")
        assert ok is False
        assert "db error" in result["error"]


class TestGetPlan:
    def test_returns_plan_when_found(self):
        row = {"id": "pl-1", "project_id": "proj-1", "title": "Plan A"}
        client, _ = _make_client(data=row)
        svc = PlanService(supabase_client=client)
        ok, result = svc.get_plan("pl-1")
        assert ok is True
        assert result["plan"] == row

    def test_returns_error_when_not_found(self):
        client, _ = _make_client(data=None)
        svc = PlanService(supabase_client=client)
        ok, result = svc.get_plan("pl-missing")
        assert ok is False
        assert "not found" in result["error"]

    def test_returns_error_on_exception(self):
        client, _ = _make_client(error=RuntimeError("connection refused"))
        svc = PlanService(supabase_client=client)
        ok, result = svc.get_plan("pl-1")
        assert ok is False
        assert "connection refused" in result["error"]


class TestCreatePlan:
    def test_creates_plan_successfully(self):
        row = {"id": "pl-new", "project_id": "proj-1", "title": "My Plan", "status": "draft"}
        client, _ = _make_client(data=[row])
        svc = PlanService(supabase_client=client)
        ok, result = svc.create_plan(project_id="proj-1", title="My Plan")
        assert ok is True
        assert result["plan"] == row

    def test_fails_without_project_id(self):
        client, _ = _make_client(data=None)
        svc = PlanService(supabase_client=client)
        ok, result = svc.create_plan(project_id="", title="My Plan")
        assert ok is False
        assert "project_id" in result["error"]

    def test_fails_without_title(self):
        client, _ = _make_client(data=None)
        svc = PlanService(supabase_client=client)
        ok, result = svc.create_plan(project_id="proj-1", title="")
        assert ok is False
        assert "title" in result["error"]

    def test_fails_when_insert_returns_nothing(self):
        client, _ = _make_client(data=[])
        svc = PlanService(supabase_client=client)
        ok, result = svc.create_plan(project_id="proj-1", title="My Plan")
        assert ok is False
        assert "Failed to create plan" in result["error"]


class TestUpdatePlan:
    def _make_update_svc(self, get_data, update_data):
        """Service whose get_plan returns get_data and update returns update_data."""
        client = MagicMock()
        get_result = MagicMock()
        get_result.data = get_data

        update_result = MagicMock()
        update_result.data = update_data

        get_chain = MagicMock()
        get_chain.execute.return_value = get_result
        get_chain.eq.return_value = get_chain
        get_chain.select.return_value = get_chain
        get_chain.maybe_single.return_value = get_chain

        update_chain = MagicMock()
        update_chain.execute.return_value = update_result
        update_chain.eq.return_value = update_chain
        update_chain.update.return_value = update_chain

        # First call to .table() → get chain; subsequent → update chain
        call_count = {"n": 0}

        def table_side_effect(name):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return get_chain
            return update_chain

        client.table.side_effect = table_side_effect
        return PlanService(supabase_client=client)

    def test_updates_plan_successfully(self):
        existing = {"id": "pl-1", "title": "Old Title"}
        updated = {"id": "pl-1", "title": "New Title"}
        svc = self._make_update_svc(get_data=existing, update_data=[updated])
        ok, result = svc.update_plan("pl-1", title="New Title")
        assert ok is True
        assert result["plan"]["title"] == "New Title"

    def test_returns_error_when_plan_not_found(self):
        svc = self._make_update_svc(get_data=None, update_data=None)
        ok, result = svc.update_plan("pl-missing", title="X")
        assert ok is False
        assert "not found" in result["error"]


# ── Phases ────────────────────────────────────────────────────────────────────


class TestCreatePhase:
    def _make_phase_svc(self, plan_data, phase_data):
        client = MagicMock()
        plan_result = MagicMock()
        plan_result.data = plan_data

        phase_result = MagicMock()
        phase_result.data = phase_data

        plan_chain = MagicMock()
        plan_chain.execute.return_value = plan_result
        plan_chain.eq.return_value = plan_chain
        plan_chain.select.return_value = plan_chain
        plan_chain.maybe_single.return_value = plan_chain

        phase_chain = MagicMock()
        phase_chain.execute.return_value = phase_result
        phase_chain.insert.return_value = phase_chain

        call_count = {"n": 0}

        def table_side_effect(name):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return plan_chain
            return phase_chain

        client.table.side_effect = table_side_effect
        return PlanService(supabase_client=client)

    def test_creates_phase_successfully(self):
        plan = {"id": "pl-1", "title": "Plan"}
        phase = {"id": "ph-1", "plan_id": "pl-1", "title": "Phase 1"}
        svc = self._make_phase_svc(plan_data=plan, phase_data=[phase])
        ok, result = svc.create_phase("pl-1", title="Phase 1")
        assert ok is True
        assert result["phase"] == phase

    def test_fails_when_plan_not_found(self):
        svc = self._make_phase_svc(plan_data=None, phase_data=None)
        ok, result = svc.create_phase("pl-missing", title="Phase 1")
        assert ok is False
        assert "not found" in result["error"]

    def test_fails_without_title(self):
        plan = {"id": "pl-1", "title": "Plan"}
        svc = self._make_phase_svc(plan_data=plan, phase_data=None)
        ok, result = svc.create_phase("pl-1", title="")
        assert ok is False
        assert "title" in result["error"]


# ── Items ─────────────────────────────────────────────────────────────────────


class TestCreateItem:
    def _make_item_svc(self, plan_data, item_data):
        client = MagicMock()
        plan_result = MagicMock()
        plan_result.data = plan_data

        item_result = MagicMock()
        item_result.data = item_data

        plan_chain = MagicMock()
        plan_chain.execute.return_value = plan_result
        plan_chain.eq.return_value = plan_chain
        plan_chain.select.return_value = plan_chain
        plan_chain.maybe_single.return_value = plan_chain

        item_chain = MagicMock()
        item_chain.execute.return_value = item_result
        item_chain.insert.return_value = item_chain

        call_count = {"n": 0}

        def table_side_effect(name):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return plan_chain
            return item_chain

        client.table.side_effect = table_side_effect
        return PlanService(supabase_client=client)

    def test_creates_item_successfully(self):
        plan = {"id": "pl-1", "title": "Plan"}
        item = {"id": "it-1", "plan_id": "pl-1", "title": "Item A", "status": "planned"}
        svc = self._make_item_svc(plan_data=plan, item_data=[item])
        ok, result = svc.create_item("pl-1", title="Item A")
        assert ok is True
        assert result["item"]["status"] == "planned"

    def test_fails_when_plan_not_found(self):
        svc = self._make_item_svc(plan_data=None, item_data=None)
        ok, result = svc.create_item("pl-missing", title="Item A")
        assert ok is False
        assert "not found" in result["error"]


# ── Dependencies ──────────────────────────────────────────────────────────────


class TestCreateDependency:
    def _make_dep_svc(self, item1_data, item2_data, dep_data=None, dep_error=None):
        client = MagicMock()

        def make_chain(data, error=None):
            result = MagicMock()
            result.data = data
            chain = MagicMock()
            chain.execute.return_value = result
            chain.eq.return_value = chain
            chain.select.return_value = chain
            chain.insert.return_value = chain
            chain.maybe_single.return_value = chain
            if error:
                chain.execute.side_effect = error
            return chain

        item1_chain = make_chain(item1_data)
        item2_chain = make_chain(item2_data)
        dep_chain = make_chain(dep_data, error=dep_error)

        call_count = {"n": 0}

        def table_side_effect(name):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return item1_chain
            if call_count["n"] == 2:
                return item2_chain
            return dep_chain

        client.table.side_effect = table_side_effect
        return PlanService(supabase_client=client)

    def test_creates_dependency_successfully(self):
        item1 = {"id": "it-1", "title": "Item 1"}
        item2 = {"id": "it-2", "title": "Item 2"}
        dep = {"id": "dep-1", "dependent_id": "it-1", "dependency_id": "it-2", "dependency_type": "blocks"}
        svc = self._make_dep_svc(item1_data=item1, item2_data=item2, dep_data=[dep])
        ok, result = svc.create_dependency("it-1", dependency_id="it-2")
        assert ok is True
        assert result["dependency"]["dependency_type"] == "blocks"

    def test_fails_when_same_item(self):
        item = {"id": "it-1", "title": "Item 1"}
        svc = self._make_dep_svc(item1_data=item, item2_data=item)
        ok, result = svc.create_dependency("it-1", dependency_id="it-1")
        assert ok is False
        assert "cannot depend on itself" in result["error"]

    def test_fails_when_dependent_not_found(self):
        svc = self._make_dep_svc(item1_data=None, item2_data=None)
        ok, result = svc.create_dependency("it-missing", dependency_id="it-2")
        assert ok is False
        assert "not found" in result["error"]

    def test_fails_when_dependency_not_found(self):
        item1 = {"id": "it-1", "title": "Item 1"}
        svc = self._make_dep_svc(item1_data=item1, item2_data=None)
        ok, result = svc.create_dependency("it-1", dependency_id="it-missing")
        assert ok is False
        assert "not found" in result["error"]


# ── Task Links ────────────────────────────────────────────────────────────────


def _make_two_table_client(first_data, second_data):
    """Build a client where the first table() call returns first_data and subsequent calls return second_data."""
    client = MagicMock()

    def _chain(data):
        result = MagicMock()
        result.data = data
        chain = MagicMock()
        chain.execute.return_value = result
        chain.eq.return_value = chain
        chain.select.return_value = chain
        chain.insert.return_value = chain
        chain.delete.return_value = chain
        chain.update.return_value = chain
        chain.order.return_value = chain
        chain.maybe_single.return_value = chain
        chain.in_.return_value = chain
        return chain

    first_chain = _chain(first_data)
    second_chain = _chain(second_data)
    call_count = {"n": 0}

    def side_effect(name):
        call_count["n"] += 1
        return first_chain if call_count["n"] == 1 else second_chain

    client.table.side_effect = side_effect
    return client


class TestListTaskLinks:
    def test_returns_links(self):
        item = {"id": "it-1", "title": "Item"}
        links = [{"id": "lk-1", "item_id": "it-1", "task_id": "t-1", "link_type": "implements"}]
        client = _make_two_table_client(first_data=item, second_data=links)
        svc = PlanService(supabase_client=client)
        ok, result = svc.list_task_links("it-1")
        assert ok is True
        assert result["links"] == links
        assert result["total_count"] == 1

    def test_returns_error_when_item_not_found(self):
        client, _ = _make_client(data=None)
        svc = PlanService(supabase_client=client)
        ok, result = svc.list_task_links("it-missing")
        assert ok is False
        assert "not found" in result["error"]


class TestCreateTaskLink:
    def test_creates_link_successfully(self):
        item = {"id": "it-1", "title": "Item"}
        link = {"id": "lk-new", "item_id": "it-1", "task_id": "t-1", "link_type": "implements"}
        client = _make_two_table_client(first_data=item, second_data=[link])
        svc = PlanService(supabase_client=client)
        ok, result = svc.create_task_link("it-1", task_id="t-1")
        assert ok is True
        assert result["link"] == link

    def test_fails_when_item_not_found(self):
        client, _ = _make_client(data=None)
        svc = PlanService(supabase_client=client)
        ok, result = svc.create_task_link("it-missing", task_id="t-1")
        assert ok is False
        assert "not found" in result["error"]

    def test_fails_without_task_id(self):
        item = {"id": "it-1", "title": "Item"}
        client, _ = _make_client(data=item)
        svc = PlanService(supabase_client=client)
        ok, result = svc.create_task_link("it-1", task_id="")
        assert ok is False
        assert "task_id" in result["error"]


class TestDeleteTaskLink:
    def test_deletes_link_successfully(self):
        link = {"id": "lk-1", "item_id": "it-1", "task_id": "t-1", "link_type": "implements"}
        client = _make_two_table_client(first_data=link, second_data=None)
        svc = PlanService(supabase_client=client)
        ok, result = svc.delete_task_link("lk-1")
        assert ok is True
        assert result["deleted"] == "lk-1"

    def test_fails_when_link_not_found(self):
        client, _ = _make_client(data=None)
        svc = PlanService(supabase_client=client)
        ok, result = svc.delete_task_link("lk-missing")
        assert ok is False
        assert "not found" in result["error"]


# ── Auto-link Parser ──────────────────────────────────────────────────────────


class TestRunRefLinkParser:
    def _make_parser_client(self, tasks, item_data, update_data=None, link_data=None):
        """Build a mock client for the auto-link parser."""
        client = MagicMock()
        calls = {"n": 0}

        def _chain(data):
            result = MagicMock()
            result.data = data
            chain = MagicMock()
            chain.execute.return_value = result
            chain.eq.return_value = chain
            chain.select.return_value = chain
            chain.insert.return_value = chain
            chain.update.return_value = chain
            chain.order.return_value = chain
            chain.maybe_single.return_value = chain
            chain.in_.return_value = chain
            chain.limit.return_value = chain
            return chain

        tasks_chain = _chain(tasks)
        item_chain = _chain(item_data)
        update_chain = _chain(update_data or [])
        link_chain = _chain(link_data or [{"id": "lk-new"}])

        def side_effect(name):
            calls["n"] += 1
            if name == "archon_tasks" and calls["n"] == 1:
                return tasks_chain
            if name == "project_implementation_items":
                return item_chain
            if name == "archon_tasks":
                return update_chain
            if name == "project_implementation_item_task_links":
                return link_chain
            return _chain(None)

        client.table.side_effect = side_effect
        return client

    def test_links_task_with_ref_pattern(self):
        tasks = [{"id": "t-1", "description": "Ref: B-P2-03 some text", "plan_item_id": None}]
        item = {"id": "it-1", "item_key": "B-P2-03", "title": "Plan item", "status": "planned"}
        client = self._make_parser_client(tasks=tasks, item_data=item)
        svc = PlanService(supabase_client=client)
        ok, result = svc.run_ref_link_parser()
        assert ok is True
        assert result["scanned"] == 1
        assert result["linked"] == 1
        assert result["skipped"] == 0

    def test_skips_tasks_without_ref(self):
        tasks = [{"id": "t-1", "description": "No ref here", "plan_item_id": None}]
        client, _ = _make_client(data=tasks)
        svc = PlanService(supabase_client=client)
        ok, result = svc.run_ref_link_parser()
        assert ok is True
        assert result["scanned"] == 1
        assert result["skipped"] == 1
        assert result["linked"] == 0

    def test_skips_when_item_key_not_found(self):
        tasks = [{"id": "t-1", "description": "Ref: UNKNOWN-001", "plan_item_id": None}]
        item_chain_result = MagicMock()
        item_chain_result.data = None  # item not found

        client = MagicMock()
        calls = {"n": 0}

        def _chain(data):
            result = MagicMock()
            result.data = data
            chain = MagicMock()
            chain.execute.return_value = result
            chain.eq.return_value = chain
            chain.select.return_value = chain
            chain.order.return_value = chain
            chain.maybe_single.return_value = chain
            chain.limit.return_value = chain
            return chain

        tasks_chain = _chain(tasks)
        item_chain = _chain(None)

        def side_effect(name):
            calls["n"] += 1
            if calls["n"] == 1:
                return tasks_chain
            return item_chain

        client.table.side_effect = side_effect
        svc = PlanService(supabase_client=client)
        ok, result = svc.run_ref_link_parser()
        assert ok is True
        assert result["skipped"] == 1
        assert result["linked"] == 0
