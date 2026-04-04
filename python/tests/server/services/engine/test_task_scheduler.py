"""Tests for priority-weighted task scheduling (C-P5-01)."""

from datetime import datetime, timedelta, timezone

import pytest

from src.server.services.engine.task_scheduler import (
    AGE_MINUTES_MULTIPLIER,
    BLOCKING_IMPACT_MULTIPLIER,
    PRIORITY_WEIGHT,
    RETRY_PENALTY_MULTIPLIER,
    compute_blocking_counts,
    compute_task_score,
    score_and_sort_tasks,
)


def _make_task(
    task_id: str = "t1",
    priority: str = "medium",
    created_at: str | None = None,
    retry_count: int = 0,
    status: str = "assigned",
    blocked_by: list[str] | None = None,
) -> dict:
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    return {
        "id": task_id,
        "priority": priority,
        "created_at": created_at,
        "retry_count": retry_count,
        "status": status,
        "blocked_by": blocked_by or [],
    }


class TestComputeBlockingCounts:
    def test_empty_tasks(self):
        assert compute_blocking_counts([]) == {}

    def test_no_dependencies(self):
        tasks = [_make_task("t1"), _make_task("t2")]
        assert compute_blocking_counts(tasks) == {}

    def test_single_blocker(self):
        tasks = [
            _make_task("t1"),
            _make_task("t2", blocked_by=["t1"]),
        ]
        counts = compute_blocking_counts(tasks)
        assert counts["t1"] == 1

    def test_multiple_blockers(self):
        tasks = [
            _make_task("t1"),
            _make_task("t2", blocked_by=["t1"]),
            _make_task("t3", blocked_by=["t1"]),
            _make_task("t4", blocked_by=["t1", "t2"]),
        ]
        counts = compute_blocking_counts(tasks)
        assert counts["t1"] == 3  # blocks t2, t3, t4
        assert counts["t2"] == 1  # blocks t4

    def test_terminal_tasks_excluded(self):
        tasks = [
            _make_task("t1"),
            _make_task("t2", blocked_by=["t1"], status="done"),
            _make_task("t3", blocked_by=["t1"], status="cancelled"),
        ]
        counts = compute_blocking_counts(tasks)
        # t2 and t3 are terminal, so t1 doesn't get blocking credit
        assert counts.get("t1", 0) == 0


class TestComputeTaskScore:
    def test_priority_weights(self):
        blocking = {}
        critical = compute_task_score(_make_task(priority="critical"), blocking)
        high = compute_task_score(_make_task(priority="high"), blocking)
        medium = compute_task_score(_make_task(priority="medium"), blocking)
        low = compute_task_score(_make_task(priority="low"), blocking)

        assert critical > high > medium > low

    def test_blocking_impact_increases_score(self):
        t = _make_task("t1", priority="medium")
        score_no_block = compute_task_score(t, {})
        score_blocks_3 = compute_task_score(t, {"t1": 3})

        assert score_blocks_3 > score_no_block
        assert score_blocks_3 - score_no_block == pytest.approx(
            3 * BLOCKING_IMPACT_MULTIPLIER, abs=0.1
        )

    def test_age_increases_score(self):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        new_time = datetime.now(timezone.utc).isoformat()

        old_task = _make_task(created_at=old_time)
        new_task = _make_task(created_at=new_time)

        score_old = compute_task_score(old_task, {})
        score_new = compute_task_score(new_task, {})

        assert score_old > score_new

    def test_retry_penalty_decreases_score(self):
        fresh = _make_task(retry_count=0)
        retried = _make_task(retry_count=3)

        score_fresh = compute_task_score(fresh, {})
        score_retried = compute_task_score(retried, {})

        assert score_fresh > score_retried
        expected_penalty = 3 * RETRY_PENALTY_MULTIPLIER
        assert score_fresh - score_retried == pytest.approx(expected_penalty, abs=0.5)

    def test_high_blocking_beats_higher_priority(self):
        """A medium task blocking 5 others should outscore a high task blocking none."""
        medium_blocker = _make_task("t1", priority="medium")
        high_standalone = _make_task("t2", priority="high")

        score_medium = compute_task_score(medium_blocker, {"t1": 5})
        score_high = compute_task_score(high_standalone, {})

        # medium(20) + 5*5(25) = 45 > high(30) + 0 = 30
        assert score_medium > score_high


class TestScoreAndSortTasks:
    def test_fifo_mode_preserves_priority_order(self):
        tasks = [
            _make_task("t3", priority="low"),
            _make_task("t1", priority="critical"),
            _make_task("t2", priority="high"),
        ]
        result = score_and_sort_tasks(tasks, mode="fifo")
        assert [t["id"] for t in result] == ["t1", "t2", "t3"]

    def test_priority_weighted_mode_uses_scoring(self):
        now = datetime.now(timezone.utc).isoformat()
        tasks = [
            _make_task("t_low", priority="low", created_at=now),
            _make_task("t_critical", priority="critical", created_at=now),
            _make_task("t_medium", priority="medium", created_at=now),
        ]
        result = score_and_sort_tasks(tasks, mode="priority_weighted")
        assert result[0]["id"] == "t_critical"
        assert result[-1]["id"] == "t_low"

    def test_priority_weighted_considers_blocking(self):
        now = datetime.now(timezone.utc).isoformat()
        # medium task blocks 5 others
        all_tasks = [
            _make_task("blocker", priority="medium", created_at=now),
            _make_task("high_solo", priority="high", created_at=now),
            _make_task("c1", blocked_by=["blocker"]),
            _make_task("c2", blocked_by=["blocker"]),
            _make_task("c3", blocked_by=["blocker"]),
            _make_task("c4", blocked_by=["blocker"]),
            _make_task("c5", blocked_by=["blocker"]),
        ]
        executable = [
            _make_task("blocker", priority="medium", created_at=now),
            _make_task("high_solo", priority="high", created_at=now),
        ]
        result = score_and_sort_tasks(executable, all_tasks, mode="priority_weighted")
        # blocker should come first because it unblocks 5 tasks
        assert result[0]["id"] == "blocker"

    def test_empty_tasks(self):
        assert score_and_sort_tasks([], mode="priority_weighted") == []

    def test_unknown_mode_falls_back_to_fifo(self):
        tasks = [
            _make_task("t1", priority="high"),
            _make_task("t2", priority="low"),
        ]
        result = score_and_sort_tasks(tasks, mode="unknown_mode")
        assert result[0]["id"] == "t1"

    def test_score_attached_to_task(self):
        tasks = [_make_task("t1")]
        result = score_and_sort_tasks(tasks, mode="priority_weighted")
        assert "_scheduling_score" in result[0]
        assert isinstance(result[0]["_scheduling_score"], float)

    def test_ten_tasks_mixed_priority_correct_order(self):
        """Integration test: 10 tasks with varied priorities, retries, and blocking."""
        now = datetime.now(timezone.utc)
        tasks = []
        all_tasks = []

        # Create 10 tasks with varied attributes
        for i in range(10):
            priority = ["critical", "high", "medium", "low"][i % 4]
            retry_count = i % 3
            created = (now - timedelta(minutes=i * 10)).isoformat()
            t = _make_task(f"t{i}", priority=priority, created_at=created, retry_count=retry_count)
            tasks.append(t)
            all_tasks.append(t)

        # t0 blocks several tasks
        for i in range(5, 10):
            all_tasks.append(_make_task(f"dep{i}", blocked_by=["t0"]))

        result = score_and_sort_tasks(tasks, all_tasks, mode="priority_weighted")

        # Verify ordering: each task should have >= score of next task
        for i in range(len(result) - 1):
            assert result[i]["_scheduling_score"] >= result[i + 1]["_scheduling_score"], (
                f"Task {result[i]['id']} (score={result[i]['_scheduling_score']}) "
                f"should >= Task {result[i+1]['id']} (score={result[i+1]['_scheduling_score']})"
            )
