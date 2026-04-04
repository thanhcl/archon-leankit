"""Tests for unified engine health endpoint enhancements (D-P2-01)."""

import pytest


class TestHealthScoreComputation:
    """Test health_score logic independently."""

    def _compute_health_score(
        self,
        stalled_count: int = 0,
        blocked_count: int = 0,
        budget_warnings: int = 0,
        failed_today: int = 0,
        completed_today: int = 0,
    ) -> float:
        """Mirror the health_score computation from engine_api.py."""
        health_score = 1.0
        if stalled_count > 0:
            health_score -= min(0.3, stalled_count * 0.1)
        if blocked_count > 5:
            health_score -= min(0.2, (blocked_count - 5) * 0.02)
        if budget_warnings > 0:
            health_score -= min(0.2, budget_warnings * 0.1)
        if failed_today > completed_today and completed_today > 0:
            health_score -= 0.1
        return round(max(0.0, health_score), 2)

    def test_perfect_health(self):
        score = self._compute_health_score()
        assert score == 1.0

    def test_stalled_runs_reduce_score(self):
        score = self._compute_health_score(stalled_count=2)
        assert score == 0.8  # 1.0 - 2*0.1

    def test_stalled_capped_at_0_3(self):
        score = self._compute_health_score(stalled_count=10)
        assert score == 0.7  # 1.0 - 0.3 (cap)

    def test_blocked_over_5_reduces(self):
        score = self._compute_health_score(blocked_count=10)
        assert score == 0.9  # 1.0 - (10-5)*0.02 = 0.9

    def test_blocked_under_5_no_penalty(self):
        score = self._compute_health_score(blocked_count=3)
        assert score == 1.0

    def test_budget_warnings_reduce(self):
        score = self._compute_health_score(budget_warnings=2)
        assert score == 0.8  # 1.0 - 2*0.1

    def test_failures_exceed_completions(self):
        score = self._compute_health_score(failed_today=5, completed_today=3)
        assert score == 0.9  # 1.0 - 0.1

    def test_failures_no_penalty_when_zero_completed(self):
        score = self._compute_health_score(failed_today=5, completed_today=0)
        assert score == 1.0  # no penalty when completed=0

    def test_combined_degradation(self):
        score = self._compute_health_score(
            stalled_count=1,
            blocked_count=15,
            budget_warnings=1,
            failed_today=10,
            completed_today=2,
        )
        # 1.0 - 0.1(stalled) - 0.2(blocked, cap) - 0.1(budget) - 0.1(failures) = 0.5
        assert score == 0.5

    def test_never_goes_below_zero(self):
        score = self._compute_health_score(
            stalled_count=10,
            blocked_count=100,
            budget_warnings=10,
            failed_today=100,
            completed_today=1,
        )
        assert score >= 0.0
