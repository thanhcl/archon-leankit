"""Tests for CostBudgetService."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.server.services.cost_budget_service import (
    BUDGET_WARNING_THRESHOLD,
    DEFAULT_MAX_COST_PER_DAY,
    DEFAULT_MAX_COST_PER_SPRINT,
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


class TestGetCostStatus:
    def test_empty_project(self, service, mock_supabase):
        """No tasks returns zero costs."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=[])
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "ok"
        assert result["today"]["cost_usd"] == 0.0
        assert result["sprint"]["total_cost_usd"] == 0.0

    def test_within_budget(self, service, mock_supabase):
        """Tasks within budget show ok status."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tasks = [_make_task("t-1", 5.0, f"{today}T10:00:00Z")]

        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "ok"
        assert result["today"]["cost_usd"] == 5.0
        assert result["sprint"]["total_cost_usd"] == 5.0

    def test_daily_warning(self, service, mock_supabase):
        """Cost at 80% of daily limit triggers warning."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # 80% of $20 = $16
        tasks = [_make_task("t-1", 16.5, f"{today}T10:00:00Z")]

        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "warning"
        assert result["today"]["warning"] is True

    def test_daily_exceeded(self, service, mock_supabase):
        """Cost above daily limit triggers exceeded."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tasks = [_make_task("t-1", 25.0, f"{today}T10:00:00Z")]

        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "exceeded"
        assert result["today"]["exceeded"] is True

    def test_sprint_exceeded(self, service, mock_supabase):
        """Total cost above sprint limit triggers exceeded."""
        tasks = [
            _make_task("t-1", 10.0, "2026-03-17T10:00:00Z"),
            _make_task("t-2", 10.0, "2026-03-18T10:00:00Z"),
            _make_task("t-3", 10.0, "2026-03-19T10:00:00Z"),
            _make_task("t-4", 80.0, "2026-03-16T10:00:00Z"),
        ]

        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "exceeded"
        assert result["sprint"]["exceeded"] is True

    def test_custom_budget_from_metadata(self, service, mock_supabase):
        """Budget config read from project metadata."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tasks = [_make_task("t-1", 8.0, f"{today}T10:00:00Z")]

        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        # Custom budget: $10/day, $50/sprint — $8 is 80% of $10, triggers warning
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data={
                "metadata": {
                    "cost_budget": {"max_cost_per_day": 10.0, "max_cost_per_sprint": 50.0}
                }
            })
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["status"] == "warning"
        assert result["budget_config"]["max_cost_per_day"] == 10.0

    def test_cost_from_stdout(self, service, mock_supabase):
        """Cost parsed from stdout JSON."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tasks = [_make_task_stdout_cost("t-1", 3.5, f"{today}T10:00:00Z")]

        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["today"]["cost_usd"] == 3.5

    def test_daily_breakdown(self, service, mock_supabase):
        """Daily breakdown groups costs by date."""
        tasks = [
            _make_task("t-1", 5.0, "2026-03-17T10:00:00Z"),
            _make_task("t-2", 3.0, "2026-03-17T14:00:00Z"),
            _make_task("t-3", 7.0, "2026-03-18T10:00:00Z"),
        ]

        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["daily_breakdown"]["2026-03-17"] == 8.0
        assert result["daily_breakdown"]["2026-03-18"] == 7.0

    def test_no_execution_result(self, service, mock_supabase):
        """Tasks without execution_result contribute zero cost."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tasks = [
            {"id": "t-1", "status": "doing", "execution_result": None,
             "state_changed_at": f"{today}T10:00:00Z", "updated_at": f"{today}T10:00:00Z",
             "created_at": f"{today}T10:00:00Z"},
        ]

        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["sprint"]["total_cost_usd"] == 0.0


class TestCheckBudget:
    def test_within_budget_allowed(self, service, mock_supabase):
        """Within budget returns allowed=True."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=[])
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        allowed, details = service.check_budget("proj-1")

        assert allowed is True
        assert details["allowed"] is True

    def test_exceeded_not_allowed(self, service, mock_supabase):
        """Exceeded budget returns allowed=False."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tasks = [_make_task("t-1", 25.0, f"{today}T10:00:00Z")]

        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        allowed, details = service.check_budget("proj-1")

        assert allowed is False
        assert details["allowed"] is False
        assert "Budget exceeded" in details["reason"]

    def test_warning_still_allowed(self, service, mock_supabase):
        """Warning state still allows execution."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tasks = [_make_task("t-1", 17.0, f"{today}T10:00:00Z")]

        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        allowed, details = service.check_budget("proj-1")

        assert allowed is True
        assert details["reason"] == "approaching_limit"


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
