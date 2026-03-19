"""
Global Capacity Tracker for LeanKit V3 Task Engine.

Enforces cross-project parallel execution limits by tracking
total running tasks across all project engines.

Usage:
    tracker = GlobalCapacityTracker(max_global=10)
    if tracker.try_acquire("project-123"):
        # ... spawn task ...
        tracker.release("project-123")
"""

import threading
from collections import defaultdict

from ...config.logfire_config import get_logger

logger = get_logger(__name__)


class GlobalCapacityTracker:
    """Thread-safe tracker for cross-project execution limits.

    Each project engine calls try_acquire/release when spawning tasks.
    The tracker enforces a global ceiling across all projects.
    """

    def __init__(self, max_global: int = 10):
        self.max_global = max_global
        self._lock = threading.Lock()
        self._counts: dict[str, int] = defaultdict(int)

    @property
    def total_running(self) -> int:
        with self._lock:
            return sum(self._counts.values())

    @property
    def available_slots(self) -> int:
        return max(0, self.max_global - self.total_running)

    def has_capacity(self) -> bool:
        return self.total_running < self.max_global

    def try_acquire(self, project_id: str) -> bool:
        """Try to acquire a global slot for a project.

        Returns True if a slot was acquired, False if at global limit.
        """
        with self._lock:
            if sum(self._counts.values()) >= self.max_global:
                return False
            self._counts[project_id] += 1
            return True

    def release(self, project_id: str) -> None:
        """Release a global slot for a project."""
        with self._lock:
            if self._counts[project_id] > 0:
                self._counts[project_id] -= 1

    def running_for_project(self, project_id: str) -> int:
        with self._lock:
            return self._counts.get(project_id, 0)

    def status(self) -> dict:
        """Return current status snapshot."""
        with self._lock:
            per_project = dict(self._counts)
        total = sum(per_project.values())
        return {
            "max_global": self.max_global,
            "total_running": total,
            "available_slots": max(0, self.max_global - total),
            "per_project": per_project,
        }
