"""Tests for ConversationLockManager — per-task locks with concurrency limits."""

import asyncio
import time

import pytest

from src.server.services.engine.conversation_lock import (
    ConversationLockManager,
    LockInfo,
    get_conversation_lock_manager,
)


@pytest.fixture
def lock_mgr() -> ConversationLockManager:
    return ConversationLockManager(max_concurrent=3)


class TestAcquireRelease:
    """Test basic lock acquire and release."""

    @pytest.mark.asyncio
    async def test_acquire_and_release(self, lock_mgr: ConversationLockManager) -> None:
        async with lock_mgr.acquire("task-1", holder="test") as info:
            assert info.task_id == "task-1"
            assert info.holder == "test"
            assert lock_mgr.active_count == 1
            assert lock_mgr.is_locked("task-1") is True

        # Released
        assert lock_mgr.active_count == 0
        assert lock_mgr.is_locked("task-1") is False

    @pytest.mark.asyncio
    async def test_multiple_tasks(self, lock_mgr: ConversationLockManager) -> None:
        async with lock_mgr.acquire("task-1"):
            async with lock_mgr.acquire("task-2"):
                assert lock_mgr.active_count == 2

        assert lock_mgr.active_count == 0

    @pytest.mark.asyncio
    async def test_same_task_sequential(self, lock_mgr: ConversationLockManager) -> None:
        """Same task can be locked again after release."""
        async with lock_mgr.acquire("task-1"):
            pass
        async with lock_mgr.acquire("task-1"):
            pass
        assert lock_mgr.active_count == 0


class TestConcurrencyLimit:
    """Test global concurrency limits."""

    @pytest.mark.asyncio
    async def test_respects_max_concurrent(self) -> None:
        lock_mgr = ConversationLockManager(max_concurrent=2)
        acquired_order: list[str] = []

        async def acquire_task(tid: str, delay: float = 0.05):
            async with lock_mgr.acquire(tid):
                acquired_order.append(f"start-{tid}")
                await asyncio.sleep(delay)
                acquired_order.append(f"end-{tid}")

        # Start 3 tasks, but only 2 can run concurrently
        tasks = [
            asyncio.create_task(acquire_task("a")),
            asyncio.create_task(acquire_task("b")),
            asyncio.create_task(acquire_task("c")),
        ]

        await asyncio.gather(*tasks)

        # c should start after either a or b ends
        c_start_idx = acquired_order.index("start-c")
        a_end_idx = acquired_order.index("end-a")
        b_end_idx = acquired_order.index("end-b")
        assert c_start_idx > min(a_end_idx, b_end_idx)

    @pytest.mark.asyncio
    async def test_available_slots(self) -> None:
        lock_mgr = ConversationLockManager(max_concurrent=3)
        assert lock_mgr.available_slots == 3

        async with lock_mgr.acquire("task-1"):
            assert lock_mgr.available_slots == 2
            async with lock_mgr.acquire("task-2"):
                assert lock_mgr.available_slots == 1


class TestTimeout:
    """Test lock acquisition timeout."""

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        lock_mgr = ConversationLockManager(max_concurrent=1)

        async with lock_mgr.acquire("task-1"):
            with pytest.raises(asyncio.TimeoutError):
                async with lock_mgr.acquire("task-2", timeout=0.1):
                    pass  # Should not reach here


class TestLockInfo:
    """Test LockInfo properties."""

    def test_age(self) -> None:
        info = LockInfo(task_id="t-1", acquired_at=time.time() - 10)
        assert info.age_seconds >= 10

    def test_stale(self) -> None:
        info = LockInfo(task_id="t-1", acquired_at=time.time() - 7200)
        assert info.is_stale is True

    def test_not_stale(self) -> None:
        info = LockInfo(task_id="t-1")
        assert info.is_stale is False

    def test_to_dict(self) -> None:
        info = LockInfo(task_id="t-1", holder="engine")
        d = info.to_dict()
        assert d["task_id"] == "t-1"
        assert d["holder"] == "engine"
        assert "age_seconds" in d


class TestGetActiveLocks:
    """Test listing active locks."""

    @pytest.mark.asyncio
    async def test_get_active_locks(self, lock_mgr: ConversationLockManager) -> None:
        async with lock_mgr.acquire("task-1", holder="alice"):
            async with lock_mgr.acquire("task-2", holder="bob"):
                locks = lock_mgr.get_active_locks()
                assert len(locks) == 2
                task_ids = {l["task_id"] for l in locks}
                assert task_ids == {"task-1", "task-2"}


class TestShutdown:
    """Test graceful shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_rejects_new_locks(self, lock_mgr: ConversationLockManager) -> None:
        await lock_mgr.shutdown(grace_seconds=0.1)
        with pytest.raises(RuntimeError, match="shutting down"):
            async with lock_mgr.acquire("task-new"):
                pass

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_drain(self) -> None:
        lock_mgr = ConversationLockManager(max_concurrent=5)

        async def hold_lock():
            async with lock_mgr.acquire("task-1"):
                await asyncio.sleep(0.2)

        task = asyncio.create_task(hold_lock())
        await asyncio.sleep(0.05)
        assert lock_mgr.active_count == 1

        await lock_mgr.shutdown(grace_seconds=1.0)
        await task
        assert lock_mgr.active_count == 0


class TestGetStatus:
    """Test status reporting."""

    @pytest.mark.asyncio
    async def test_status(self, lock_mgr: ConversationLockManager) -> None:
        status = lock_mgr.get_status()
        assert status["max_concurrent"] == 3
        assert status["active_count"] == 0
        assert status["available_slots"] == 3
        assert status["shutting_down"] is False

    @pytest.mark.asyncio
    async def test_status_with_locks(self, lock_mgr: ConversationLockManager) -> None:
        async with lock_mgr.acquire("task-1"):
            status = lock_mgr.get_status()
            assert status["active_count"] == 1
            assert status["available_slots"] == 2


class TestSingleton:
    """Test module-level singleton."""

    def test_singleton_returns_same_instance(self) -> None:
        m1 = get_conversation_lock_manager()
        m2 = get_conversation_lock_manager()
        assert m1 is m2
