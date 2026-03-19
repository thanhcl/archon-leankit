"""Tests for TaskEngine — poll cycle, execution, notification, health."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.architect_reviewer import ArchitectReviewResult, ReviewAction, ReviewConfig
from src.server.services.engine.capacity_tracker import GlobalCapacityTracker
from src.server.services.engine.cc_spawner import CCExecutionResult
from src.server.services.engine.task_engine import TaskEngine


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "title": "Add auth endpoint",
        "description": "Implement login",
        "status": "assigned",
        "priority": "high",
        "assignee": "Agent",
        "source_app": "leankit",
        "acceptance_criteria": [],
        "retry_count": 0,
        "architect_review": None,
        "execution_result": None,
        "rejection_reason": None,
        "execution_prompt": None,
        "created_at": "2026-01-01T00:00:00",
        "reviewed_by": [],
        "review_history": [],
    }
    base.update(overrides)
    return base


def _mock_reviewer_approve():
    return (
        ArchitectReviewResult(verdict="approve", confidence=0.95, summary="Good", mode="self-review"),
        ReviewAction(next_status="review", reason="Approved"),
    )


def _success_result():
    return CCExecutionResult(
        success=True,
        stdout="SELF_REVIEW: PASS\nRESULT: SUCCESS\nFILES_CHANGED: 2\nSUMMARY: Done\n",
        stderr="", exit_code=0, duration_seconds=30.0,
        parsed={"result": "SUCCESS", "files_changed": 2, "summary": "Done", "model_used": "claude-sonnet-4-6"},
    )


def _failure_result():
    return CCExecutionResult(
        success=False,
        stdout="RESULT: FAILURE\nSUMMARY: Build broke\n",
        stderr="error: test failed", exit_code=1, duration_seconds=15.0,
        parsed={"result": "FAILURE", "summary": "Build broke", "model_used": "claude-sonnet-4-6"},
    )


def _code_review_approve_result():
    """CC result for an independent code review that approves."""
    return CCExecutionResult(
        success=True,
        stdout="CODE_REVIEW_VERDICT: APPROVE\nCODE_REVIEW_FINDINGS: []\n",
        stderr="", exit_code=0, duration_seconds=10.0,
        parsed={"code_review_verdict": "APPROVE", "code_review_findings": [], "model_used": "claude-sonnet-4-6"},
    )


def _setup_engine():
    """Create a TaskEngine with all services mocked."""
    engine = TaskEngine(project_path="/tmp/test")
    engine.lifecycle_service = MagicMock()
    engine.lifecycle_service.execute_transition = AsyncMock(
        return_value=(True, {"task": _make_task(status="executing")}),
    )
    engine.task_service = MagicMock()
    engine.task_service.get_task.return_value = (True, {"task": _make_task()})
    engine.task_service.update_task = AsyncMock(return_value=(True, {}))
    engine.prompt_builder = MagicMock()
    engine.prompt_builder.build = AsyncMock(
        return_value=("test prompt", {"learnings": 0, "patterns": 0, "kb_chunks": 0, "tokens": 100}),
    )
    engine.spawner = MagicMock()
    engine.spawner.has_capacity = True
    engine.spawner.spawn = AsyncMock(return_value=_success_result())
    engine.architect_reviewer = MagicMock()
    engine.architect_reviewer.review = AsyncMock(return_value=_mock_reviewer_approve())
    engine.notifier = MagicMock()
    engine.notifier.on_task_started = AsyncMock()
    engine.notifier.on_task_completed = AsyncMock()
    engine.notifier.on_task_review_ready = AsyncMock()
    engine.notifier.on_task_escalated = AsyncMock()
    engine.notifier.on_task_failed = AsyncMock()
    engine.notifier.on_code_review_changes_requested = AsyncMock()
    engine.health_monitor = MagicMock()
    engine.health_monitor.start = AsyncMock()
    engine.health_monitor.stop = AsyncMock()
    engine.bug_task_creator = MagicMock()
    engine.bug_task_creator.create_bug_tasks_from_findings = AsyncMock(return_value=[])
    return engine


class TestPollCycle:
    @pytest.mark.asyncio
    async def test_poll_picks_assigned_tasks(self):
        engine = _setup_engine()
        engine.task_service.list_tasks.return_value = (True, {"tasks": [_make_task(id="t1")]})
        engine._execute_task = AsyncMock(return_value=_success_result())
        await engine._poll_cycle()
        # list_tasks is called at least once for assigned tasks (may also be called for auto-assign cycle)
        assert engine.task_service.list_tasks.call_count >= 1

    @pytest.mark.asyncio
    async def test_poll_sorts_by_priority(self):
        engine = _setup_engine()
        tasks = [
            _make_task(id="low", priority="low"),
            _make_task(id="critical", priority="critical"),
        ]
        engine.task_service.list_tasks.return_value = (True, {"tasks": tasks})

        launched = []

        async def mock_execute(task):
            launched.append(task["id"])
            return _success_result()

        engine._execute_task = mock_execute
        await engine._poll_cycle()
        if engine._execution_tasks:
            await asyncio.gather(*engine._execution_tasks.values(), return_exceptions=True)
        assert launched[0] == "critical"

    @pytest.mark.asyncio
    async def test_poll_sorts_high_before_medium_before_low(self):
        engine = _setup_engine()
        tasks = [
            _make_task(id="low", priority="low", created_at="2026-01-01T00:00:00"),
            _make_task(id="medium", priority="medium", created_at="2026-01-01T00:00:00"),
            _make_task(id="high", priority="high", created_at="2026-01-01T00:00:00"),
        ]
        engine.task_service.list_tasks.return_value = (True, {"tasks": tasks})

        launched = []

        async def mock_execute(task):
            launched.append(task["id"])
            return _success_result()

        engine._execute_task = mock_execute
        await engine._poll_cycle()
        if engine._execution_tasks:
            await asyncio.gather(*engine._execution_tasks.values(), return_exceptions=True)
        assert launched == ["high", "medium", "low"]

    @pytest.mark.asyncio
    async def test_poll_fifo_within_same_priority(self):
        engine = _setup_engine()
        tasks = [
            _make_task(id="high-later", priority="high", created_at="2026-01-02T00:00:00"),
            _make_task(id="high-earlier", priority="high", created_at="2026-01-01T00:00:00"),
            _make_task(id="medium-only", priority="medium", created_at="2026-01-01T00:00:00"),
        ]
        engine.task_service.list_tasks.return_value = (True, {"tasks": tasks})

        launched = []

        async def mock_execute(task):
            launched.append(task["id"])
            return _success_result()

        engine._execute_task = mock_execute
        await engine._poll_cycle()
        if engine._execution_tasks:
            await asyncio.gather(*engine._execution_tasks.values(), return_exceptions=True)
        assert launched == ["high-earlier", "high-later", "medium-only"]

    @pytest.mark.asyncio
    async def test_poll_handles_failure(self):
        engine = _setup_engine()
        engine.task_service.list_tasks.return_value = (False, {"error": "DB down"})
        await engine._poll_cycle()  # Should not raise


class TestExecuteTask:
    @pytest.mark.asyncio
    async def test_success_full_pipeline(self):
        engine = _setup_engine()
        # First spawn: execution. Second spawn: code review (approves).
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )
        result = await engine._execute_task(_make_task())

        assert result.success is True
        engine.notifier.on_task_started.assert_called_once()
        engine.notifier.on_task_completed.assert_called_once()
        engine.notifier.on_task_review_ready.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_notifies(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_failure_result())
        result = await engine._execute_task(_make_task())

        assert result.success is False
        engine.notifier.on_task_started.assert_called_once()
        engine.notifier.on_task_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_escalation_notifies(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        engine.architect_reviewer.review = AsyncMock(return_value=(
            ArchitectReviewResult(verdict="escalate", confidence=0.0, summary="Human needed"),
            ReviewAction(next_status="escalated", reason="Escalated", escalation_reason="reviewer_escalate"),
        ))
        await engine._execute_task(_make_task())
        engine.notifier.on_task_escalated.assert_called_once()

    @pytest.mark.asyncio
    async def test_stores_execution_result(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        await engine._execute_task(_make_task())

        first_update = engine.task_service.update_task.call_args_list[0]
        update_fields = first_update[1]["update_fields"]
        stored = update_fields["execution_result"]
        assert stored["exit_code"] == 0
        assert stored["result"] == "SUCCESS"

        # Verify executed_by is populated
        executed_by = update_fields["executed_by"]
        assert executed_by["model"] == "claude-sonnet-4-6"
        assert executed_by["session_id"] == "task-001"
        assert executed_by["source_app"] == "claude-code-cli"
        assert executed_by["duration_seconds"] == 30.0

    @pytest.mark.asyncio
    async def test_stores_reviewed_by(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )
        await engine._execute_task(_make_task())

        # Find the update call that stores reviewed_by (second update, after architect review)
        review_update = engine.task_service.update_task.call_args_list[1]
        update_fields = review_update[1]["update_fields"]
        reviewed_by = update_fields["reviewed_by"]
        assert len(reviewed_by) == 1
        assert reviewed_by[0]["stage"] == "architect-review"
        assert reviewed_by[0]["agent"] == "architect-reviewer"
        assert reviewed_by[0]["verdict"] == "approve"
        assert reviewed_by[0]["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_transition_failure_aborts(self):
        engine = _setup_engine()
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(False, {"error": "Already executing"}),
        )
        result = await engine._execute_task(_make_task())
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unified_prompt_no_include_self_review(self):
        """build() is called without include_self_review param (unified template)."""
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        await engine._execute_task(_make_task())

        call_kwargs = engine.prompt_builder.build.call_args[1]
        assert "include_self_review" not in call_kwargs

    @pytest.mark.asyncio
    async def test_stores_review_history(self):
        """Review history array is appended with quality gate score."""
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        engine.bug_task_creator = MagicMock()
        engine.bug_task_creator.create_bug_tasks_from_findings = AsyncMock(return_value=[])
        await engine._execute_task(_make_task())

        # Find the update_task call that contains review_history
        for call in engine.task_service.update_task.call_args_list:
            fields = call[1]["update_fields"]
            if "review_history" in fields:
                history = fields["review_history"]
                assert len(history) == 1
                entry = history[0]
                assert entry["review_number"] == 1
                assert entry["verdict"] == "approve"
                assert "quality_gate" in entry
                assert entry["quality_gate"]["gate_result"] in ("pass", "retry", "escalate")
                assert "review_id" in entry
                assert "reviewed_at" in entry
                return
        pytest.fail("review_history not found in any update_task call")

    @pytest.mark.asyncio
    async def test_review_history_appends_not_overwrites(self):
        """Subsequent reviews append to existing history."""
        engine = _setup_engine()
        engine.bug_task_creator = MagicMock()
        engine.bug_task_creator.create_bug_tasks_from_findings = AsyncMock(return_value=[])
        existing_entry = {
            "review_id": "prev-1", "review_number": 1,
            "verdict": "changes-requested", "confidence": 0.5,
        }
        engine.task_service.get_task.return_value = (
            True, {"task": _make_task(review_history=[existing_entry])}
        )
        await engine._run_architect_review("task-001", {
            "exit_code": 0, "timed_out": False, "result": "SUCCESS",
        })

        for call in engine.task_service.update_task.call_args_list:
            fields = call[1]["update_fields"]
            if "review_history" in fields:
                history = fields["review_history"]
                assert len(history) == 2
                assert history[0]["review_id"] == "prev-1"
                assert history[1]["review_number"] == 2
                return
        pytest.fail("review_history not found in update_task calls")

    @pytest.mark.asyncio
    async def test_quality_gate_score_in_architect_review(self):
        """architect_review data includes quality_gate_score."""
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_success_result())
        engine.bug_task_creator = MagicMock()
        engine.bug_task_creator.create_bug_tasks_from_findings = AsyncMock(return_value=[])
        await engine._execute_task(_make_task())

        for call in engine.task_service.update_task.call_args_list:
            fields = call[1]["update_fields"]
            if "architect_review" in fields:
                review_data = fields["architect_review"]
                assert "quality_gate_score" in review_data
                qg = review_data["quality_gate_score"]
                assert "compound_score" in qg
                assert "gate_result" in qg
                return
        pytest.fail("architect_review with quality_gate_score not found")


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        engine = _setup_engine()
        engine._poll_cycle = AsyncMock()
        await engine.start()
        assert engine._running is True
        engine.health_monitor.start.assert_called_once()
        await engine.stop()
        assert engine._running is False
        engine.health_monitor.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_reap_completed(self):
        engine = _setup_engine()
        done_task = MagicMock()
        done_task.done.return_value = True
        running_task = MagicMock()
        running_task.done.return_value = False
        engine._execution_tasks = {"done-1": done_task, "running-1": running_task}
        engine._reap_completed()
        assert "done-1" not in engine._execution_tasks
        assert "running-1" in engine._execution_tasks


class TestGlobalCapacityIntegration:
    @pytest.mark.asyncio
    async def test_poll_respects_global_limit(self):
        """Engine should not spawn tasks when global tracker is at capacity."""
        tracker = GlobalCapacityTracker(max_global=1)
        tracker.try_acquire("other-project")  # Fill the global slot

        engine = _setup_engine()
        engine.project_id = "proj-1"
        engine.global_tracker = tracker
        engine.task_service.list_tasks.return_value = (True, {"tasks": [_make_task()]})
        engine._execute_task = AsyncMock(return_value=_success_result())

        await engine._poll_cycle()
        engine._execute_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_acquires_global_slot(self):
        """Engine should acquire a global slot when spawning a task."""
        tracker = GlobalCapacityTracker(max_global=5)

        engine = _setup_engine()
        engine.project_id = "proj-1"
        engine.global_tracker = tracker
        engine.task_service.list_tasks.return_value = (True, {"tasks": [_make_task(id="t1")]})
        engine._execute_task = AsyncMock(return_value=_success_result())

        await engine._poll_cycle()
        assert tracker.running_for_project("proj-1") == 1

    @pytest.mark.asyncio
    async def test_reap_releases_global_slot(self):
        """Completed tasks should release global slots."""
        tracker = GlobalCapacityTracker(max_global=5)
        tracker.try_acquire("proj-1")

        engine = _setup_engine()
        engine.project_id = "proj-1"
        engine.global_tracker = tracker

        done_task = MagicMock()
        done_task.done.return_value = True
        engine._execution_tasks = {"t1": done_task}

        engine._reap_completed()
        assert tracker.running_for_project("proj-1") == 0

    @pytest.mark.asyncio
    async def test_poll_without_global_tracker(self):
        """Engine should work fine without a global tracker (backward compat)."""
        engine = _setup_engine()
        engine.global_tracker = None
        engine.task_service.list_tasks.return_value = (True, {"tasks": [_make_task(id="t1")]})
        engine._execute_task = AsyncMock(return_value=_success_result())

        await engine._poll_cycle()
        assert len(engine._execution_tasks) == 1

    def test_has_capacity_checks_both(self):
        """_has_capacity should check both per-project and global limits."""
        tracker = GlobalCapacityTracker(max_global=1)

        engine = _setup_engine()
        engine.global_tracker = tracker

        # Per-project has capacity, global has capacity
        assert engine._has_capacity()

        # Fill global
        tracker.try_acquire("other")
        assert not engine._has_capacity()

        # Release global, disable per-project
        tracker.release("other")
        engine.spawner.has_capacity = False
        assert not engine._has_capacity()

    def test_get_status(self):
        """get_status should return correct snapshot."""
        engine = _setup_engine()
        engine.project_id = "proj-1"
        engine._running = True
        engine.spawner.max_parallel = 3
        engine.spawner.running_count = 1
        engine._poll_cycle_count = 10
        engine._execution_tasks = {"t1": MagicMock()}

        status = engine.get_status()
        assert status["project_id"] == "proj-1"
        assert status["running"] is True
        assert status["max_parallel"] == 3
        assert status["slots_used"] == 1
        assert status["slots_available"] == 2
        assert status["active_tasks"] == ["t1"]
        assert status["poll_cycles"] == 10


class TestCodeReviewWiring:
    """Tests for independent code review wiring into engine pipeline."""

    @pytest.mark.asyncio
    async def test_code_review_populates_review_history(self):
        """Code review should append to review_history array."""
        engine = _setup_engine()
        # First spawn: execution. Second spawn: code review (approves).
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )
        await engine._execute_task(_make_task())

        # Find the update call that stores review_history from code review
        found_code_review_history = False
        for call in engine.task_service.update_task.call_args_list:
            fields = call[1]["update_fields"]
            if "code_review" in fields and "review_history" in fields:
                history = fields["review_history"]
                # Should have architect review entry + code review entry
                code_review_entries = [
                    h for h in history if h.get("mode") == "independent-review"
                ]
                assert len(code_review_entries) >= 1
                entry = code_review_entries[0]
                assert entry["verdict"] == "approve"
                assert "quality_gate" in entry
                found_code_review_history = True
                break
        assert found_code_review_history, "Code review did not populate review_history"

    @pytest.mark.asyncio
    async def test_code_review_populates_reviewed_by(self):
        """Code review should append to reviewed_by with model name."""
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )
        await engine._execute_task(_make_task())

        # Find the code review update
        for call in engine.task_service.update_task.call_args_list:
            fields = call[1]["update_fields"]
            if "code_review" in fields and "reviewed_by" in fields:
                reviewed_by = fields["reviewed_by"]
                code_review_entries = [
                    r for r in reviewed_by if r.get("stage") == "code-review"
                ]
                assert len(code_review_entries) == 1
                entry = code_review_entries[0]
                assert entry["agent"] == "code-reviewer"
                assert entry["model"]  # model name should be set
                assert entry["mode"] == "independent-review"
                return
        pytest.fail("Code review did not populate reviewed_by")

    @pytest.mark.asyncio
    async def test_code_review_computes_quality_score(self):
        """Code review should compute and store quality_score."""
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )
        await engine._execute_task(_make_task())

        for call in engine.task_service.update_task.call_args_list:
            fields = call[1]["update_fields"]
            if "code_review" in fields and "quality_score" in fields:
                assert isinstance(fields["quality_score"], float)
                assert 0 <= fields["quality_score"] <= 100
                # Also check quality_gate_score in code_review data
                assert "quality_gate_score" in fields["code_review"]
                return
        pytest.fail("Code review did not compute quality_score")

    @pytest.mark.asyncio
    async def test_code_review_persists_review_cycle(self):
        """review_cycle should be stored at task top level."""
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )
        await engine._execute_task(_make_task())

        for call in engine.task_service.update_task.call_args_list:
            fields = call[1]["update_fields"]
            if "review_cycle" in fields:
                assert fields["review_cycle"] == 1
                return
        pytest.fail("review_cycle not stored at task top level")

    @pytest.mark.asyncio
    async def test_code_review_request_changes_triggers_retry(self):
        """REQUEST_CHANGES verdict should send task back to assigned."""
        engine = _setup_engine()
        changes_result = CCExecutionResult(
            success=True,
            stdout='CODE_REVIEW_VERDICT: REQUEST_CHANGES\nCODE_REVIEW_FINDINGS: [{"severity":"warning","category":"tests","description":"Missing edge case test"}]\n',
            stderr="", exit_code=0, duration_seconds=10.0,
            parsed={
                "code_review_verdict": "REQUEST_CHANGES",
                "code_review_findings": [{"severity": "warning", "category": "tests", "description": "Missing edge case test"}],
                "model_used": "claude-sonnet-4-6",
            },
        )
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), changes_result]
        )
        await engine._execute_task(_make_task())

        # Should transition to assigned (auto-retry)
        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        statuses = [c[1]["new_status"] for c in transition_calls]
        assert "assigned" in statuses
        engine.notifier.on_code_review_changes_requested.assert_called_once()

    @pytest.mark.asyncio
    async def test_code_review_critical_at_max_cycles_escalates(self):
        """Critical findings at max review cycles should escalate."""
        engine = _setup_engine()
        task = _make_task(review_cycle=1)  # Already at cycle 1, max is 2
        engine.task_service.get_task.return_value = (True, {"task": task})

        critical_result = CCExecutionResult(
            success=True,
            stdout='CODE_REVIEW_VERDICT: REQUEST_CHANGES\nCODE_REVIEW_FINDINGS: [{"severity":"critical","category":"security","description":"SQL injection"}]\n',
            stderr="", exit_code=0, duration_seconds=10.0,
            parsed={
                "code_review_verdict": "REQUEST_CHANGES",
                "code_review_findings": [{"severity": "critical", "category": "security", "description": "SQL injection"}],
                "model_used": "claude-sonnet-4-6",
            },
        )
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), critical_result]
        )
        await engine._execute_task(task)
        engine.notifier.on_task_escalated.assert_called()

    @pytest.mark.asyncio
    async def test_independent_review_disabled_skips_code_review(self):
        """When independent_review_enabled=False, skip code review and go to review."""
        engine = _setup_engine()
        engine.review_config = ReviewConfig(independent_review_enabled=False)
        engine.spawner.spawn = AsyncMock(return_value=_success_result())

        await engine._execute_task(_make_task())

        # Should transition directly to review, not code-review
        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        statuses = [c[1]["new_status"] for c in transition_calls]
        assert "review" in statuses
        assert "code-review" not in statuses
        # Spawner should only be called once (execution, not review)
        assert engine.spawner.spawn.call_count == 1
        engine.notifier.on_task_review_ready.assert_called_once()

    @pytest.mark.asyncio
    async def test_independent_review_enabled_routes_through_code_review(self):
        """When independent_review_enabled=True (default), route through code-review."""
        engine = _setup_engine()
        engine.review_config = ReviewConfig(independent_review_enabled=True)
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )

        await engine._execute_task(_make_task())

        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        statuses = [c[1]["new_status"] for c in transition_calls]
        assert "code-review" in statuses
        # Spawner should be called twice (execution + review)
        assert engine.spawner.spawn.call_count == 2

    @pytest.mark.asyncio
    async def test_max_review_cycles_escalates(self):
        """Exceeding MAX_CODE_REVIEW_CYCLES should escalate."""
        engine = _setup_engine()
        task = _make_task(review_cycle=2)  # At max (MAX_CODE_REVIEW_CYCLES=2)
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.spawner.spawn = AsyncMock(return_value=_success_result())

        await engine._execute_task(task)

        # Should escalate without spawning code review
        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        statuses = [c[1]["new_status"] for c in transition_calls]
        assert "escalated" in statuses
        engine.notifier.on_task_escalated.assert_called()
