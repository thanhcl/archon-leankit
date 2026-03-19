"""Tests for GlobalCapacityTracker — cross-project parallel execution limits."""

import threading

import pytest

from src.server.services.engine.capacity_tracker import GlobalCapacityTracker


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
