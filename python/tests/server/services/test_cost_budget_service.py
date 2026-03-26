"""Tests for CostBudgetService."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.server.services.cost_budget_service import (
    BUDGET_WARNING_THRESHOLD,
    DEFAULT_DAILY_BUDGET,
    DEFAULT_WEEKLY_BUDGET,
    CostBudgetService,
)


@pytest.fixture
def mock_supabase():
    return MagicMock()


@pytest.fixture
def service(mock_supabase):
    return CostBudgetService(supabase_client=mock_supabase)


def _make_task(task_id: str, cost: float, date: str = "2026-03-19T10:00:00Z") -> dict:
    """Helper to create a task dict with cost in execution_result."""
    return {
        "id": task_id,
        "status": "done",
        "execution_result": {"total_cost_usd": cost},
        "state_changed_at": date,
        "updated_at": date,
        "created_at": date,
    }


def _make_task_stdout_cost(task_id: str, cost: float, date: str = "2026-03-19T10:00:00Z") -> dict:
    """Helper to create a task with cost in stdout."""
    return {
        "id": task_id,
        "status": "done",
        "execution_result": {"stdout": f'{{"total_cost_usd": {cost}}}'},
        "state_changed_at": date,
        "updated_at": date,
        "created_at": date,
    }


def _make_run(cost_usd: float | None, started_at: str) -> dict:
    """Helper to create an execution_run dict."""
    return {"cost_usd": cost_usd, "started_at": started_at}


def _mock_supabase_for_status(mock_supabase, runs: list[dict], tasks: list[dict], project_metadata=None):
    """Configure mock_supabase to return execution_runs and tasks and optional project metadata."""
    # execution_runs query (for _fetch_costs_from_execution_runs)
    runs_mock = MagicMock()
    runs_mock.data = runs
    # tasks query (for _fetch_project_tasks via _accumulate_daily_costs)
    tasks_mock = MagicMock()
    tasks_mock.data = tasks
    # project metadata query (for _get_budget_config)
    meta_mock = MagicMock()
    meta_mock.data = project_metadata

    # Route calls: execution_runs uses .gte(), tasks uses .or_(), metadata uses .single()
    def table_side_effect(table_name):
        if table_name == "archon_execution_runs":
            chain = MagicMock()
            chain.select.return_value.eq.return_value.gte.return_value.execute.return_value = runs_mock
            return chain
        elif table_name == "archon_tasks":
            chain = MagicMock()
            chain.select.return_value.eq.return_value.or_.return_value.execute.return_value = tasks_mock
            return chain
        elif table_name == "archon_projects":
            chain = MagicMock()
            chain.select.return_value.eq.return_value.single.return_value.execute.return_value = meta_mock
            return chain
        return MagicMock()

    mock_supabase.table.side_effect = table_side_effect


class TestGetCostStatus:
    def test_empty_project(self, service, mock_supabase):
        """No runs or tasks returns zero costs."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _mock_supabase_for_status(mock_supabase, runs=[], tasks=[], project_metadata=None)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "ok"
        assert result["today"]["cost_usd"] == 0.0
        assert result["weekly"]["total_cost_usd"] == 0.0

    def test_within_budget(self, service, mock_supabase):
        """Runs within budget show ok status."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        runs = [_make_run(5.0, f"{today}T10:00:00Z")]
        _mock_supabase_for_status(mock_supabase, runs=runs, tasks=[], project_metadata=None)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "ok"
        assert result["today"]["cost_usd"] == 5.0
        assert result["weekly"]["total_cost_usd"] == 5.0

    def test_daily_warning(self, service, mock_supabase):
        """Cost at 80% of daily limit triggers warning."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # 80% of DEFAULT_DAILY_BUDGET ($50) = $40 → use $41
        runs = [_make_run(41.0, f"{today}T10:00:00Z")]
        _mock_supabase_for_status(mock_supabase, runs=runs, tasks=[], project_metadata=None)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "warning"
        assert result["today"]["warning"] is True

    def test_daily_exceeded(self, service, mock_supabase):
        """Cost above daily limit triggers exceeded."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # DEFAULT_DAILY_BUDGET is $50; use $55
        runs = [_make_run(55.0, f"{today}T10:00:00Z")]
        _mock_supabase_for_status(mock_supabase, runs=runs, tasks=[], project_metadata=None)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "exceeded"
        assert result["today"]["exceeded"] is True

    def test_weekly_exceeded(self, service, mock_supabase):
        """Total cost above weekly limit triggers exceeded."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # DEFAULT_WEEKLY_BUDGET is $200; use $210 across multiple days
        runs = [
            _make_run(50.0, "2026-03-19T10:00:00Z"),
            _make_run(50.0, "2026-03-20T10:00:00Z"),
            _make_run(50.0, "2026-03-21T10:00:00Z"),
            _make_run(60.0, "2026-03-22T10:00:00Z"),
        ]
        _mock_supabase_for_status(mock_supabase, runs=runs, tasks=[], project_metadata=None)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "exceeded"
        assert result["weekly"]["exceeded"] is True

    def test_custom_budget_from_metadata(self, service, mock_supabase):
        """Budget config read from project metadata using new field names."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        runs = [_make_run(8.0, f"{today}T10:00:00Z")]
        # Custom budget: $10/day, $50/week — $8 is 80% of $10, triggers warning
        metadata = {"metadata": {"cost_budget": {"daily_budget_usd": 10.0, "weekly_budget_usd": 50.0}}}
        _mock_supabase_for_status(mock_supabase, runs=runs, tasks=[], project_metadata=metadata)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "warning"
        assert result["budget_config"]["daily_budget_usd"] == 10.0

    def test_legacy_metadata_fields_supported(self, service, mock_supabase):
        """Legacy max_cost_per_day/max_cost_per_sprint metadata fields still work."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        runs = [_make_run(8.0, f"{today}T10:00:00Z")]
        # Legacy field names
        metadata = {"metadata": {"cost_budget": {"max_cost_per_day": 10.0, "max_cost_per_sprint": 50.0}}}
        _mock_supabase_for_status(mock_supabase, runs=runs, tasks=[], project_metadata=metadata)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "warning"
        assert result["budget_config"]["daily_budget_usd"] == 10.0

    def test_null_cost_usd_fails_open(self, service, mock_supabase):
        """Runs with null cost_usd are skipped; execution is allowed (fail-open)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        runs = [
            _make_run(None, f"{today}T10:00:00Z"),  # null cost — CI-5 not populated
            _make_run(5.0, f"{today}T11:00:00Z"),
        ]
        _mock_supabase_for_status(mock_supabase, runs=runs, tasks=[], project_metadata=None)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["today"]["cost_usd"] == 5.0  # only non-null counted

    def test_execution_runs_query_failure_fails_open(self, service, mock_supabase):
        """When execution_runs query fails, cost defaults to 0 (fail-open)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # execution_runs raises exception
        def table_side_effect(table_name):
            if table_name == "archon_execution_runs":
                chain = MagicMock()
                chain.select.return_value.eq.return_value.gte.return_value.execute.side_effect = Exception("DB down")
                return chain
            elif table_name == "archon_tasks":
                chain = MagicMock()
                chain.select.return_value.eq.return_value.or_.return_value.execute.return_value = MagicMock(data=[])
                return chain
            elif table_name == "archon_projects":
                chain = MagicMock()
                chain.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=None)
                return chain
            return MagicMock()

        mock_supabase.table.side_effect = table_side_effect

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["today"]["cost_usd"] == 0.0  # fail-open: zero cost assumed
        assert result["status"] == "ok"

    def test_daily_breakdown_from_tasks(self, service, mock_supabase):
        """Daily breakdown still uses task-based costs for detail view."""
        tasks = [
            _make_task("t-1", 5.0, "2026-03-17T10:00:00Z"),
            _make_task("t-2", 3.0, "2026-03-17T14:00:00Z"),
            _make_task("t-3", 7.0, "2026-03-18T10:00:00Z"),
        ]
        _mock_supabase_for_status(mock_supabase, runs=[], tasks=tasks, project_metadata=None)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["daily_breakdown"]["2026-03-17"] == 8.0
        assert result["daily_breakdown"]["2026-03-18"] == 7.0

    def test_sprint_alias_matches_weekly(self, service, mock_supabase):
        """sprint key in response is an alias for weekly for backward compat."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        runs = [_make_run(10.0, f"{today}T10:00:00Z")]
        _mock_supabase_for_status(mock_supabase, runs=runs, tasks=[], project_metadata=None)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["sprint"]["total_cost_usd"] == result["weekly"]["total_cost_usd"]


class TestCheckBudget:
    def test_within_budget_allowed(self, service, mock_supabase):
        """Within budget returns allowed=True."""
        _mock_supabase_for_status(mock_supabase, runs=[], tasks=[], project_metadata=None)

        allowed, details = service.check_budget("proj-1")

        assert allowed is True
        assert details["allowed"] is True

    def test_exceeded_not_allowed(self, service, mock_supabase):
        """Exceeded budget returns allowed=False."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Exceed daily budget ($50 default)
        runs = [_make_run(55.0, f"{today}T10:00:00Z")]
        _mock_supabase_for_status(mock_supabase, runs=runs, tasks=[], project_metadata=None)

        allowed, details = service.check_budget("proj-1")

        assert allowed is False
        assert details["allowed"] is False
        assert "Budget exceeded" in details["reason"]
        assert "daily" in details["reason"]

    def test_warning_still_allowed(self, service, mock_supabase):
        """Warning state still allows execution."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # 82% of $50 default = $41
        runs = [_make_run(41.0, f"{today}T10:00:00Z")]
        _mock_supabase_for_status(mock_supabase, runs=runs, tasks=[], project_metadata=None)

        allowed, details = service.check_budget("proj-1")

        assert allowed is True
        assert details["reason"] == "approaching_limit"

    def test_budget_check_failure_allows_execution(self, service, mock_supabase):
        """When budget check fails entirely, execution is allowed (fail-open)."""
        mock_supabase.table.side_effect = Exception("Supabase down")

        allowed, details = service.check_budget("proj-1")

        assert allowed is True
        assert details["allowed"] is True
        assert details["reason"] == "budget_check_failed"


class TestFetchCostsFromExecutionRuns:
    def test_daily_and_weekly_costs(self, service, mock_supabase):
        """Correctly sums daily and weekly costs from execution_runs."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        runs = [
            _make_run(10.0, f"{today}T08:00:00Z"),
            _make_run(5.0, f"{today}T12:00:00Z"),
            _make_run(20.0, "2026-03-19T10:00:00Z"),  # older run (still in week)
        ]
        chain = MagicMock()
        chain.select.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(data=runs)
        mock_supabase.table.return_value = chain

        daily, weekly = service._fetch_costs_from_execution_runs("proj-1")

        assert daily == 15.0  # today's runs only
        assert weekly == 35.0  # all runs

    def test_null_costs_skipped(self, service, mock_supabase):
        """Null cost_usd entries are skipped without failing."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        runs = [
            _make_run(None, f"{today}T08:00:00Z"),
            _make_run(5.0, f"{today}T12:00:00Z"),
        ]
        chain = MagicMock()
        chain.select.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(data=runs)
        mock_supabase.table.return_value = chain

        daily, weekly = service._fetch_costs_from_execution_runs("proj-1")

        assert daily == 5.0
        assert weekly == 5.0

    def test_query_failure_returns_zeros(self, service, mock_supabase):
        """Query failure returns (0.0, 0.0) without raising."""
        chain = MagicMock()
        chain.select.return_value.eq.return_value.gte.return_value.execute.side_effect = Exception("timeout")
        mock_supabase.table.return_value = chain

        daily, weekly = service._fetch_costs_from_execution_runs("proj-1")

        assert daily == 0.0
        assert weekly == 0.0


class TestRecordTaskCost:
    def test_records_cost(self, service):
        """Extracts cost from execution result."""
        cost = service.record_task_cost("proj-1", "t-1", {"total_cost_usd": 2.5})
        assert cost == 2.5

    def test_zero_cost_no_field(self, service):
        """Returns 0 when no cost field."""
        cost = service.record_task_cost("proj-1", "t-1", {"exit_code": 0})
        assert cost == 0.0

    def test_cost_from_stdout(self, service):
        """Extracts cost from stdout JSON."""
        cost = service.record_task_cost(
            "proj-1", "t-1",
            {"stdout": '{"total_cost_usd": 1.75}'},
        )
        assert cost == 1.75


class TestParseCost:
    def test_direct_field(self, service):
        assert service._parse_cost({"total_cost_usd": 3.14}) == 3.14

    def test_string_cost(self, service):
        assert service._parse_cost({"total_cost_usd": "2.50"}) == 2.5

    def test_stdout_regex(self, service):
        result = {"stdout": 'some output\n{"total_cost_usd": 4.20}\nmore output'}
        assert service._parse_cost(result) == 4.2

    def test_empty_result(self, service):
        assert service._parse_cost({}) == 0.0

    def test_none_result(self, service):
        assert service._parse_cost(None) == 0.0

    def test_invalid_cost_value(self, service):
        assert service._parse_cost({"total_cost_usd": "not_a_number"}) == 0.0


class TestGetBudgetConfig:
    def test_defaults_without_project(self, service, mock_supabase):
        """Returns env-var defaults when no project ID provided."""
        config = service._get_budget_config(None)
        assert config["daily_budget_usd"] == DEFAULT_DAILY_BUDGET
        assert config["weekly_budget_usd"] == DEFAULT_WEEKLY_BUDGET

    def test_new_field_names(self, service, mock_supabase):
        """Reads daily_budget_usd and weekly_budget_usd from project metadata."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data={"metadata": {"cost_budget": {"daily_budget_usd": 25.0, "weekly_budget_usd": 100.0}}}
        )
        config = service._get_budget_config("proj-1")
        assert config["daily_budget_usd"] == 25.0
        assert config["weekly_budget_usd"] == 100.0

    def test_legacy_field_names(self, service, mock_supabase):
        """Legacy max_cost_per_day/max_cost_per_sprint fields are recognized."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data={"metadata": {"cost_budget": {"max_cost_per_day": 30.0, "max_cost_per_sprint": 150.0}}}
        )
        config = service._get_budget_config("proj-1")
        assert config["daily_budget_usd"] == 30.0
        assert config["weekly_budget_usd"] == 150.0


class TestEnvVarDefaults:
    def test_default_daily_budget_from_env(self):
        """LEANKIT_ENGINE_DEFAULT_DAILY_BUDGET overrides default."""
        from src.server.config.env_aliases import get_engine_default_daily_budget
        result = get_engine_default_daily_budget(env={"LEANKIT_ENGINE_DEFAULT_DAILY_BUDGET": "75.0"})
        assert result == 75.0

    def test_default_weekly_budget_from_env(self):
        """LEANKIT_ENGINE_DEFAULT_WEEKLY_BUDGET overrides default."""
        from src.server.config.env_aliases import get_engine_default_weekly_budget
        result = get_engine_default_weekly_budget(env={"LEANKIT_ENGINE_DEFAULT_WEEKLY_BUDGET": "300.0"})
        assert result == 300.0

    def test_invalid_env_value_falls_back(self):
        """Invalid env value returns the hardcoded default."""
        from src.server.config.env_aliases import get_engine_default_daily_budget
        result = get_engine_default_daily_budget(env={"LEANKIT_ENGINE_DEFAULT_DAILY_BUDGET": "not_a_number"})
        assert result == 50.0

    def test_negative_env_value_clamped_to_zero(self):
        """Negative env value is clamped to 0."""
        from src.server.config.env_aliases import get_engine_default_weekly_budget
        result = get_engine_default_weekly_budget(env={"LEANKIT_ENGINE_DEFAULT_WEEKLY_BUDGET": "-10"})
        assert result == 0.0
