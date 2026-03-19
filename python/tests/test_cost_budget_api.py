"""
Tests for Cost Budget API

Tests the CostBudgetService integration and API endpoint behavior
with mocked Supabase client. Mirrors test_sprint_stats_api.py pattern.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.server.services.cost_budget_service import CostBudgetService


def _make_task(task_id: str, cost: float, date: str) -> dict:
    return {
        "id": task_id,
        "status": "done",
        "execution_result": {"total_cost_usd": cost},
        "state_changed_at": date,
        "updated_at": date,
        "created_at": date,
    }


@pytest.fixture
def mock_supabase():
    return MagicMock()


@pytest.fixture
def service(mock_supabase):
    return CostBudgetService(supabase_client=mock_supabase)


class TestCostStatusAPIBehavior:
    """Test the service behavior as the API endpoint would use it."""

    def test_returns_project_id(self, service, mock_supabase):
        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=[])
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-123")

        assert ok is True
        assert result["project_id"] == "proj-123"

    def test_response_structure(self, service, mock_supabase):
        """Verify the response has all expected fields for the API."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=[])
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        # Required top-level fields
        assert "project_id" in result
        assert "status" in result
        assert "today" in result
        assert "sprint" in result
        assert "daily_breakdown" in result
        assert "budget_config" in result

        # Today fields
        today = result["today"]
        assert "date" in today
        assert "cost_usd" in today
        assert "budget_usd" in today
        assert "usage_pct" in today
        assert "exceeded" in today
        assert "warning" in today

        # Sprint fields
        sprint = result["sprint"]
        assert "total_cost_usd" in sprint
        assert "budget_usd" in sprint
        assert "usage_pct" in sprint
        assert "exceeded" in sprint
        assert "warning" in sprint

    def test_multi_day_costs(self, service, mock_supabase):
        """Multi-day costs correctly accumulated."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tasks = [
            _make_task("t-1", 5.0, "2026-03-17T10:00:00Z"),
            _make_task("t-2", 10.0, "2026-03-18T10:00:00Z"),
            _make_task("t-3", 3.0, f"{today}T10:00:00Z"),
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["sprint"]["total_cost_usd"] == 18.0
        assert result["today"]["cost_usd"] == 3.0

    def test_error_returns_false(self, service, mock_supabase):
        """Service error returns (False, error_dict)."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.side_effect = (
            Exception("DB connection failed")
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is False
        assert "error" in result

    def test_budget_check_fail_open(self, service, mock_supabase):
        """If budget check fails, fail-open (allow execution)."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.side_effect = (
            Exception("DB error")
        )

        allowed, details = service.check_budget("proj-1")

        assert allowed is True
        assert details["reason"] == "budget_check_failed"

    def test_usage_percentage_calculation(self, service, mock_supabase):
        """Usage percentage is correctly calculated."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # $10 of $20 daily budget = 50%
        tasks = [_make_task("t-1", 10.0, f"{today}T10:00:00Z")]

        mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = (
            MagicMock(data=tasks)
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data=None)
        )

        ok, result = service.get_cost_status("proj-1")

        assert ok is True
        assert result["today"]["usage_pct"] == 50.0
        assert result["sprint"]["usage_pct"] == 10.0  # $10 of $100
