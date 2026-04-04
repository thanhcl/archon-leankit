"""Tests for TaskEngine — poll cycle, execution, notification, health."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.architect_reviewer import ArchitectReviewResult, ReviewAction, ReviewConfig
from src.server.services.engine.capacity_tracker import GlobalCapacityTracker
from src.server.services.engine.cc_spawner import CCExecutionResult
from src.server.services.engine.task_engine import TaskEngine, TaskExecutionState


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
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
        "allowed_paths": [],
        "forbidden_paths": [],
        "repo_guidance_packs": [],
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
    engine.engine_policy_service = MagicMock()
    engine.engine_policy_service.get_active_policy.return_value = None
    engine.lifecycle_service = MagicMock()
    engine.lifecycle_service.execute_transition = AsyncMock(
        return_value=(True, {"task": _make_task(status="executing")}),
    )
    engine.task_service = MagicMock()
    engine.task_service.get_task.return_value = (True, {"task": _make_task()})
    engine.task_service.update_task = AsyncMock(return_value=(True, {}))
    engine.execution_run_service = MagicMock()
    engine.execution_run_service.create_run = AsyncMock(
        side_effect=[
            (True, {"run": {"id": "run-execute-001"}}),
            (True, {"run": {"id": "run-architect-001"}}),
            (True, {"run": {"id": "run-code-review-001"}}),
        ]
    )
    engine.execution_run_service.update_run = AsyncMock(return_value=(True, {}))
    engine.prompt_builder = MagicMock()
    engine.prompt_builder.build = AsyncMock(
        return_value=("test prompt", {"learnings": 0, "patterns": 0, "kb_chunks": 0, "tokens": 100}),
    )
    engine.spawner = MagicMock()
    engine.spawner.has_capacity = True
    engine.spawner.source_app = "claude-code-cli"
    engine.spawner.review_model = "claude-sonnet-4-6"
    engine.spawner.select_model.return_value = "claude-sonnet-4-6"
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
    engine.notifier.on_agent_status = AsyncMock()
    engine.health_monitor = MagicMock()
    engine.health_monitor.start = AsyncMock()
    engine.health_monitor.stop = AsyncMock()
    engine._validate_retry_feedback_policy = AsyncMock(return_value=(True, ""))
    engine.bug_task_creator = MagicMock()
    engine.bug_task_creator.create_bug_tasks_from_findings = AsyncMock(return_value=[])
    engine.approval_request_service = MagicMock()
    engine.approval_request_service.list_requests.return_value = (
        True, {"approvals": [], "total_count": 0, "filters_applied": "none"}
    )
    engine.approval_request_service.create_request = AsyncMock(
        return_value=(True, {"approval": {"id": "apr-001", "status": "pending"}})
    )
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

        async def mock_execute(task, **_kwargs):
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

        async def mock_execute(task, **_kwargs):
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

        async def mock_execute(task, **_kwargs):
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

    @pytest.mark.asyncio
    async def test_poll_queues_predicted_overlap_in_shared_isolation(self):
        engine = _setup_engine()
        task_auth = _make_task(id="auth", title="Auth task", allowed_paths=["src/server/auth/**"])
        task_server = _make_task(
            id="server",
            title="Server task",
            repo_guidance_packs=[
                {
                    "title": "Server discipline",
                    "guidance": "Keep services stable",
                    "path_scope": ["src/server/**"],
                }
            ],
        )

        def _list_tasks(*_, **kwargs):
            status = kwargs.get("status")
            if status == "approved":
                return True, {"tasks": []}
            if status == "assigned":
                return True, {"tasks": [task_auth, task_server]}
            if status == "executing":
                return True, {"tasks": []}
            return True, {"tasks": []}

        engine.task_service.list_tasks.side_effect = _list_tasks

        launched = []

        async def mock_execute(task, **_kwargs):
            launched.append(task["id"])
            return _success_result()

        engine._execute_task = mock_execute
        await engine._poll_cycle()
        if engine._execution_tasks:
            await asyncio.gather(*engine._execution_tasks.values(), return_exceptions=True)
        assert launched == ["auth"]

    @pytest.mark.asyncio
    async def test_poll_allows_overlap_when_git_worktree_isolation_enabled(self):
        engine = _setup_engine()
        engine.project_config.isolation = "git-worktree"
        task_auth = _make_task(id="auth", title="Auth task", allowed_paths=["src/server/auth/**"])
        task_server = _make_task(
            id="server",
            title="Server task",
            repo_guidance_packs=[
                {
                    "title": "Server discipline",
                    "guidance": "Keep services stable",
                    "path_scope": ["src/server/**"],
                }
            ],
        )

        def _list_tasks(*_, **kwargs):
            status = kwargs.get("status")
            if status == "approved":
                return True, {"tasks": []}
            if status == "assigned":
                return True, {"tasks": [task_auth, task_server]}
            if status == "executing":
                return True, {"tasks": []}
            return True, {"tasks": []}

        engine.task_service.list_tasks.side_effect = _list_tasks

        launched = []

        async def mock_execute(task, **_kwargs):
            launched.append(task["id"])
            return _success_result()

        engine._execute_task = mock_execute
        await engine._poll_cycle()
        if engine._execution_tasks:
            await asyncio.gather(*engine._execution_tasks.values(), return_exceptions=True)
        assert launched == ["auth", "server"]


class TestExecuteTask:
    def test_execution_stage_chain_order(self):
        engine = _setup_engine()

        assert [stage.__name__ for stage in engine._build_execute_stage_chain()] == [
            "_stage_claim",
            "_stage_pre_validation",
            "_stage_policy_load",
            "_stage_guidance_injection",
            "_stage_boundary_injection",
            "_stage_workspace_acquire",
            "_stage_lifecycle_restore",
            "_stage_runner_execute",
            "_stage_changed_file_validation",
            "_stage_artifact_capture",
            "_stage_notifier_publish",
        ]

    @pytest.mark.asyncio
    async def test_execute_task_runs_stages_in_documented_order(self):
        engine = _setup_engine()
        stage_calls: list[str] = []
        expected = _success_result()

        def _record_stage(name: str, *, final: bool = False):
            async def _stage(state):
                stage_calls.append(name)
                if final:
                    state.final_result = expected
                return state

            _stage.__name__ = name
            return _stage

        engine._stage_claim = _record_stage("_stage_claim")
        engine._stage_policy_load = _record_stage("_stage_policy_load")
        engine._stage_guidance_injection = _record_stage("_stage_guidance_injection")
        engine._stage_boundary_injection = _record_stage("_stage_boundary_injection")
        engine._stage_workspace_acquire = _record_stage("_stage_workspace_acquire")
        engine._stage_lifecycle_restore = _record_stage("_stage_lifecycle_restore")
        engine._stage_runner_execute = _record_stage("_stage_runner_execute")
        engine._stage_changed_file_validation = _record_stage("_stage_changed_file_validation")
        engine._stage_artifact_capture = _record_stage("_stage_artifact_capture")
        engine._stage_notifier_publish = _record_stage("_stage_notifier_publish", final=True)

        result = await engine._execute_task(_make_task())

        assert result is expected
        assert stage_calls == [
            "_stage_claim",
            "_stage_policy_load",
            "_stage_guidance_injection",
            "_stage_boundary_injection",
            "_stage_workspace_acquire",
            "_stage_lifecycle_restore",
            "_stage_runner_execute",
            "_stage_changed_file_validation",
            "_stage_artifact_capture",
            "_stage_notifier_publish",
        ]

    @pytest.mark.asyncio
    async def test_execute_task_uses_stage_chain_builder_as_single_extension_point(self):
        engine = _setup_engine()
        stage_calls: list[str] = []
        expected = _success_result()

        async def _stage_one(state):
            stage_calls.append("stage_one")
            return state

        async def _stage_appended(state):
            stage_calls.append("stage_appended")
            return state

        async def _stage_terminal(state):
            stage_calls.append("stage_terminal")
            state.final_result = expected
            return state

        engine._build_execute_stage_chain = MagicMock(
            return_value=(_stage_one, _stage_appended, _stage_terminal)
        )

        result = await engine._execute_task(_make_task())

        assert result is expected
        assert stage_calls == ["stage_one", "stage_appended", "stage_terminal"]

    @pytest.mark.asyncio
    async def test_stage_lifecycle_restore_updates_execution_run_heartbeat_from_stream(self):
        engine = _setup_engine()
        full_task = _make_task(id="task-001", status="executing")
        state = TaskExecutionState(
            task=full_task,
            task_id="task-001",
            full_task=full_task,
            runner=engine.spawner,
            execute_run_id="run-execute-001",
        )

        state = await engine._stage_lifecycle_restore(state)
        assert state.stream_callback is not None

        await state.stream_callback("task-001", {"event": "assistant", "message": "working"})

        heartbeat_updates = [
            call
            for call in engine.execution_run_service.update_run.await_args_list
            if call.args[0] == "run-execute-001" and "heartbeat_at" in call.args[1]
        ]
        assert heartbeat_updates

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
        assert engine.execution_run_service.create_run.await_count == 3
        stages = [call.kwargs["stage"] for call in engine.execution_run_service.create_run.await_args_list]
        assert stages == ["execute", "architect-review", "code-review"]
        assert engine.execution_run_service.update_run.await_count == 3
        started_runtime = engine.notifier.on_task_started.await_args.kwargs["runtime"]
        assert started_runtime["execution_run_id"] == "run-execute-001"
        assert started_runtime["stage"] == "execute"
        assert started_runtime["source_app"] == "leankit"
        review_runtime = engine.notifier.on_task_review_ready.await_args.kwargs["runtime"]
        assert review_runtime["execution_run_id"] == "run-code-review-001"
        assert review_runtime["stage"] == "code-review"

    @pytest.mark.asyncio
    async def test_failure_notifies(self):
        engine = _setup_engine()
        engine.spawner.spawn = AsyncMock(return_value=_failure_result())
        result = await engine._execute_task(_make_task())

        assert result.success is False
        engine.notifier.on_task_started.assert_called_once()
        engine.notifier.on_task_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_runner_fails_fast(self):
        engine = _setup_engine()
        engine.task_service.get_task.return_value = (True, {"task": _make_task(runner_key="other-cli")})
        result = await engine._execute_task(_make_task(runner_key="other-cli"))

        assert result.success is False
        assert "Unsupported execution runner: other-cli" in result.stderr
        engine.notifier.on_task_started.assert_not_called()
        engine.notifier.on_task_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_codex_runner_routes_to_registered_adapter(self):
        engine = _setup_engine()
        codex_runner = MagicMock()
        codex_runner.runner_key = "codex-cli"
        codex_runner.source_app = "codex-cli"
        codex_runner.review_model = "codex-default"
        codex_runner.select_model.return_value = "codex-default"
        codex_runner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )
        engine.register_runner(codex_runner)
        engine.task_service.get_task.return_value = (
            True,
            {"task": _make_task(runner_key="codex-cli")},
        )

        result = await engine._execute_task(_make_task(runner_key="codex-cli"))

        assert result.success is True
        assert codex_runner.spawn.await_count == 2
        engine.spawner.spawn.assert_not_called()
        started_runtime = engine.notifier.on_task_started.await_args.kwargs["runtime"]
        assert started_runtime["execution_run_id"] == "run-execute-001"
        executed_by = engine.task_service.update_task.call_args_list[0][1]["update_fields"]["executed_by"]
        assert executed_by["source_app"] == "codex-cli"
        assert executed_by["runner_key"] == "codex-cli"
        assert executed_by["runner_routing_reason"] == "explicit:runner_key"

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"LEANKIT_ENGINE_DISABLE_CODEX": ""})
    async def test_policy_routes_docs_tasks_to_codex(self):
        engine = _setup_engine()
        codex_runner = MagicMock()
        codex_runner.runner_key = "codex-cli"
        codex_runner.source_app = "codex-cli"
        codex_runner.review_model = "codex-default"
        codex_runner.select_model.return_value = "codex-default"
        codex_runner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )
        engine.register_runner(codex_runner)
        engine.task_service.get_task.return_value = (
            True,
            {"task": _make_task(task_type="docs", priority="low", complexity="simple")},
        )

        result = await engine._execute_task(_make_task(task_type="docs", priority="low", complexity="simple"))

        assert result.success is True
        assert codex_runner.spawn.await_count == 2
        executed_by = engine.task_service.update_task.call_args_list[0][1]["update_fields"]["executed_by"]
        assert executed_by["runner_key"] == "codex-cli"
        assert executed_by["runner_routing_reason"] == "policy:codex-preferred-workload"

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"LEANKIT_ENGINE_DISABLE_CODEX": ""})
    async def test_engine_policy_default_runner_routes_execution(self):
        engine = _setup_engine()
        engine.engine_policy_service.get_active_policy.return_value = {
            "model_routing": {"default_runner": "codex-cli"},
        }
        codex_runner = MagicMock()
        codex_runner.runner_key = "codex-cli"
        codex_runner.source_app = "codex-cli"
        codex_runner.review_model = "codex-default"
        codex_runner.select_model.return_value = "codex-default"
        codex_runner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )
        engine.register_runner(codex_runner)
        task = _make_task(task_type="feature", priority="critical", complexity="complex")
        engine.task_service.get_task.return_value = (True, {"task": task})

        result = await engine._execute_task(task)

        assert result.success is True
        assert codex_runner.spawn.await_count == 2
        engine.spawner.spawn.assert_not_called()
        executed_by = engine.task_service.update_task.call_args_list[0][1]["update_fields"]["executed_by"]
        assert executed_by["runner_key"] == "codex-cli"
        assert executed_by["runner_routing_reason"] == "project-policy:default-runner"

    @pytest.mark.asyncio
    async def test_policy_routes_critical_security_tasks_to_claude(self):
        engine = _setup_engine()
        codex_runner = MagicMock()
        codex_runner.runner_key = "codex-cli"
        codex_runner.source_app = "codex-cli"
        codex_runner.review_model = "codex-default"
        codex_runner.select_model.return_value = "codex-default"
        codex_runner.spawn = AsyncMock()
        engine.register_runner(codex_runner)
        task = _make_task(priority="critical", complexity="complex", tags=["security"])
        engine.task_service.get_task.return_value = (True, {"task": task})

        def _list_approved(*, task_id, status, **_kw):
            if status == "approved":
                return (True, {"approvals": [{"id": "apr-001", "status": "approved"}], "total_count": 1, "filters_applied": "none"})
            return (True, {"approvals": [], "total_count": 0, "filters_applied": "none"})

        engine.approval_request_service.list_requests.side_effect = _list_approved
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )

        result = await engine._execute_task(task)

        assert result.success is True
        engine.spawner.spawn.assert_awaited()
        codex_runner.spawn.assert_not_called()
        executed_by = engine.task_service.update_task.call_args_list[0][1]["update_fields"]["executed_by"]
        assert executed_by["runner_key"] == "claude-code-cli"
        assert executed_by["runner_routing_reason"] == "policy:claude-high-risk"

    @pytest.mark.asyncio
    async def test_engine_policy_review_mode_overrides_review_config(self):
        engine = _setup_engine()
        engine.engine_policy_service.get_active_policy.return_value = {
            "review_policy": {"review_mode": "api"},
        }

        result = await engine._execute_task(_make_task())

        assert result.success is True
        review_config = engine.architect_reviewer.review.await_args.kwargs["config"]
        assert review_config.review_mode == "api"
        architect_run = engine.execution_run_service.create_run.await_args_list[1].kwargs
        assert architect_run["metadata"]["mode"] == "api"

    @pytest.mark.asyncio
    async def test_engine_policy_review_policy_overrides_provider_and_model(self):
        engine = _setup_engine()
        engine.engine_policy_service.get_active_policy.return_value = {
            "review_policy": {
                "review_mode": "api",
                "provider": "anthropic",
                "model": "claude-opus-4-6",
                "timeout": 90,
            },
        }

        result = await engine._execute_task(_make_task())

        assert result.success is True
        review_config = engine.architect_reviewer.review.await_args.kwargs["config"]
        assert review_config.review_mode == "api"
        assert review_config.provider == "anthropic"
        assert review_config.model == "claude-opus-4-6"
        assert review_config.timeout == 90

    @pytest.mark.asyncio
    async def test_engine_policy_isolation_mode_overrides_project_config(self):
        engine = _setup_engine()
        engine.engine_policy_service.get_active_policy.return_value = {
            "isolation_policy": {"worktree_mode": "isolated"},
        }

        result = await engine._execute_task(_make_task())

        assert result.success is True
        execute_config = engine.spawner.spawn.await_args_list[0].kwargs["config"]
        review_config = engine.spawner.spawn.await_args_list[1].kwargs["config"]
        assert execute_config.isolation == "git-worktree"
        assert review_config.isolation == "git-worktree"

    @pytest.mark.asyncio
    async def test_boundary_validation_allows_in_scope_changes(self):
        engine = _setup_engine()
        scoped_task = _make_task(allowed_paths=["src/server/**"])
        engine.task_service.get_task.return_value = (True, {"task": scoped_task})
        engine._capture_dirty_file_snapshot = AsyncMock(return_value={"src/server/task_engine.py": "before"})
        engine._collect_changed_files_since = AsyncMock(return_value=["src/server/task_engine.py"])
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )

        result = await engine._execute_task(scoped_task)

        assert result.success is True
        engine.notifier.on_task_completed.assert_called_once()
        update_fields = engine.task_service.update_task.await_args_list[0].kwargs["update_fields"]
        assert update_fields["execution_result"]["boundary_validation"]["status"] == "clean"
        assert update_fields["execution_result"]["changed_files"] == ["src/server/task_engine.py"]

    @pytest.mark.asyncio
    async def test_boundary_violation_escalates_successful_task(self):
        engine = _setup_engine()
        scoped_task = _make_task(
            allowed_paths=["src/server/**"],
            forbidden_paths=["src/store/**"],
        )
        engine.task_service.get_task.return_value = (True, {"task": scoped_task})
        engine._capture_dirty_file_snapshot = AsyncMock(return_value={"src/server/task_engine.py": "before"})
        engine._collect_changed_files_since = AsyncMock(return_value=["src/store/office_store.py"])
        engine.spawner.spawn = AsyncMock(return_value=_success_result())

        result = await engine._execute_task(scoped_task)

        assert result.success is True
        transition_statuses = [
            call.kwargs["new_status"]
            for call in engine.lifecycle_service.execute_transition.await_args_list
        ]
        assert transition_statuses == ["executing", "escalated"]
        engine.notifier.on_task_completed.assert_not_called()
        engine.notifier.on_task_review_ready.assert_not_called()
        engine.notifier.on_task_escalated.assert_called_once()
        update_fields = engine.task_service.update_task.await_args_list[0].kwargs["update_fields"]
        assert update_fields["execution_result"]["boundary_validation"]["status"] == "violation"
        assert update_fields["execution_result"]["changed_files"] == ["src/store/office_store.py"]

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


# ---------------------------------------------------------------------------
# Approval gate tests
# ---------------------------------------------------------------------------


class TestApprovalGate:
    @pytest.mark.asyncio
    async def test_normal_task_skips_approval_gate(self):
        """Non-high-risk tasks proceed without creating an approval request."""
        engine = _setup_engine()
        task = _make_task(priority="medium", tags=["feature"])
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )

        result = await engine._execute_task(task)

        assert result.success is True
        engine.approval_request_service.list_requests.assert_not_called()
        engine.approval_request_service.create_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_risk_task_blocked_when_no_approval_exists(self):
        """Critical + high-risk task is blocked and a pending approval request is created."""
        engine = _setup_engine()
        task = _make_task(priority="critical", tags=["security", "migration"])
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.approval_request_service.list_requests.return_value = (
            True, {"approvals": [], "total_count": 0, "filters_applied": "none"}
        )

        result = await engine._execute_task(task)

        assert result.success is False
        assert result.exit_code == -2
        assert "approval" in result.stderr.lower()
        engine.approval_request_service.create_request.assert_awaited_once()
        create_kwargs = engine.approval_request_service.create_request.call_args.kwargs
        assert create_kwargs["task_id"] == "task-001"
        assert "security" in create_kwargs["context"]["approval_reason"]
        engine.lifecycle_service.execute_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_risk_task_blocked_does_not_duplicate_approval_request(self):
        """When a pending approval already exists, no new request is created."""
        engine = _setup_engine()
        task = _make_task(priority="critical", tags=["security"])
        engine.task_service.get_task.return_value = (True, {"task": task})

        def _list_side_effect(*, task_id, status, **_kw):
            if status == "approved":
                return (True, {"approvals": [], "total_count": 0, "filters_applied": "none"})
            return (True, {"approvals": [{"id": "apr-existing"}], "total_count": 1, "filters_applied": "none"})

        engine.approval_request_service.list_requests.side_effect = _list_side_effect

        result = await engine._execute_task(task)

        assert result.success is False
        assert result.exit_code == -2
        engine.approval_request_service.create_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_risk_task_proceeds_when_approved(self):
        """Execution proceeds when an approved approval request exists for the task."""
        engine = _setup_engine()
        task = _make_task(priority="critical", tags=["security"])
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )

        def _list_approved(*, task_id, status, **_kw):
            if status == "approved":
                return (True, {"approvals": [{"id": "apr-001", "status": "approved"}], "total_count": 1, "filters_applied": "none"})
            return (True, {"approvals": [], "total_count": 0, "filters_applied": "none"})

        engine.approval_request_service.list_requests.side_effect = _list_approved

        result = await engine._execute_task(task)

        assert result.success is True
        engine.lifecycle_service.execute_transition.assert_any_call(
            task_id="task-001", new_status="executing", changed_by="task-engine"
        )

    @pytest.mark.asyncio
    async def test_policy_require_approval_for_tag_blocks_execution(self):
        """Policy require_approval_for tag blocks even non-critical tasks."""
        engine = _setup_engine()
        engine.engine_policy_service.get_active_policy.return_value = {
            "model_routing": {"require_approval_for": ["database"]},
        }
        task = _make_task(priority="medium", tags=["database"])
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.approval_request_service.list_requests.return_value = (
            True, {"approvals": [], "total_count": 0, "filters_applied": "none"}
        )

        result = await engine._execute_task(task)

        assert result.success is False
        assert result.exit_code == -2
        engine.approval_request_service.create_request.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"LEANKIT_ENGINE_DISABLE_CODEX": ""})
    async def test_repo_language_routing_via_policy_metadata(self):
        """Java repo language in model_routing.repo_metadata routes to Codex."""
        engine = _setup_engine()
        codex_runner = MagicMock()
        codex_runner.runner_key = "codex-cli"
        codex_runner.source_app = "codex-cli"
        codex_runner.review_model = "codex-default"
        codex_runner.select_model.return_value = "codex-default"
        codex_runner.spawn = AsyncMock(
            side_effect=[_success_result(), _code_review_approve_result()]
        )
        engine.register_runner(codex_runner)
        engine.engine_policy_service.get_active_policy.return_value = {
            "model_routing": {"repo_metadata": {"language": "java", "framework": "spring"}},
        }
        task = _make_task(task_type="feature", priority="medium")
        engine.task_service.get_task.return_value = (True, {"task": task})

        result = await engine._execute_task(task)

        assert result.success is True
        executed_by = engine.task_service.update_task.call_args_list[0][1]["update_fields"]["executed_by"]
        assert executed_by["runner_key"] == "codex-cli"
        assert "java" in executed_by["runner_routing_reason"]


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


class TestRetryAndEscalationPolicy:
    """Tests for _handle_task_failure, _calculate_retry_delay, and _schedule_retry."""

    def test_calculate_retry_delay_exponential_backoff(self):
        engine = _setup_engine()
        engine._retry_delay = 60
        engine._max_retry_delay = 3600
        assert engine._calculate_retry_delay(0) == 60.0   # 60 * 2^0
        assert engine._calculate_retry_delay(1) == 120.0  # 60 * 2^1
        assert engine._calculate_retry_delay(2) == 240.0  # 60 * 2^2
        assert engine._calculate_retry_delay(10) == 3600.0  # capped at max

    def test_calculate_retry_delay_capped_at_max(self):
        engine = _setup_engine()
        engine._retry_delay = 60
        engine._max_retry_delay = 300
        assert engine._calculate_retry_delay(5) == 300.0  # 60 * 32 = 1920, capped at 300

    @pytest.mark.asyncio
    async def test_handle_failure_schedules_retry_when_attempts_remain(self):
        engine = _setup_engine()
        engine._retry_delay = 0
        engine._max_retry_delay = 3600
        engine._escalation_enabled = True
        task = _make_task(retry_count=0, max_retries=3)

        with patch("src.server.services.engine.task_engine.asyncio.create_task") as mock_create:
            await engine._handle_task_failure("task-001", task, "CC failed")

        # Should transition to failed
        engine.lifecycle_service.execute_transition.assert_called_with(
            task_id="task-001", new_status="failed", changed_by="task-engine", reason="CC failed"
        )
        # Should notify failed
        engine.notifier.on_task_failed.assert_called_once()
        # Should schedule retry background task
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_failure_escalates_when_retries_exhausted_and_escalation_enabled(self):
        engine = _setup_engine()
        engine._escalation_enabled = True
        task = _make_task(retry_count=3, max_retries=3)

        await engine._handle_task_failure("task-001", task, "CC failed")

        # Should transition to escalated, not failed
        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        statuses = [c[1]["new_status"] for c in transition_calls]
        assert "escalated" in statuses
        assert "failed" not in statuses
        # Should send escalation notification
        engine.notifier.on_task_escalated.assert_called_once()
        engine.notifier.on_task_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_failure_stays_failed_when_retries_exhausted_and_escalation_disabled(self):
        engine = _setup_engine()
        engine._escalation_enabled = False
        task = _make_task(retry_count=3, max_retries=3)

        await engine._handle_task_failure("task-001", task, "CC failed")

        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        statuses = [c[1]["new_status"] for c in transition_calls]
        assert "failed" in statuses
        assert "escalated" not in statuses
        engine.notifier.on_task_failed.assert_called_once()
        engine.notifier.on_task_escalated.assert_not_called()

    @pytest.mark.asyncio
    async def test_escalation_reason_includes_max_retries_count(self):
        engine = _setup_engine()
        engine._escalation_enabled = True
        task = _make_task(retry_count=2, max_retries=2)

        await engine._handle_task_failure("task-001", task, "build error")

        call_args = engine.lifecycle_service.execute_transition.call_args
        assert "Max retries (2) exhausted" in call_args[1]["reason"]
        assert "build error" in call_args[1]["reason"]

    @pytest.mark.asyncio
    async def test_handle_failure_marks_failed_then_escalated_when_retry_feedback_missing(self):
        engine = _setup_engine()
        engine._escalation_enabled = True
        engine._validate_retry_feedback_policy = AsyncMock(return_value=(False, "No review feedback"))
        task = _make_task(status="executing", retry_count=1, max_retries=3)

        await engine._handle_task_failure("task-001", task, "CC failed")

        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        statuses = [call.kwargs["new_status"] for call in transition_calls]
        assert statuses[:2] == ["failed", "escalated"]
        engine.notifier.on_task_escalated.assert_called_once()
        engine.notifier.on_task_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_failure_marks_failed_when_retry_feedback_missing_and_escalation_disabled(self):
        engine = _setup_engine()
        engine._escalation_enabled = False
        engine._validate_retry_feedback_policy = AsyncMock(return_value=(False, "No review feedback"))
        task = _make_task(status="executing", retry_count=1, max_retries=3)

        await engine._handle_task_failure("task-001", task, "CC failed")

        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        statuses = [call.kwargs["new_status"] for call in transition_calls]
        assert statuses == ["failed"]
        engine.notifier.on_task_failed.assert_called_once()
        engine.notifier.on_task_escalated.assert_not_called()

    @pytest.mark.asyncio
    async def test_schedule_retry_requeues_task_after_delay(self):
        engine = _setup_engine()
        engine._retry_delay = 0
        engine._max_retry_delay = 3600

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await engine._schedule_retry("task-001", 0, "reason")

        mock_sleep.assert_called_once_with(0.0)
        engine.lifecycle_service.execute_transition.assert_called_once_with(
            task_id="task-001",
            new_status="assigned",
            changed_by="task-engine",
            reason="Auto-retry attempt 1: reason",
        )

    @pytest.mark.asyncio
    async def test_execution_failure_uses_retry_policy(self):
        """Execution failure in _on_cc_complete should apply retry policy."""
        engine = _setup_engine()
        engine._retry_delay = 0
        engine._escalation_enabled = True
        task = _make_task(retry_count=0, max_retries=3)
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.spawner.spawn = AsyncMock(return_value=_failure_result())

        with patch("src.server.services.engine.task_engine.asyncio.create_task") as mock_create:
            await engine._execute_task(task)

        # Failure should transition to failed (not directly to escalated — retries remain)
        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        statuses = [c[1]["new_status"] for c in transition_calls]
        assert "failed" in statuses
        # Retry should be scheduled since retry_count < max_retries
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_execution_failure_escalates_when_retries_exhausted(self):
        """Execution failure with exhausted retries should escalate."""
        engine = _setup_engine()
        engine._escalation_enabled = True
        task = _make_task(retry_count=3, max_retries=3)
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.spawner.spawn = AsyncMock(return_value=_failure_result())

        await engine._execute_task(task)

        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        statuses = [c[1]["new_status"] for c in transition_calls]
        assert "escalated" in statuses
        engine.notifier.on_task_escalated.assert_called()
        engine.notifier.on_task_failed.assert_not_called()


# ---------------------------------------------------------------------------
# TestFilterBlockedTasks — unit tests for _filter_blocked_tasks
# ---------------------------------------------------------------------------


class TestFilterBlockedTasks:
    def _make_db_response(self, rows: list[dict]) -> MagicMock:
        resp = MagicMock()
        resp.data = rows
        return resp

    def _setup_engine_with_blocker_db(self, blocker_rows: list[dict]) -> "TaskEngine":
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()
        engine.task_service.supabase_client.table.return_value.select.return_value.in_.return_value.execute.return_value = (
            self._make_db_response(blocker_rows)
        )
        return engine

    def test_no_blocked_tasks_passes_through(self):
        engine = _setup_engine()
        tasks = [_make_task(id="t1"), _make_task(id="t2")]
        result = engine._filter_blocked_tasks(tasks)
        assert [t["id"] for t in result] == ["t1", "t2"]
        # No DB call needed when no blocked_by fields exist
        engine.task_service.supabase_client.table.assert_not_called()

    def test_task_with_done_blocker_is_executable(self):
        engine = self._setup_engine_with_blocker_db([{"id": "blocker-1", "status": "done"}])
        task = _make_task(id="t1", blocked_by=["blocker-1"])
        result = engine._filter_blocked_tasks([task])
        assert [t["id"] for t in result] == ["t1"]

    def test_task_with_undone_blocker_is_filtered(self):
        engine = self._setup_engine_with_blocker_db([{"id": "blocker-1", "status": "doing"}])
        task = _make_task(id="t1", blocked_by=["blocker-1"])
        result = engine._filter_blocked_tasks([task])
        assert result == []

    def test_task_with_multiple_blockers_all_done_is_executable(self):
        engine = self._setup_engine_with_blocker_db([
            {"id": "b1", "status": "done"},
            {"id": "b2", "status": "done"},
        ])
        task = _make_task(id="t1", blocked_by=["b1", "b2"])
        result = engine._filter_blocked_tasks([task])
        assert [t["id"] for t in result] == ["t1"]

    def test_task_with_partial_done_blockers_is_filtered(self):
        engine = self._setup_engine_with_blocker_db([
            {"id": "b1", "status": "done"},
            {"id": "b2", "status": "todo"},
        ])
        task = _make_task(id="t1", blocked_by=["b1", "b2"])
        result = engine._filter_blocked_tasks([task])
        assert result == []

    def test_unknown_blocker_id_treated_as_unresolved(self):
        """A blocker not found in DB should be treated as not done (fail safe)."""
        engine = self._setup_engine_with_blocker_db([])  # blocker not found
        task = _make_task(id="t1", blocked_by=["ghost-id"])
        result = engine._filter_blocked_tasks([task])
        assert result == []

    def test_db_exception_returns_empty_list(self):
        """On DB failure, return empty list to avoid spawning unvalidated tasks."""
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()
        engine.task_service.supabase_client.table.return_value.select.return_value.in_.return_value.execute.side_effect = Exception(
            "DB connection lost"
        )
        task = _make_task(id="t1", blocked_by=["b1"])
        result = engine._filter_blocked_tasks([task])
        assert result == []

    def test_mix_of_blocked_and_free_tasks(self):
        """Free tasks (no blocked_by) are always executable regardless of blocker statuses."""
        engine = self._setup_engine_with_blocker_db([{"id": "b1", "status": "doing"}])
        free_task = _make_task(id="free")
        blocked_task = _make_task(id="blocked", blocked_by=["b1"])
        result = engine._filter_blocked_tasks([free_task, blocked_task])
        assert [t["id"] for t in result] == ["free"]


# ---------------------------------------------------------------------------
# TestRecheckBlockedBy — race condition guard before spawn
# ---------------------------------------------------------------------------


class TestRecheckBlockedBy:
    def _setup_engine_with_blocker_db(self, blocker_rows: list[dict]) -> "TaskEngine":
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()
        engine.task_service.supabase_client.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(
            data=blocker_rows
        )
        return engine

    def test_no_blockers_returns_empty(self):
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()
        result = engine._recheck_blocked_by("t1", [])
        assert result == []

    def test_all_blockers_done_returns_empty(self):
        engine = self._setup_engine_with_blocker_db([
            {"id": "b1", "status": "done"},
            {"id": "b2", "status": "done"},
        ])
        result = engine._recheck_blocked_by("t1", ["b1", "b2"])
        assert result == []

    def test_undone_blocker_returned(self):
        engine = self._setup_engine_with_blocker_db([
            {"id": "b1", "status": "done"},
            {"id": "b2", "status": "review"},
        ])
        result = engine._recheck_blocked_by("t1", ["b1", "b2"])
        assert result == ["b2"]

    def test_db_error_returns_all_blockers(self):
        """On DB failure, return all blocker IDs to prevent spawning."""
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()
        engine.task_service.supabase_client.table.return_value.select.return_value.in_.return_value.execute.side_effect = Exception(
            "timeout"
        )
        result = engine._recheck_blocked_by("t1", ["b1", "b2"])
        assert set(result) == {"b1", "b2"}

    @pytest.mark.asyncio
    async def test_execute_task_aborted_when_blocked_at_spawn_time(self):
        """Race condition: task passes filter but is blocked when _execute_task is called."""
        engine = _setup_engine()

        # Simulate blocker re-check returning an unresolved dependency
        engine._recheck_blocked_by = MagicMock(return_value=["b1"])

        task = _make_task(id="t1", blocked_by=["b1"])
        result = await engine._execute_task(task)

        assert result.success is False
        assert "b1" in result.stderr
        # Must NOT transition to executing
        engine.lifecycle_service.execute_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_task_proceeds_when_blockers_resolved(self):
        """Happy path: all blockers are done, task executes normally."""
        engine = _setup_engine()
        engine._recheck_blocked_by = MagicMock(return_value=[])
        task = _make_task(id="t1", blocked_by=["b1"])
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.spawner.spawn = AsyncMock(return_value=_success_result())

        await engine._execute_task(task)

        # Should proceed to execution (transition called with executing)
        transition_calls = engine.lifecycle_service.execute_transition.call_args_list
        statuses = [c[1]["new_status"] for c in transition_calls]
        assert "executing" in statuses

    def test_recheck_detects_reopened_blocker(self):
        """Race condition: blocker was 'done' during filter but reverted to 'todo' before spawn.

        This represents the window between _filter_blocked_tasks and _execute_task
        where a blocker could be re-opened (e.g. transitioned back from done to todo
        by another operation).
        """
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()

        # Blocker b1 was 'done' when filter ran, but is now 'todo' at spawn time
        engine.task_service.supabase_client.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(
            data=[{"id": "b1", "status": "todo"}]
        )

        still_blocked = engine._recheck_blocked_by("t1", ["b1"])

        assert still_blocked == ["b1"], (
            "Reopened blocker must be detected at spawn time to prevent the race condition"
        )

    def test_recheck_detects_multiple_blockers_partially_reopened(self):
        """Race condition: one of multiple blockers reverts to non-done between filter and spawn."""
        engine = TaskEngine(project_path="/tmp/test")
        engine.task_service = MagicMock()

        # b1 still done, b2 reverted to doing
        engine.task_service.supabase_client.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(
            data=[{"id": "b1", "status": "done"}, {"id": "b2", "status": "doing"}]
        )

        still_blocked = engine._recheck_blocked_by("t1", ["b1", "b2"])

        assert still_blocked == ["b2"]


def _setup_pipeline_engine():
    """Variant of _setup_engine with an unlimited execution run ID generator.

    Pipeline tasks create one run per step plus architect/code-review runs,
    so the fixed 3-item side_effect in _setup_engine is too small.
    """
    engine = _setup_engine()
    run_counter = [0]

    async def _create_run_unlimited(*_args, **_kwargs):
        run_counter[0] += 1
        return (True, {"run": {"id": f"run-{run_counter[0]:03d}"}})

    engine.execution_run_service.create_run = _create_run_unlimited
    return engine


class TestPipelineExecution:
    """Tests for pipeline mode — sequential multi-step task execution with checkpoints."""

    @pytest.mark.asyncio
    async def test_pipeline_executes_all_steps_sequentially(self):
        """Task with pipeline_steps runs each step as a separate CC session."""
        engine = _setup_pipeline_engine()
        pipeline_steps = [
            {"stage": "refactor", "prompt_template": "Refactor the auth module"},
            {"stage": "test", "prompt_template": "Write tests for auth module"},
            {"stage": "review", "prompt_template": "Review the changes"},
        ]
        task = _make_task(
            pipeline_steps=pipeline_steps,
            execution_result=None,
        )
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.spawner.spawn = AsyncMock(return_value=_success_result())

        result = await engine._execute_task(task)

        assert result.success is True
        # spawner called at least once per pipeline step
        assert engine.spawner.spawn.await_count >= len(pipeline_steps)
        # lifecycle transitions include architect-review after pipeline completes
        transition_calls = engine.lifecycle_service.execute_transition.await_args_list
        statuses = [c.kwargs["new_status"] for c in transition_calls]
        assert "architect-review" in statuses

    @pytest.mark.asyncio
    async def test_pipeline_persists_checkpoint_after_each_step(self):
        """Each completed step is saved as a checkpoint in execution_result."""
        engine = _setup_pipeline_engine()
        pipeline_steps = [
            {"stage": "step-a", "prompt_template": "Do step A", "checkpoint": "step-a-done"},
            {"stage": "step-b", "prompt_template": "Do step B", "checkpoint": "step-b-done"},
        ]
        task = _make_task(pipeline_steps=pipeline_steps, execution_result=None)
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.spawner.spawn = AsyncMock(return_value=_success_result())

        await engine._execute_task(task)

        # Verify update_task was called with pipeline_checkpoints
        update_calls = engine.task_service.update_task.await_args_list
        checkpoint_calls = [
            c for c in update_calls
            if "pipeline_checkpoints" in (c.kwargs.get("update_fields") or {}).get("execution_result", {})
        ]
        assert len(checkpoint_calls) >= 1
        final_checkpoints = checkpoint_calls[-1].kwargs["update_fields"]["execution_result"]["pipeline_checkpoints"]
        stages = [cp["stage"] for cp in final_checkpoints]
        assert "step-a" in stages
        assert "step-b" in stages

    @pytest.mark.asyncio
    async def test_pipeline_fails_on_step_failure_and_preserves_prior_checkpoints(self):
        """When step N fails, prior checkpoints (steps 0..N-1) are preserved for retry."""
        engine = _setup_pipeline_engine()
        pipeline_steps = [
            {"stage": "step-0", "prompt_template": "Step 0"},
            {"stage": "step-1", "prompt_template": "Step 1"},  # will fail
        ]
        task = _make_task(pipeline_steps=pipeline_steps, execution_result=None)
        engine.task_service.get_task.return_value = (True, {"task": task})
        # Step 0 succeeds, step 1 fails
        engine.spawner.spawn = AsyncMock(
            side_effect=[_success_result(), _failure_result()]
        )

        result = await engine._execute_task(task)

        assert result.success is False
        # Pipeline should have persisted step-0 checkpoint before failing on step-1
        update_calls = engine.task_service.update_task.await_args_list
        checkpoint_calls = [
            c for c in update_calls
            if "pipeline_checkpoints" in (c.kwargs.get("update_fields") or {}).get("execution_result", {})
        ]
        # At least one checkpoint call with step-0 preserved
        assert len(checkpoint_calls) >= 1
        # The final persisted checkpoints should contain step-0 only (step-1 failed, not added)
        final_checkpoints = checkpoint_calls[-1].kwargs["update_fields"]["execution_result"]["pipeline_checkpoints"]
        assert len(final_checkpoints) == 1
        assert final_checkpoints[0]["stage"] == "step-0"

    @pytest.mark.asyncio
    async def test_pipeline_resumes_from_checkpoint_on_retry(self):
        """On retry, steps with existing checkpoints are skipped."""
        engine = _setup_pipeline_engine()
        pipeline_steps = [
            {"stage": "step-0", "prompt_template": "Step 0"},
            {"stage": "step-1", "prompt_template": "Step 1"},
            {"stage": "step-2", "prompt_template": "Step 2"},
        ]
        # Simulate task with step-0 already completed
        task = _make_task(
            pipeline_steps=pipeline_steps,
            execution_result={
                "pipeline_checkpoints": [
                    {
                        "step_index": 0,
                        "stage": "step-0",
                        "result": "SUCCESS",
                        "summary": "done",
                        "duration_seconds": 10.0,
                        "completed_at": "2026-01-01T00:00:00",
                    }
                ],
                "pipeline_last_completed_step": 0,
            },
        )
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.spawner.spawn = AsyncMock(return_value=_success_result())

        await engine._execute_task(task)

        # At least steps 1 and 2 are executed; step-0 was already checkpointed
        assert engine.spawner.spawn.await_count >= 2

    @pytest.mark.asyncio
    async def test_pipeline_all_checkpoints_present_completes_without_spawning(self):
        """If all steps already have checkpoints, pipeline completes without new CC sessions."""
        engine = _setup_pipeline_engine()
        pipeline_steps = [
            {"stage": "step-0", "prompt_template": "Step 0"},
            {"stage": "step-1", "prompt_template": "Step 1"},
        ]
        task = _make_task(
            pipeline_steps=pipeline_steps,
            execution_result={
                "pipeline_checkpoints": [
                    {"step_index": 0, "stage": "step-0", "result": "SUCCESS", "summary": "done", "duration_seconds": 5.0, "completed_at": "2026-01-01T00:00:00"},
                    {"step_index": 1, "stage": "step-1", "result": "SUCCESS", "summary": "done", "duration_seconds": 5.0, "completed_at": "2026-01-01T00:00:00"},
                ],
                "pipeline_last_completed_step": 1,
            },
        )
        engine.task_service.get_task.return_value = (True, {"task": task})
        engine.spawner.spawn = AsyncMock(return_value=_success_result())

        result = await engine._execute_task(task)

        assert result.success is True
        # No pipeline step spawns — only the downstream review (architect/code) may spawn
        # Verify architect-review transition was triggered
        transition_calls = engine.lifecycle_service.execute_transition.await_args_list
        statuses = [c.kwargs["new_status"] for c in transition_calls]
        assert "architect-review" in statuses
        # on_task_completed must be called with pipeline completion result
        engine.notifier.on_task_completed.assert_awaited_once()

    def test_load_pipeline_checkpoints_returns_empty_for_no_execution_result(self):
        """_load_pipeline_checkpoints returns [] when task has no execution_result."""
        task = _make_task(execution_result=None)
        checkpoints = TaskEngine._load_pipeline_checkpoints(task)
        assert checkpoints == []

    def test_load_pipeline_checkpoints_returns_existing_checkpoints(self):
        """_load_pipeline_checkpoints extracts checkpoints from execution_result."""
        cp = {"step_index": 0, "stage": "step-0", "result": "SUCCESS"}
        task = _make_task(execution_result={"pipeline_checkpoints": [cp]})
        checkpoints = TaskEngine._load_pipeline_checkpoints(task)
        assert checkpoints == [cp]

    def test_load_pipeline_checkpoints_filters_non_dict_entries(self):
        """_load_pipeline_checkpoints skips malformed entries."""
        task = _make_task(execution_result={"pipeline_checkpoints": [{"step_index": 0}, "bad", None]})
        checkpoints = TaskEngine._load_pipeline_checkpoints(task)
        assert checkpoints == [{"step_index": 0}]

    def test_build_step_task_injects_prompt_template(self):
        """_build_step_task overrides execution_prompt with step's prompt_template."""
        task = _make_task(execution_prompt="original prompt")
        step = {"stage": "refactor", "prompt_template": "New prompt for this step"}
        result = TaskEngine._build_step_task(task, step, step_index=0, prior_checkpoints=[])
        assert result["execution_prompt"] == "New prompt for this step"
        assert result["_pipeline_step_index"] == 0
        assert result["_pipeline_step_stage"] == "refactor"

    def test_build_step_task_includes_prior_checkpoint_context(self):
        """_build_step_task prepends prior checkpoint summaries to the prompt."""
        task = _make_task(execution_prompt="original")
        step = {"stage": "test", "prompt_template": "Run tests"}
        prior = [{"step_index": 0, "stage": "refactor", "summary": "Refactored auth module"}]
        result = TaskEngine._build_step_task(task, step, step_index=1, prior_checkpoints=prior)
        assert "Pipeline Context (prior steps)" in result["execution_prompt"]
        assert "Refactored auth module" in result["execution_prompt"]
        assert "Run tests" in result["execution_prompt"]

    def test_build_step_task_no_prior_checkpoints_uses_raw_template(self):
        """_build_step_task does not prefix checkpoint context when there are no prior checkpoints."""
        task = _make_task()
        step = {"stage": "step-0", "prompt_template": "Clean prompt"}
        result = TaskEngine._build_step_task(task, step, step_index=0, prior_checkpoints=[])
        assert result["execution_prompt"] == "Clean prompt"
        assert "Pipeline Context" not in result["execution_prompt"]

    def test_build_step_checkpoint_captures_key_fields(self):
        """_build_step_checkpoint produces a valid checkpoint dict."""
        from src.server.services.engine.task_engine import TaskExecutionState

        result = _success_result()
        state = TaskExecutionState(
            task={"id": "task-001"},
            task_id="task-001",
            execute_run_id="run-001",
        )
        step = {"stage": "refactor", "checkpoint": "refactor-done"}
        cp = TaskEngine._build_step_checkpoint(0, "refactor", step, result, state)

        assert cp["step_index"] == 0
        assert cp["stage"] == "refactor"
        assert cp["checkpoint"] == "refactor-done"
        assert cp["execution_run_id"] == "run-001"
        assert cp["result"] == "SUCCESS"
        assert "completed_at" in cp

    @pytest.mark.asyncio
    async def test_non_pipeline_task_runs_normal_chain(self):
        """Tasks without pipeline_steps run the standard stage chain."""
        engine = _setup_engine()
        task = _make_task()  # no pipeline_steps
        engine.task_service.get_task.return_value = (True, {"task": task})

        result = await engine._execute_task(task)

        assert result.success is True
        # Standard chain spawns at least once (execution + optional code review)
        engine.spawner.spawn.assert_awaited()


# ---------------------------------------------------------------------------
# Tests: _assign_approved_tasks — contract gate observability
# ---------------------------------------------------------------------------


class TestAssignApprovedTasksContractGate:
    """Engine must emit structured log + notifier event when contract gate blocks assignment."""

    @pytest.mark.asyncio
    async def test_assigns_when_no_contract_gate(self):
        """Normal approved task (no contract) gets assigned without event."""
        engine = _setup_engine()
        engine.notifier.emit = AsyncMock()
        approved_task = _make_task(id="t-approved", status="approved")
        engine.task_service.list_tasks.return_value = (True, {"tasks": [approved_task]})
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(True, {"task": _make_task(id="t-approved", status="assigned")})
        )

        await engine._assign_approved_tasks()

        engine.lifecycle_service.execute_transition.assert_awaited_once()
        engine.notifier.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_emits_contract_gate_event_when_unlocked(self):
        """Engine emits task_contract_gate_blocked when lifecycle blocks due to unlocked contract."""
        from src.server.services.engine.notifier import EVENT_TASK_CONTRACT_GATE_BLOCKED

        engine = _setup_engine()
        engine.notifier.emit = AsyncMock()
        approved_task = _make_task(id="t-gated", status="approved")
        engine.task_service.list_tasks.return_value = (True, {"tasks": [approved_task]})
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(
                False,
                {
                    "error": "Contract must be locked before assignment.",
                    "contract_gate": True,
                    "contract_id": "ctr-001",
                    "contract_status": "unlocked",
                    "blocked_transition": "approved → assigned",
                },
            )
        )

        await engine._assign_approved_tasks()

        engine.notifier.emit.assert_awaited_once()
        call_args = engine.notifier.emit.call_args
        assert call_args[0][0] == EVENT_TASK_CONTRACT_GATE_BLOCKED
        assert call_args[1]["task_id"] == "t-gated"
        data = call_args[1]["data"]
        assert data["contract_id"] == "ctr-001"
        assert data["contract_status"] == "unlocked"
        assert data["blocked_transition"] == "approved → assigned"

    @pytest.mark.asyncio
    async def test_non_contract_failure_does_not_emit_gate_event(self):
        """Ordinary assignment failure (not contract gate) must not emit the contract gate event."""
        from src.server.services.engine.notifier import EVENT_TASK_CONTRACT_GATE_BLOCKED

        engine = _setup_engine()
        engine.notifier.emit = AsyncMock()
        approved_task = _make_task(id="t-fail", status="approved")
        engine.task_service.list_tasks.return_value = (True, {"tasks": [approved_task]})
        engine.lifecycle_service.execute_transition = AsyncMock(
            return_value=(False, {"error": "Invalid transition for some other reason"})
        )

        await engine._assign_approved_tasks()

        for call in engine.notifier.emit.await_args_list:
            assert call[0][0] != EVENT_TASK_CONTRACT_GATE_BLOCKED

    @pytest.mark.asyncio
    async def test_skips_assignment_does_not_call_execute_for_zero_tasks(self):
        """No approved tasks → no transitions attempted."""
        engine = _setup_engine()
        engine.task_service.list_tasks.return_value = (True, {"tasks": []})
        engine.lifecycle_service.execute_transition = AsyncMock()

        await engine._assign_approved_tasks()

        engine.lifecycle_service.execute_transition.assert_not_awaited()


class TestPollCycleReconcile:
    @pytest.mark.asyncio
    async def test_poll_cycle_reconciles_stale_runs_before_capacity_gate(self):
        engine = _setup_engine()
        engine._reconcile_stale_execute_runs = AsyncMock()
        engine.spawner.has_capacity = False

        await engine._poll_cycle()

        engine._reconcile_stale_execute_runs.assert_awaited_once()


class TestTaskExecuteFailureContainment:
    @pytest.mark.asyncio
    async def test_execute_task_with_timeout_converts_spawn_exception_into_failed_run(self):
        engine = _setup_engine()
        task = _make_task(id="task-crash", status="assigned")
        engine.spawner.spawn = AsyncMock(side_effect=TypeError("boom"))
        engine.execution_run_service.list_runs.return_value = (
            True,
            {"runs": [{"id": "run-crash"}]},
        )
        engine._handle_task_failure = AsyncMock()

        result = await engine._execute_task_with_timeout(task, timeout_seconds=30)

        assert result.success is False
        assert "boom" in result.stderr
        engine.execution_run_service.update_run.assert_awaited()
        engine._handle_task_failure.assert_awaited_once_with("task-crash", task, "boom")
