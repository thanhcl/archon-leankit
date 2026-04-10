"""
Conversation Lock Manager — Per-task sequential processing with concurrency limits.

Adopted from upstream Archon v0.3.2 ConversationLockManager pattern.
Ensures that only one execution runs for a given task at a time,
with configurable global concurrency limits.

Features:
- Per-task asyncio.Lock for sequential processing
- Global concurrency semaphore (configurable max)
- Lock status reporting for monitoring
- Graceful queue drain on shutdown
- Auto-cleanup of stale locks

Usage:
    lock_mgr = ConversationLockManager(max_concurrent=10)
    async with lock_mgr.acquire("task-123"):
        await execute_task(...)
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_CONCURRENT = 10
LOCK_STALE_SECONDS = 3600  # 1 hour — locks older than this are considered stale


@dataclass
class LockInfo:
    """Metadata about an active lock."""

    task_id: str
    acquired_at: float = field(default_factory=time.time)
    holder: str = ""

    @property
    def age_seconds(self) -> float:
        return time.time() - self.acquired_at

    @property
    def is_stale(self) -> bool:
        return self.age_seconds > LOCK_STALE_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "acquired_at": self.acquired_at,
            "age_seconds": round(self.age_seconds, 1),
            "holder": self.holder,
            "is_stale": self.is_stale,
        }


class ConversationLockManager:
    """Manages per-task locks with global concurrency limits.

    Each task gets its own asyncio.Lock ensuring sequential processing.
    A global semaphore limits total concurrent executions.
    """

    def __init__(
        self,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: dict[str, LockInfo] = {}
        self._shutting_down = False

    @asynccontextmanager
    async def acquire(
        self,
        task_id: str,
        holder: str = "",
        timeout: float | None = None,
    ) -> AsyncGenerator[LockInfo, None]:
        """Acquire both the per-task lock and a global concurrency slot.

        Args:
            task_id: The task to lock.
            holder: Optional identifier for who is holding the lock.
            timeout: Optional timeout in seconds. Raises TimeoutError if exceeded.

        Yields:
            LockInfo about the acquired lock.

        Raises:
            TimeoutError: If the lock cannot be acquired within timeout.
            RuntimeError: If the manager is shutting down.
        """
        if self._shutting_down:
            raise RuntimeError("ConversationLockManager is shutting down")

        # Get or create per-task lock
        if task_id not in self._locks:
            self._locks[task_id] = asyncio.Lock()

        task_lock = self._locks[task_id]
        acquired_successfully = False

        try:
            if timeout is not None:
                # Acquire with timeout
                info = await asyncio.wait_for(
                    self._acquire_both(task_id, task_lock, holder),
                    timeout=timeout,
                )
            else:
                info = await self._acquire_both(task_id, task_lock, holder)

            acquired_successfully = True
            yield info

        finally:
            # Only release if we actually acquired the lock
            if acquired_successfully:
                self._active.pop(task_id, None)
                task_lock.release()
                self._semaphore.release()
                logger.debug(f"Lock released | task_id={task_id} | holder={holder}")

    async def _acquire_both(
        self,
        task_id: str,
        task_lock: asyncio.Lock,
        holder: str,
    ) -> LockInfo:
        """Acquire both the semaphore and the per-task lock."""
        await self._semaphore.acquire()

        try:
            await task_lock.acquire()
        except Exception:
            self._semaphore.release()
            raise

        info = LockInfo(task_id=task_id, holder=holder)
        self._active[task_id] = info
        logger.debug(
            f"Lock acquired | task_id={task_id} | holder={holder} | "
            f"active={len(self._active)}/{self._max_concurrent}"
        )
        return info

    def is_locked(self, task_id: str) -> bool:
        """Check if a task is currently locked."""
        return task_id in self._active

    @property
    def active_count(self) -> int:
        """Number of currently held locks."""
        return len(self._active)

    @property
    def max_concurrent(self) -> int:
        """Maximum allowed concurrent locks."""
        return self._max_concurrent

    @property
    def available_slots(self) -> int:
        """Number of available concurrency slots."""
        return self._max_concurrent - len(self._active)

    def get_active_locks(self) -> list[dict[str, Any]]:
        """List all currently held locks with metadata."""
        return [info.to_dict() for info in self._active.values()]

    def get_stale_locks(self) -> list[dict[str, Any]]:
        """List locks that have been held longer than the stale threshold."""
        return [info.to_dict() for info in self._active.values() if info.is_stale]

    def cleanup_stale(self) -> int:
        """Remove stale lock tracking entries.

        Note: This only cleans up tracking metadata. The actual asyncio.Lock
        is not forcefully released (that would be unsafe). Instead, stale
        locks are reported for operator intervention.
        """
        stale_ids = [tid for tid, info in self._active.items() if info.is_stale]
        for tid in stale_ids:
            self._active.pop(tid, None)
            logger.warning(f"Stale lock cleaned up | task_id={tid}")
        return len(stale_ids)

    async def shutdown(self, grace_seconds: float = 30.0) -> None:
        """Gracefully shut down, waiting for active locks to drain.

        Args:
            grace_seconds: Maximum time to wait for locks to be released.
        """
        self._shutting_down = True
        if not self._active:
            logger.info("ConversationLockManager: no active locks, shut down immediately")
            return

        logger.info(
            f"ConversationLockManager: shutting down, waiting for {len(self._active)} "
            f"active locks (grace={grace_seconds}s)"
        )

        start = time.time()
        while self._active and (time.time() - start) < grace_seconds:
            await asyncio.sleep(0.5)

        remaining = len(self._active)
        if remaining > 0:
            logger.warning(
                f"ConversationLockManager: {remaining} locks still held after grace period"
            )
        else:
            logger.info("ConversationLockManager: all locks drained, shut down complete")

    def get_status(self) -> dict[str, Any]:
        """Get lock manager status for monitoring."""
        return {
            "max_concurrent": self._max_concurrent,
            "active_count": self.active_count,
            "available_slots": self.available_slots,
            "shutting_down": self._shutting_down,
            "stale_count": len(self.get_stale_locks()),
            "active_locks": self.get_active_locks(),
        }


# Module-level singleton
_default_manager: ConversationLockManager | None = None


def get_conversation_lock_manager(max_concurrent: int = DEFAULT_MAX_CONCURRENT) -> ConversationLockManager:
    """Get or create the module-level ConversationLockManager singleton."""
    global _default_manager
    if _default_manager is None:
        _default_manager = ConversationLockManager(max_concurrent=max_concurrent)
    return _default_manager
