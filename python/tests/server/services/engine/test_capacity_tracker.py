"""Tests for GlobalCapacityTracker and SharedAgentPool — cross-project parallel execution limits."""

import threading

import pytest

from src.server.services.engine.capacity_tracker import GlobalCapacityTracker, SharedAgentPool


class TestGlobalCapacityTracker:
    def test_initial_state(self):
        tracker = GlobalCapacityTracker(max_global=5)
        assert tracker.total_running == 0
        assert tracker.available_slots == 5
        assert tracker.has_capacity()

    def test_try_acquire_success(self):
        tracker = GlobalCapacityTracker(max_global=3)
        assert tracker.try_acquire("proj-1")
        assert tracker.total_running == 1
        assert tracker.available_slots == 2

    def test_try_acquire_at_limit(self):
        tracker = GlobalCapacityTracker(max_global=2)
        assert tracker.try_acquire("proj-1")
        assert tracker.try_acquire("proj-2")
        assert not tracker.try_acquire("proj-3")
        assert tracker.total_running == 2

    def test_release(self):
        tracker = GlobalCapacityTracker(max_global=2)
        tracker.try_acquire("proj-1")
        tracker.try_acquire("proj-1")
        assert tracker.total_running == 2
        tracker.release("proj-1")
        assert tracker.total_running == 1
        assert tracker.has_capacity()

    def test_release_below_zero(self):
        tracker = GlobalCapacityTracker(max_global=5)
        tracker.release("proj-1")
        assert tracker.total_running == 0
        assert tracker.running_for_project("proj-1") == 0

    def test_running_for_project(self):
        tracker = GlobalCapacityTracker(max_global=10)
        tracker.try_acquire("proj-a")
        tracker.try_acquire("proj-a")
        tracker.try_acquire("proj-b")
        assert tracker.running_for_project("proj-a") == 2
        assert tracker.running_for_project("proj-b") == 1
        assert tracker.running_for_project("proj-c") == 0

    def test_cross_project_limit(self):
        tracker = GlobalCapacityTracker(max_global=3)
        assert tracker.try_acquire("proj-a")
        assert tracker.try_acquire("proj-b")
        assert tracker.try_acquire("proj-a")
        # At limit — no more slots for any project
        assert not tracker.try_acquire("proj-a")
        assert not tracker.try_acquire("proj-c")
        # Release one and acquire again
        tracker.release("proj-b")
        assert tracker.try_acquire("proj-c")

    def test_status_snapshot(self):
        tracker = GlobalCapacityTracker(max_global=5)
        tracker.try_acquire("proj-1")
        tracker.try_acquire("proj-2")
        tracker.try_acquire("proj-1")

        status = tracker.status()
        assert status["max_global"] == 5
        assert status["total_running"] == 3
        assert status["available_slots"] == 2
        assert status["per_project"]["proj-1"] == 2
        assert status["per_project"]["proj-2"] == 1

    def test_thread_safety(self):
        """Verify tracker handles concurrent access without data corruption."""
        tracker = GlobalCapacityTracker(max_global=100)
        errors = []

        def acquire_release(project: str, count: int):
            try:
                for _ in range(count):
                    tracker.try_acquire(project)
                for _ in range(count):
                    tracker.release(project)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=acquire_release, args=(f"proj-{i}", 50))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert tracker.total_running == 0


class TestSharedAgentPool:
    def test_initial_state(self):
        pool = SharedAgentPool(pool_id="test", total_slots=5)
        assert pool.total_running == 0
        assert pool.available_slots == 5
        assert pool.has_capacity("proj-1")

    def test_invalid_config(self):
        with pytest.raises(ValueError):
            SharedAgentPool(pool_id="bad", total_slots=0)
        with pytest.raises(ValueError):
            SharedAgentPool(pool_id="bad", total_slots=3, priority_reserve=3)
        with pytest.raises(ValueError):
            SharedAgentPool(pool_id="bad", total_slots=3, priority_reserve=-1)

    def test_acquire_and_release(self):
        pool = SharedAgentPool(pool_id="test", total_slots=5)
        assert pool.try_acquire("proj-1")
        assert pool.total_running == 1
        assert pool.running_for_project("proj-1") == 1
        pool.release("proj-1")
        assert pool.total_running == 0

    def test_per_project_limit(self):
        pool = SharedAgentPool(
            pool_id="test",
            total_slots=5,
            per_project_limits={"proj-a": 2, "proj-b": 3},
        )
        # proj-a can get 2 slots
        assert pool.try_acquire("proj-a")
        assert pool.try_acquire("proj-a")
        # Third acquire for proj-a is denied (at its per-project limit)
        assert not pool.try_acquire("proj-a")
        # But proj-b can still acquire
        assert pool.try_acquire("proj-b")

    def test_total_slots_limit(self):
        pool = SharedAgentPool(
            pool_id="test",
            total_slots=3,
            per_project_limits={"proj-a": 3, "proj-b": 3},
            priority_reserve=0,
        )
        assert pool.try_acquire("proj-a")
        assert pool.try_acquire("proj-b")
        assert pool.try_acquire("proj-a")
        # Pool is full — neither project can acquire more
        assert not pool.try_acquire("proj-a")
        assert not pool.try_acquire("proj-b")

    def test_priority_reserve_blocks_normal(self):
        # priority_reserve=1 means only 2 of 3 slots available to normal tasks
        pool = SharedAgentPool(
            pool_id="test",
            total_slots=3,
            per_project_limits={"proj-a": 3},
            priority_reserve=1,
        )
        assert pool.try_acquire("proj-a", priority="medium")
        assert pool.try_acquire("proj-a", priority="low")
        # Third slot is reserved — normal task blocked
        assert not pool.try_acquire("proj-a", priority="medium")
        # High priority task CAN use the reserved slot
        assert pool.try_acquire("proj-a", priority="high")

    def test_priority_reserve_allows_critical(self):
        pool = SharedAgentPool(
            pool_id="test",
            total_slots=3,
            per_project_limits={"proj-a": 3},
            priority_reserve=2,
        )
        # First slot for medium
        assert pool.try_acquire("proj-a", priority="medium")
        # Second and third slots are reserved — medium blocked
        assert not pool.try_acquire("proj-a", priority="medium")
        # Critical can use reserved slots
        assert pool.try_acquire("proj-a", priority="critical")
        assert pool.try_acquire("proj-a", priority="high")

    def test_no_starvation_via_per_project_limits(self):
        # proj-a max 2 slots, proj-b max 2 slots, pool has 4 total
        pool = SharedAgentPool(
            pool_id="test",
            total_slots=4,
            per_project_limits={"proj-a": 2, "proj-b": 2},
            priority_reserve=0,
        )
        assert pool.try_acquire("proj-a")
        assert pool.try_acquire("proj-a")
        # proj-a at limit — cannot starve proj-b
        assert not pool.try_acquire("proj-a")
        # proj-b still gets its fair share
        assert pool.try_acquire("proj-b")
        assert pool.try_acquire("proj-b")

    def test_release_below_zero_safe(self):
        pool = SharedAgentPool(pool_id="test", total_slots=5)
        pool.release("proj-1")  # No error; count stays 0
        assert pool.running_for_project("proj-1") == 0

    def test_default_project_slots(self):
        pool = SharedAgentPool(pool_id="test", total_slots=10, default_project_slots=2)
        # Unknown project gets default limit of 2
        assert pool.try_acquire("new-proj")
        assert pool.try_acquire("new-proj")
        assert not pool.try_acquire("new-proj")

    def test_status_snapshot(self):
        pool = SharedAgentPool(
            pool_id="test",
            total_slots=5,
            per_project_limits={"proj-a": 2, "proj-b": 3},
            priority_reserve=1,
        )
        pool.try_acquire("proj-a")
        pool.try_acquire("proj-b")

        status = pool.status()
        assert status["pool_id"] == "test"
        assert status["total_slots"] == 5
        assert status["total_running"] == 2
        assert status["available_slots"] == 3
        assert status["priority_reserve"] == 1
        assert status["per_project_running"]["proj-a"] == 1
        assert status["per_project_running"]["proj-b"] == 1

    def test_thread_safety(self):
        """Verify pool handles concurrent access without data corruption."""
        pool = SharedAgentPool(
            pool_id="test",
            total_slots=100,
            default_project_slots=50,
            priority_reserve=0,
        )
        errors = []

        def acquire_release(project: str, count: int):
            try:
                for _ in range(count):
                    pool.try_acquire(project)
                for _ in range(count):
                    pool.release(project)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=acquire_release, args=(f"proj-{i}", 20))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert pool.total_running == 0
