"""Tests for adaptive poll interval (C-P5-04)."""

from unittest.mock import MagicMock, patch

import pytest

from src.server.services.engine.task_engine import TaskEngine


def _make_engine():
    with patch("src.server.services.engine.task_engine.TaskService"), \
         patch("src.server.services.engine.task_engine.TaskLifecycleService"), \
         patch("src.server.services.engine.task_engine.ArchitectReviewer"), \
         patch("src.server.services.engine.task_engine.Notifier"), \
         patch("src.server.services.engine.task_engine.LearningProcessor"), \
         patch("src.server.services.engine.task_engine.PromptBuilder"), \
         patch("src.server.services.engine.task_engine.HealthMonitor"), \
         patch("src.server.services.engine.task_engine.CCSpawner"):
        engine = TaskEngine(project_path="/tmp/test", project_id="proj-001")
        return engine


class TestComputeAdaptiveInterval:

    def test_active_work_returns_shortest(self):
        engine = _make_engine()
        interval = engine._compute_adaptive_interval(had_work_before=False, has_work_after=True)
        assert interval == 2

    def test_just_completed_returns_short(self):
        engine = _make_engine()
        interval = engine._compute_adaptive_interval(had_work_before=True, has_work_after=False)
        assert interval == 5

    def test_idle_3_cycles_returns_medium(self):
        engine = _make_engine()
        engine._adaptive_idle_cycles = 2  # will be incremented to 3
        interval = engine._compute_adaptive_interval(had_work_before=False, has_work_after=False)
        assert interval == 15

    def test_deep_idle_returns_longest(self):
        engine = _make_engine()
        engine._adaptive_idle_cycles = 9  # will be incremented to 10
        interval = engine._compute_adaptive_interval(had_work_before=False, has_work_after=False)
        assert interval == 30

    def test_work_resets_idle_counter(self):
        engine = _make_engine()
        engine._adaptive_idle_cycles = 20
        engine._compute_adaptive_interval(had_work_before=False, has_work_after=True)
        assert engine._adaptive_idle_cycles == 0

    def test_idle_increments_counter(self):
        engine = _make_engine()
        engine._adaptive_idle_cycles = 0
        engine._compute_adaptive_interval(had_work_before=False, has_work_after=False)
        assert engine._adaptive_idle_cycles == 1

    def test_early_idle_returns_recent(self):
        engine = _make_engine()
        engine._adaptive_idle_cycles = 0
        interval = engine._compute_adaptive_interval(had_work_before=False, has_work_after=False)
        assert interval == 5  # < 3 idle cycles

    def test_progressive_backoff(self):
        """Verify intervals increase over consecutive idle cycles."""
        engine = _make_engine()
        intervals = []
        for _ in range(15):
            interval = engine._compute_adaptive_interval(had_work_before=False, has_work_after=False)
            intervals.append(interval)

        # Should start low and increase
        assert intervals[0] <= 5
        assert intervals[5] >= 15  # after 3+ idle cycles
        assert intervals[12] == 30  # after 10+ idle cycles


class TestGetPollMode:

    def test_returns_fixed_when_no_project(self):
        engine = _make_engine()
        engine.project_id = None
        assert engine._get_poll_mode() == "fixed"

    def test_returns_fixed_when_no_policy(self):
        engine = _make_engine()
        engine.engine_policy_service.get_active_policy = MagicMock(return_value=None)
        assert engine._get_poll_mode() == "fixed"

    def test_returns_adaptive_from_policy(self):
        engine = _make_engine()
        engine.engine_policy_service.get_active_policy = MagicMock(return_value={
            "capacity_policy": {"poll_mode": "adaptive"},
        })
        assert engine._get_poll_mode() == "adaptive"

    def test_returns_fixed_from_policy(self):
        engine = _make_engine()
        engine.engine_policy_service.get_active_policy = MagicMock(return_value={
            "capacity_policy": {"poll_mode": "fixed"},
        })
        assert engine._get_poll_mode() == "fixed"

    def test_returns_fixed_when_empty_capacity(self):
        engine = _make_engine()
        engine.engine_policy_service.get_active_policy = MagicMock(return_value={
            "capacity_policy": {},
        })
        assert engine._get_poll_mode() == "fixed"
