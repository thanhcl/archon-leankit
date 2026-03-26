"""
Global Capacity Tracker and Shared Agent Pool for LeanKit V3 Task Engine.

GlobalCapacityTracker enforces cross-project parallel execution limits by
tracking total running tasks across all project engines.

SharedAgentPool extends this with named pools that support per-project slot
limits and priority-based allocation to prevent starvation.

Usage:
    tracker = GlobalCapacityTracker(max_global=10)
    if tracker.try_acquire("project-123"):
        # ... spawn task ...
        tracker.release("project-123")

    pool = SharedAgentPool(pool_id="shared", total_slots=5,
                           per_project_limits={"proj-a": 2, "proj-b": 3})
    if pool.try_acquire("proj-a", priority="high"):
        # ... spawn task ...
        pool.release("proj-a")
"""

import threading
from collections import defaultdict

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Tasks at or above this priority can use priority-reserved slots.
_HIGH_PRIORITY_LEVELS = {"critical", "high"}
_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


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


class SharedAgentPool:
    """Named pool of execution slots shared across multiple projects.

    Enforces three constraints when a slot is requested:
    1. Per-project limit — a project cannot exceed its configured slot quota.
    2. Pool capacity — the total running count cannot exceed ``total_slots``.
    3. Priority reserve — the last ``priority_reserve`` slots are only granted
       to tasks with ``critical`` or ``high`` priority, ensuring high-urgency
       work is never blocked by a saturated pool.

    Anti-starvation: because every project has its own per-project limit that
    is less than ``total_slots``, no single project can monopolize the pool.
    Projects without an explicit limit receive ``default_project_slots``.

    Usage::

        pool = SharedAgentPool(
            pool_id="shared",
            total_slots=5,
            per_project_limits={"proj-a": 2, "proj-b": 3},
            priority_reserve=1,
        )
        if pool.try_acquire("proj-a", priority="high"):
            try:
                ...
            finally:
                pool.release("proj-a")
    """

    def __init__(
        self,
        pool_id: str,
        total_slots: int,
        per_project_limits: dict[str, int] | None = None,
        default_project_slots: int = 1,
        priority_reserve: int = 1,
    ):
        if total_slots < 1:
            raise ValueError(f"SharedAgentPool '{pool_id}': total_slots must be >= 1, got {total_slots}")
        if priority_reserve < 0:
            raise ValueError(f"SharedAgentPool '{pool_id}': priority_reserve must be >= 0, got {priority_reserve}")
        if priority_reserve >= total_slots:
            raise ValueError(
                f"SharedAgentPool '{pool_id}': priority_reserve ({priority_reserve}) "
                f"must be less than total_slots ({total_slots})"
            )

        self.pool_id = pool_id
        self.total_slots = total_slots
        self.per_project_limits: dict[str, int] = per_project_limits or {}
        self.default_project_slots = max(1, default_project_slots)
        self.priority_reserve = priority_reserve
        self._lock = threading.Lock()
        self._counts: dict[str, int] = defaultdict(int)

    def _project_limit(self, project_id: str) -> int:
        return self.per_project_limits.get(project_id, self.default_project_slots)

    @property
    def total_running(self) -> int:
        with self._lock:
            return sum(self._counts.values())

    @property
    def available_slots(self) -> int:
        return max(0, self.total_slots - self.total_running)

    def has_capacity(self, project_id: str, priority: str = "medium") -> bool:
        """Return True if this project could acquire a slot at the given priority."""
        with self._lock:
            total_running = sum(self._counts.values())
            project_running = self._counts[project_id]
            if project_running >= self._project_limit(project_id):
                return False
            effective_limit = self.total_slots if priority in _HIGH_PRIORITY_LEVELS else (self.total_slots - self.priority_reserve)
            return total_running < effective_limit

    def try_acquire(self, project_id: str, priority: str = "medium") -> bool:
        """Try to acquire a pool slot for a project.

        Grants a slot only when all three constraints are satisfied:
        - project is below its per-project limit
        - pool has capacity (with priority reserve applied for normal tasks)

        Returns True if a slot was acquired, False otherwise.
        """
        with self._lock:
            total_running = sum(self._counts.values())
            project_running = self._counts[project_id]
            project_limit = self._project_limit(project_id)

            if project_running >= project_limit:
                logger.debug(
                    f"Pool '{self.pool_id}': per-project limit reached | "
                    f"project_id={project_id} | running={project_running} | limit={project_limit}"
                )
                return False

            # High/critical priority tasks can use the reserved slots;
            # normal tasks see a reduced effective limit.
            effective_limit = (
                self.total_slots if priority in _HIGH_PRIORITY_LEVELS
                else self.total_slots - self.priority_reserve
            )
            if total_running >= effective_limit:
                logger.debug(
                    f"Pool '{self.pool_id}': pool capacity reached | "
                    f"total_running={total_running} | effective_limit={effective_limit} | priority={priority}"
                )
                return False

            self._counts[project_id] += 1
            logger.debug(
                f"Pool '{self.pool_id}': slot acquired | "
                f"project_id={project_id} | total_running={total_running + 1} | priority={priority}"
            )
            return True

    def release(self, project_id: str) -> None:
        """Release a pool slot for a project."""
        with self._lock:
            if self._counts[project_id] > 0:
                self._counts[project_id] -= 1

    def running_for_project(self, project_id: str) -> int:
        with self._lock:
            return self._counts.get(project_id, 0)

    def status(self) -> dict:
        """Return current pool status snapshot."""
        with self._lock:
            per_project = dict(self._counts)
        total = sum(per_project.values())
        return {
            "pool_id": self.pool_id,
            "total_slots": self.total_slots,
            "total_running": total,
            "available_slots": max(0, self.total_slots - total),
            "priority_reserve": self.priority_reserve,
            "per_project_limits": dict(self.per_project_limits),
            "default_project_slots": self.default_project_slots,
            "per_project_running": per_project,
        }
