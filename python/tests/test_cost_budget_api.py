"""
Tests for Cost Budget API

Tests the CostBudgetService integration and API endpoint behavior
with mocked Supabase client.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.server.services.cost_budget_service import (
    DEFAULT_DAILY_BUDGET,
    DEFAULT_WEEKLY_BUDGET,
    CostBudgetService,
)


def _make_run(cost_usd: float | None, started_at: str) -> dict:
    return {"cost_usd": cost_usd, "started_at": started_at}


@pytest.fixture
def mock_supabase():
    return MagicMock()


@pytest.fixture
def service(mock_supabase):
    return CostBudgetService(supabase_client=mock_supabase)


def _mock_tables(mock_supabase, runs: list[dict], tasks: list[dict], project_metadata=None):
    """Route Supabase table calls to the correct mock chain."""

    def table_side_effect(table_name):
        if table_name == "archon_execution_runs":
            chain = MagicMock()
            chain.select.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(data=runs)
            return chain
        elif table_name == "archon_tasks":
            chain = MagicMock()
            chain.select.return_value.eq.return_value.or_.return_value.execute.return_value = MagicMock(data=tasks)
            return chain
        elif table_name == "archon_projects":
            chain = MagicMock()
            chain.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=project_metadata)
            return chain
        return MagicMock()

    mock_supabase.table.side_effect = table_side_effect


class TestCostStatusAPIBehavior:
    """Test the service behavior as the API endpoint would use it."""

    def test_returns_project_id(self, service, mock_supabase):
        _mock_tables(mock_supabase, runs=[], tasks=[], project_metadata=None)

        ok, result = service.get_cost_status("proj-123")

        assert ok is True
        assert result["project_id"] == "proj-123"

    def test_response_structure(self, service, mock_supabase):
        """Verify the response has all expected fields for the API."""
        _mock_tables(mock_supabase, runs=[], tasks=[], project_metadata=None)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert "project_id" in result
        assert "status" in result
        assert "today" in result
        assert "weekly" in result
        assert "sprint" in result  # backward-compat alias
        assert "daily_breakdown" in result
        assert "budget_config" in result

        today = result["today"]
        assert "date" in today
        assert "cost_usd" in today
        assert "budget_usd" in today
        assert "usage_pct" in today
        assert "exceeded" in today
        assert "warning" in today

        weekly = result["weekly"]
        assert "total_cost_usd" in weekly
        assert "budget_usd" in weekly
        assert "usage_pct" in weekly
        assert "exceeded" in weekly
        assert "warning" in weekly

    def test_multi_day_costs(self, service, mock_supabase):
        """Multi-day costs correctly accumulated from execution_runs."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        runs = [
            _make_run(5.0, "2026-03-17T10:00:00Z"),
            _make_run(10.0, "2026-03-18T10:00:00Z"),
            _make_run(3.0, f"{today}T10:00:00Z"),
        ]
        _mock_tables(mock_supabase, runs=runs, tasks=[], project_metadata=None)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["weekly"]["total_cost_usd"] == 18.0
        assert result["sprint"]["total_cost_usd"] == 18.0  # sprint alias
        assert result["today"]["cost_usd"] == 3.0

    def test_error_returns_false(self, service, mock_supabase):
        """Service error returns (False, error_dict)."""
        # Make all table calls raise to trigger outer exception handler
        mock_supabase.table.side_effect = Exception("DB connection failed")

        ok, result = service.get_cost_status("proj-1")

        assert ok is False
        assert "error" in result

    def test_budget_check_fail_open(self, service, mock_supabase):
        """If budget check fails, fail-open (allow execution)."""
        mock_supabase.table.side_effect = Exception("DB error")

        allowed, details = service.check_budget("proj-1")

        assert allowed is True
        assert details["reason"] == "budget_check_failed"

    def test_usage_percentage_calculation(self, service, mock_supabase):
        """Usage percentage is correctly calculated against env-var defaults."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Use half of the default daily budget
        half_daily = DEFAULT_DAILY_BUDGET / 2
        runs = [_make_run(half_daily, f"{today}T10:00:00Z")]
        _mock_tables(mock_supabase, runs=runs, tasks=[], project_metadata=None)

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["today"]["usage_pct"] == 50.0
        weekly_pct = round((half_daily / DEFAULT_WEEKLY_BUDGET) * 100, 1)
        assert result["weekly"]["usage_pct"] == weekly_pct
