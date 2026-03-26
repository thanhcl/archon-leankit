"""Tests for PlanRollupService."""

from unittest.mock import MagicMock, call

import pytest

from src.server.services.projects.plan_rollup_service import (
    ITEM_PROGRESS_WEIGHTS,
    PlanRollupService,
    _aggregate_progress,
    _item_progress,
    _merge_status_distribution,
    _sum_cost,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _chain(data=None, error=None):
    """Return a mock Supabase query chain that yields ``data`` on execute()."""
    c = MagicMock()
    result = MagicMock()
    result.data = data
    if error:
        c.execute.side_effect = error
    else:
        c.execute.return_value = result
    for method in ("select", "eq", "in_", "order", "limit", "maybe_single"):
        getattr(c, method).return_value = c
    return c


def _client_returning(data=None, error=None):
    """Return a mock Supabase client whose table() always returns the same chain."""
    client = MagicMock()
    client.table.return_value = _chain(data=data, error=error)
    return client


def _multi_client(table_responses: dict):
    """Return a mock client where each table name returns a configured chain.

    ``table_responses`` maps table name → list of data dicts (returned in order).
    """
    client = MagicMock()

    def _table_side_effect(name):
        seq = table_responses.get(name, [[]])
        if not isinstance(seq, list) or not seq:
            return _chain(data=[])
        data = seq.pop(0) if seq else []
        return _chain(data=data)

    client.table.side_effect = _table_side_effect
    return client


# ── Unit: pure helpers ─────────────────────────────────────────────────────────


class TestItemProgress:
    def test_known_statuses(self):
        assert _item_progress("planned") == 0.0
        assert _item_progress("ready") == 0.05
        assert _item_progress("in_progress") == 0.50
        assert _item_progress("blocked") == 0.50
        assert _item_progress("review") == 0.90
        assert _item_progress("done") == 1.00
        assert _item_progress("deferred") == 0.0
        assert _item_progress("cancelled") == 0.0

    def test_unknown_status_returns_zero(self):
        assert _item_progress("unknown_status") == 0.0


class TestAggregateProgress:
    def test_all_done(self):
        items = [{"status": "done"}, {"status": "done"}]
        assert _aggregate_progress(items) == 100.0

    def test_all_planned(self):
        items = [{"status": "planned"}, {"status": "planned"}]
        assert _aggregate_progress(items) == 0.0

    def test_mixed(self):
        items = [{"status": "planned"}, {"status": "done"}]
        # (0.0 + 1.0) / 2 * 100 = 50.0
        assert _aggregate_progress(items) == 50.0

    def test_excludes_deferred_and_cancelled(self):
        items = [{"status": "done"}, {"status": "deferred"}, {"status": "cancelled"}]
        # Only "done" is active: 1.0 / 1 * 100 = 100.0
        assert _aggregate_progress(items) == 100.0

    def test_all_excluded_returns_zero(self):
        items = [{"status": "deferred"}, {"status": "cancelled"}]
        assert _aggregate_progress(items) == 0.0

    def test_empty_returns_zero(self):
        assert _aggregate_progress([]) == 0.0


class TestMergeStatusDistribution:
    def test_merges_correctly(self):
        dists = [{"todo": 2, "done": 1}, {"todo": 1, "doing": 3}]
        result = _merge_status_distribution(dists)
        assert result == {"todo": 3, "done": 1, "doing": 3}

    def test_empty(self):
        assert _merge_status_distribution([]) == {}


class TestSumCost:
    def test_sums_known_values(self):
        assert _sum_cost([1.0, 2.5, 0.5]) == pytest.approx(4.0)

    def test_ignores_none(self):
        assert _sum_cost([1.0, None, 2.0]) == pytest.approx(3.0)

    def test_all_none_returns_none(self):
        assert _sum_cost([None, None]) is None

    def test_empty_returns_none(self):
        assert _sum_cost([]) is None


# ── Integration: PlanRollupService ────────────────────────────────────────────


class TestGetItemRollup:
    def _build_client(self, item_data, links_data, tasks_data, runs_data):
        """Build a client with ordered responses per table."""
        client = MagicMock()
        responses = {
            "project_implementation_items": [item_data, links_data],
            "project_implementation_item_task_links": [links_data],
            "archon_tasks": [tasks_data],
            "archon_execution_runs": [runs_data],
        }
        # We need finer control — use a side_effect counter per table.
        table_calls: dict[str, list] = {}

        def _table(name):
            if name not in table_calls:
                table_calls[name] = list(responses.get(name, []))
            seq = table_calls[name]
            data = seq.pop(0) if seq else []
            return _chain(data=data)

        client.table.side_effect = _table
        return client

    def test_item_not_found(self):
        client = _client_returning(data=None)
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_item_rollup("item-missing")
        assert ok is False
        assert "not found" in result["error"]

    def test_item_with_no_tasks(self):
        item = {"id": "i-1", "plan_id": "p-1", "phase_id": None, "title": "T", "status": "planned", "item_key": "K-1", "item_order": 1}
        client = self._build_client(
            item_data=item,
            links_data=[],
            tasks_data=[],
            runs_data=[],
        )
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_item_rollup("i-1")
        assert ok is True
        r = result["rollup"]
        assert r["item_id"] == "i-1"
        assert r["task_count"] == 0
        assert r["run_count"] == 0
        assert r["cost_usd"] is None
        assert r["progress_percent"] == 0.0
        assert r["status_distribution"] == {}

    def test_item_done_has_100_percent(self):
        item = {"id": "i-1", "plan_id": "p-1", "phase_id": None, "title": "T", "status": "done", "item_key": None, "item_order": 1}
        client = self._build_client(
            item_data=item,
            links_data=[],
            tasks_data=[],
            runs_data=[],
        )
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_item_rollup("i-1")
        assert ok is True
        assert result["rollup"]["progress_percent"] == 100.0

    def test_item_with_tasks_and_runs(self):
        item = {"id": "i-1", "plan_id": "p-1", "phase_id": None, "title": "T", "status": "in_progress", "item_key": "K-2", "item_order": 2}
        links = [{"item_id": "i-1", "task_id": "t-1"}, {"item_id": "i-1", "task_id": "t-2"}]
        tasks = [{"id": "t-1", "status": "doing"}, {"id": "t-2", "status": "done"}]
        runs = [{"task_id": "t-1", "cost_usd": 0.05}, {"task_id": "t-1", "cost_usd": 0.03}, {"task_id": "t-2", "cost_usd": None}]
        client = self._build_client(item_data=item, links_data=links, tasks_data=tasks, runs_data=runs)
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_item_rollup("i-1")
        assert ok is True
        r = result["rollup"]
        assert r["task_count"] == 2
        assert r["run_count"] == 3
        assert r["cost_usd"] == pytest.approx(0.08)
        assert r["status_distribution"] == {"doing": 1, "done": 1}
        assert r["progress_percent"] == 50.0  # in_progress weight

    def test_cost_usd_none_when_all_runs_lack_cost(self):
        item = {"id": "i-1", "plan_id": "p-1", "phase_id": None, "title": "T", "status": "review", "item_key": None, "item_order": 1}
        links = [{"item_id": "i-1", "task_id": "t-1"}]
        tasks = [{"id": "t-1", "status": "review"}]
        runs = [{"task_id": "t-1", "cost_usd": None}]
        client = self._build_client(item_data=item, links_data=links, tasks_data=tasks, runs_data=runs)
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_item_rollup("i-1")
        assert ok is True
        assert result["rollup"]["cost_usd"] is None

    def test_db_error_returns_false(self):
        client = _client_returning(error=RuntimeError("db down"))
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_item_rollup("i-1")
        assert ok is False
        assert "db down" in result["error"]


class TestGetPhasRollup:
    def _make_client(self, phase_data, items_data, links_data, tasks_data, runs_data):
        table_calls: dict[str, list] = {
            "project_implementation_phases": [phase_data],
            "project_implementation_items": [items_data],
            "project_implementation_item_task_links": [links_data],
            "archon_tasks": [tasks_data],
            "archon_execution_runs": [runs_data],
        }

        client = MagicMock()

        def _table(name):
            seq = table_calls.get(name, [])
            data = seq.pop(0) if seq else []
            return _chain(data=data)

        client.table.side_effect = _table
        return client

    def test_phase_not_found(self):
        client = _client_returning(data=None)
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_phase_rollup("plan-1", "phase-missing")
        assert ok is False
        assert "not found" in result["error"]

    def test_phase_with_two_items(self):
        phase = {"id": "ph-1", "plan_id": "pl-1", "title": "Phase 1", "phase_order": 1}
        items = [
            {"id": "i-1", "plan_id": "pl-1", "phase_id": "ph-1", "title": "A", "status": "done", "item_key": "K-1", "item_order": 1},
            {"id": "i-2", "plan_id": "pl-1", "phase_id": "ph-1", "title": "B", "status": "planned", "item_key": "K-2", "item_order": 2},
        ]
        links = [{"item_id": "i-1", "task_id": "t-1"}]
        tasks = [{"id": "t-1", "status": "done"}]
        runs = [{"task_id": "t-1", "cost_usd": 1.00}]
        client = self._make_client(phase, items, links, tasks, runs)
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_phase_rollup("pl-1", "ph-1")
        assert ok is True
        r = result["rollup"]
        assert r["phase_id"] == "ph-1"
        assert r["item_count"] == 2
        assert r["task_count"] == 1
        assert r["run_count"] == 1
        assert r["cost_usd"] == pytest.approx(1.00)
        # progress: done=1.0, planned=0.0 → mean=0.5 → 50.0%
        assert r["progress_percent"] == 50.0
        assert len(r["items"]) == 2


class TestGetPlanRollup:
    def _make_plan_client(
        self, plan_data, phases_data, items_data, links_data, tasks_data, runs_data
    ):
        table_calls: dict[str, list] = {
            "project_implementation_plans": [plan_data],
            "project_implementation_phases": [phases_data],
            "project_implementation_items": [items_data],
            "project_implementation_item_task_links": [links_data],
            "archon_tasks": [tasks_data],
            "archon_execution_runs": [runs_data],
        }

        client = MagicMock()

        def _table(name):
            seq = table_calls.get(name, [])
            data = seq.pop(0) if seq else []
            return _chain(data=data)

        client.table.side_effect = _table
        return client

    def test_plan_not_found(self):
        client = _client_returning(data=None)
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_plan_rollup("plan-missing")
        assert ok is False
        assert "not found" in result["error"]

    def test_plan_empty(self):
        plan = {"id": "pl-1", "title": "My Plan", "status": "draft"}
        client = self._make_plan_client(plan, [], [], [], [], [])
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_plan_rollup("pl-1")
        assert ok is True
        r = result["rollup"]
        assert r["plan_id"] == "pl-1"
        assert r["phase_count"] == 0
        assert r["item_count"] == 0
        assert r["task_count"] == 0
        assert r["run_count"] == 0
        assert r["cost_usd"] is None
        assert r["progress_percent"] == 0.0
        assert r["phases"] == []
        assert r["unphased_items"] == []

    def test_plan_with_phase_and_unphased_items(self):
        plan = {"id": "pl-1", "title": "Plan", "status": "active"}
        phases = [{"id": "ph-1", "plan_id": "pl-1", "title": "Phase 1", "phase_order": 1}]
        items = [
            {"id": "i-1", "plan_id": "pl-1", "phase_id": "ph-1", "title": "Phased item", "status": "done", "item_key": "K-1", "item_order": 1},
            {"id": "i-2", "plan_id": "pl-1", "phase_id": None, "title": "Unphased item", "status": "planned", "item_key": None, "item_order": 2},
        ]
        links = [{"item_id": "i-1", "task_id": "t-1"}]
        tasks = [{"id": "t-1", "status": "done"}]
        runs = [{"task_id": "t-1", "cost_usd": 0.50}]
        client = self._make_plan_client(plan, phases, items, links, tasks, runs)
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_plan_rollup("pl-1")
        assert ok is True
        r = result["rollup"]
        assert r["phase_count"] == 1
        assert r["item_count"] == 2
        assert r["task_count"] == 1
        assert r["run_count"] == 1
        assert r["cost_usd"] == pytest.approx(0.50)
        # done=1.0, planned=0.0 → mean=50.0
        assert r["progress_percent"] == 50.0
        assert len(r["phases"]) == 1
        assert r["phases"][0]["phase_id"] == "ph-1"
        assert len(r["unphased_items"]) == 1
        assert r["unphased_items"][0]["item_id"] == "i-2"

    def test_null_cost_handled_gracefully(self):
        plan = {"id": "pl-1", "title": "Plan", "status": "active"}
        phases = []
        items = [
            {"id": "i-1", "plan_id": "pl-1", "phase_id": None, "title": "Item", "status": "in_progress", "item_key": None, "item_order": 1},
        ]
        links = [{"item_id": "i-1", "task_id": "t-1"}]
        tasks = [{"id": "t-1", "status": "doing"}]
        runs = [{"task_id": "t-1", "cost_usd": None}]
        client = self._make_plan_client(plan, phases, items, links, tasks, runs)
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_plan_rollup("pl-1")
        assert ok is True
        r = result["rollup"]
        # Task count and run count populated despite absent cost
        assert r["task_count"] == 1
        assert r["run_count"] == 1
        assert r["cost_usd"] is None

    def test_db_error_returns_false(self):
        client = _client_returning(error=RuntimeError("timeout"))
        svc = PlanRollupService(supabase_client=client)
        ok, result = svc.get_plan_rollup("pl-1")
        assert ok is False
        assert "timeout" in result["error"]


# ── Progress weights completeness ─────────────────────────────────────────────


def test_progress_weights_cover_all_plan_item_statuses():
    """All PlanItemStatus values must be covered by ITEM_PROGRESS_WEIGHTS."""
    from src.server.models.api_contracts import PlanItemStatus

    for s in PlanItemStatus:
        assert s.value in ITEM_PROGRESS_WEIGHTS, f"Missing weight for status '{s.value}'"
