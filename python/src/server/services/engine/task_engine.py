"""
Task Engine — main daemon loop for LeanKit V3.

Polls for assigned tasks, spawns Claude Code sessions,
monitors execution, handles completions/failures,
pushes notifications, and monitors engine health.

The execution path is formalized as an ordered middleware chain. The
current stage order is:

1. claim
2. policy load
3. guidance injection
4. boundary injection
5. workspace acquire
6. lifecycle restore
7. runner execute
8. changed-file validation
9. artifact capture
10. notifier publish

Each stage receives the shared execution state, mutates or replaces the
fields it owns, and returns the state for the next stage.

Usage:
    engine = TaskEngine(project_path="/path/to/repo")
    await engine.start()   # runs until stop() is called
    await engine.stop()    # graceful shutdown
"""

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Awaitable, Callable

from ...config.env_aliases import (
    get_engine_escalation_enabled,
    get_engine_pid_check_interval,
    get_engine_retry_delay_seconds,
    get_engine_retry_max_delay_seconds,
)
from ...config.logfire_config import get_logger
from ..cost_budget_service import CostBudgetService
from ..projects.engine_policy_service import EnginePolicyService
from ..projects.execution_run_service import ExecutionRunService
from ..projects.task_lifecycle_service import TaskLifecycleService
from ..projects.task_service import TaskService
from .architect_reviewer import (
    ArchitectReviewer,
    ArchitectReviewResult,
    ReviewAction,
    ReviewConfig,
    build_review_history_entry,
    calculate_quality_gate_score,
)
from .bug_task_creator import BugTaskCreator
from .capacity_tracker import GlobalCapacityTracker, SharedAgentPool
from .cc_spawner import MODEL_SONNET, CCExecutionResult, CCSpawner, ProjectConfig, is_runner_level_failure
from .codex_runner import CodexRunnerAdapter
from .execution_health_monitor import (
    HEALTH_HEALTHY,
    ExecutionHealthMonitor,
)
from .health_monitor import HealthMonitor
from .learning_processor import LearningProcessor
from .notifier import Notifier
from .prompt_builder import PromptBuilder
from .run_workspace import RunWorkspaceContext, RunWorkspaceManager
from .runner_adapter import DEFAULT_RUNNER_KEY, ClaudeCodeRunnerAdapter, ExecutionRunner
from .runner_routing import (
    COLLABORATION_MODE_APPROVE,
    COLLABORATION_MODE_AUTO,
    COLLABORATION_MODE_REVIEW,
    _load_token_profiles,
    check_approval_required,
    get_fallback_runner_chain,
    resolve_collaboration_mode,
    resolve_runner_selection,
    select_token_profile,
)
from .task_boundaries import task_has_boundary_rules, validate_task_boundaries
from .task_conflicts import predict_task_overlap

logger = get_logger(__name__)


@dataclass
class TaskExecutionState:
    """Mutable state threaded through the task execution middleware chain.

    Each stage accepts the current state, updates the fields it owns, and
    returns the same state instance for the next stage. Stages stop the chain
    early by setting ``final_result``.
    """

    task: dict[str, Any]
    task_id: str
    isolation_override: str | None = None
    full_task: dict[str, Any] | None = None
    project_id: str | None = None
    retry_index: int = 0
    policy: dict[str, Any] | None = None
    model_routing: dict[str, Any] | None = None
    effective_project_config: ProjectConfig | None = None
    prompt: str | None = None
    injection_stats: dict[str, Any] | None = None
    boundary_snapshot: dict[str, str] | None = None
    boundary_snapshot_error: str | None = None
    runner: ExecutionRunner | None = None
    runner_key: str = DEFAULT_RUNNER_KEY
    routing_reason: str | None = None
    runner_source_app: str = DEFAULT_RUNNER_KEY
    selected_model: str | None = None
    token_profile_name: str | None = None
    token_profile: dict[str, Any] | None = None
    model_fallback_chain: list[str] | None = None
    execute_run_id: str | None = None
    execute_runtime: dict[str, Any] | None = None
    run_workspace_ctx: RunWorkspaceContext | None = None
    exec_health_monitor: ExecutionHealthMonitor | None = None
    abort_reasons: list[str] = field(default_factory=list)
    stream_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None
    runner_result: CCExecutionResult | None = None
    runner_fallback_decisions: list[dict[str, Any]] = field(default_factory=list)
    current_task: dict[str, Any] | None = None
    boundary_validation: dict[str, Any] | None = None
    execution_result: dict[str, Any] | None = None
    executed_by: dict[str, Any] | None = None
    run_status: str | None = None
    run_metadata: dict[str, Any] | None = None
    llm_metrics: dict[str, Any] = field(default_factory=dict)
    result_summary: str | None = None
    final_result: CCExecutionResult | None = None


TaskExecutionStage = Callable[[TaskExecutionState], Awaitable[TaskExecutionState]]
TaskExecutionStageChain = tuple[TaskExecutionStage, ...]


class TaskEngine:
    """Daemon that polls for assigned tasks and spawns CC sessions."""

    def __init__(
        self,
        project_path: str,
        project_id: str | None = None,
        source_app: str | None = None,
        poll_interval: int = 30,
        max_parallel: int = 5,
        default_timeout: int = 600,
        shutdown_grace: int = 60,
        build_command: str = "pnpm build && pnpm test",
        isolation: str = "shared",
        repository_url: str | None = None,
        review_config: ReviewConfig | None = None,
        global_tracker: GlobalCapacityTracker | None = None,
        agent_pool: SharedAgentPool | None = None,
    ):
        self.project_id = project_id
        self.source_app = source_app or "unknown"
        self.project_config = ProjectConfig(
            project_path=project_path,
            build_command=build_command,
            isolation=isolation,
            repository_url=repository_url,
        )
        self.review_config = review_config or ReviewConfig()
        self.poll_interval = poll_interval
        self.shutdown_grace = shutdown_grace
        self.global_tracker = global_tracker
        self.agent_pool = agent_pool

        self.task_service = TaskService()
        self.execution_run_service = ExecutionRunService()
        self.lifecycle_service = TaskLifecycleService()
        self.engine_policy_service = EnginePolicyService()
        from ..projects.approval_request_service import (
            ApprovalRequestService,  # lazy import to avoid circular dependency
        )
        self.approval_request_service = ApprovalRequestService()
        self.architect_reviewer = ArchitectReviewer()
        self.notifier = Notifier(source_app=self.source_app)
        self.cost_budget_service = CostBudgetService()
        self.learning_processor = LearningProcessor(notifier=self.notifier)
        self.bug_task_creator = BugTaskCreator(task_service=self.task_service)
        self.prompt_builder = PromptBuilder(learning_processor=self.learning_processor)
        self.health_monitor = HealthMonitor(
            task_service=self.task_service,
            notifier=self.notifier,
        )
        self.default_runner_key = DEFAULT_RUNNER_KEY
        self.runner_adapters: dict[str, ExecutionRunner] = {}
        self.spawner = ClaudeCodeRunnerAdapter(
            CCSpawner(
                default_timeout=default_timeout,
                max_parallel=max_parallel,
            )
        )
        self.register_runner(
            CodexRunnerAdapter(
                default_timeout=default_timeout,
                max_parallel=max_parallel,
            )
        )

        self.default_timeout = default_timeout
        self._running = False
        self._loop_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._execution_tasks: dict[str, asyncio.Task[CCExecutionResult]] = {}
        self._poll_cycle_count: int = 0

        # Retry and escalation policy (configurable via env vars)
        self._retry_delay: int = get_engine_retry_delay_seconds()
        self._max_retry_delay: int = get_engine_retry_max_delay_seconds()
        self._escalation_enabled: bool = get_engine_escalation_enabled()
        self._pid_check_interval: int = get_engine_pid_check_interval()

    @property
    def spawner(self) -> ExecutionRunner:
        """Backward-compatible default runner alias."""
        return self._spawner

    @spawner.setter
    def spawner(self, runner: ExecutionRunner) -> None:
        """Keep legacy `spawner` access while registering the default adapter."""
        self._spawner = runner
        self.runner_adapters[self.default_runner_key] = runner

    def register_runner(self, runner: ExecutionRunner) -> None:
        """Register a non-default execution runner adapter."""
        self.runner_adapters[runner.runner_key] = runner

    async def _create_execution_run(
        self,
        *,
        task_id: str,
        project_id: str | None,
        stage: str,
        status: str,
        session_id: str | None = None,
        model: str | None = None,
        retry_index: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Best-effort creation of an execution run record."""
        if not project_id:
            logger.warning(
                f"Execution run skipped | task_id={task_id} | stage={stage} | reason=missing_project_id"
            )
            return None
        try:
            ok, result = await self.execution_run_service.create_run(
                task_id=task_id,
                project_id=project_id,
                stage=stage,
                status=status,
                engine_id="task-engine",
                session_id=session_id,
                model=model,
                retry_index=retry_index,
                metadata=metadata or {},
            )
            if ok:
                return result["run"]["id"]
            logger.warning(
                f"Execution run create failed | task_id={task_id} | stage={stage} | error={result.get('error')}"
            )
        except Exception as e:
            logger.warning(f"Execution run create error | task_id={task_id} | stage={stage} | error={e}")
        return None

    async def _update_execution_run(
        self,
        run_id: str | None,
        **update_fields: Any,
    ) -> None:
        """Best-effort update of an execution run record."""
        if not run_id:
            return
        try:
            ok, result = await self.execution_run_service.update_run(run_id, update_fields)
            if not ok:
                logger.warning(f"Execution run update failed | run_id={run_id} | error={result.get('error')}")
        except Exception as e:
            logger.warning(f"Execution run update error | run_id={run_id} | error={e}")

    def _resolve_project_id(self, task: dict[str, Any]) -> str | None:
        """Resolve project context from task payload or engine scope."""
        task_project_id = task.get("project_id")
        if isinstance(task_project_id, str) and task_project_id.strip():
            return task_project_id
        if self.project_id:
            return self.project_id
        return None

    def _resolve_source_app(self, task: dict[str, Any]) -> str:
        """Resolve source app from task payload or engine scope."""
        task_source_app = task.get("source_app")
        if isinstance(task_source_app, str) and task_source_app.strip():
            return task_source_app
        return self.source_app

    @staticmethod
    def _resolve_bootstrap_plan_id(task: dict[str, Any]) -> str | None:
        """Resolve bootstrap plan id from task tags or metadata if present."""
        direct = task.get("bootstrap_plan_id") or task.get("bootstrapPlanId")
        if isinstance(direct, str) and direct.strip():
            return direct

        tags = task.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and tag.startswith("bootstrap-plan:"):
                    plan_id = tag.split(":", 1)[1].strip()
                    if plan_id:
                        return plan_id
        return None

    def _get_runner_adapter(
        self,
        task: dict[str, Any],
        model_routing: dict[str, Any] | None = None,
        stage: str = "execute",
    ) -> tuple[ExecutionRunner, str, str]:
        """Select a registered runner adapter for a task.

        When model_routing is provided (loaded from archon_engine_policies),
        it is evaluated after explicit task-level overrides but before the
        global heuristics, giving operators per-project routing control.
        """
        selection = resolve_runner_selection(
            task,
            available_runner_keys=set(self.runner_adapters.keys()),
            default_runner_key=self.default_runner_key,
            model_routing=model_routing,
            stage=stage,
        )
        runner_key = selection.runner_key
        runner = self.runner_adapters.get(runner_key)
        if runner is None:
            raise ValueError(f"Unsupported execution runner: {runner_key}")
        return runner, runner_key, selection.reason

    @staticmethod
    def _runner_source_app(runner: ExecutionRunner, runner_key: str) -> str:
        """Resolve a stable source-app string for a runner adapter."""
        source_app = getattr(runner, "source_app", None)
        if isinstance(source_app, str) and source_app.strip():
            return source_app
        return runner_key

    @staticmethod
    def _runner_review_model(runner: ExecutionRunner) -> str:
        """Resolve a stable review model for a runner adapter."""
        review_model = getattr(runner, "review_model", None)
        if isinstance(review_model, str) and review_model.strip():
            return review_model
        return MODEL_SONNET

    async def _check_collaboration_gate(
        self,
        task_id: str,
        full_task: dict[str, Any],
        model_routing: dict[str, Any] | None,
        project_id: str | None,
    ) -> tuple[bool, str]:
        """Apply the architect collaboration mode gate for architect-planned tasks.

        Collaboration mode controls how tasks created by the architect-provider
        are handled before execution:
        - auto: proceed immediately (no gate)
        - review: require the task to pass through owner-qa review first
        - approve: require explicit owner approval before execution

        Only applies to architect-planned tasks (tasks with ``created_from``
        starting with "architect" or tagged with "architect-request").

        Returns:
            (blocked, reason) — blocked=True means execution should not proceed.
        """
        # Collaboration mode only applies to architect-planned tasks
        created_from = str(full_task.get("created_from") or "").lower()
        tags = set()
        raw_tags = full_task.get("tags")
        if isinstance(raw_tags, list):
            tags = {str(t).lower() for t in raw_tags if isinstance(t, str)}

        is_architect_planned = (
            created_from.startswith("architect")
            or "architect-request" in tags
            or "architect-planned" in tags
        )

        if not is_architect_planned:
            return False, ""

        mode = resolve_collaboration_mode(full_task, model_routing)

        logger.info(
            "Collaboration gate check | task_id=%s | mode=%s | created_from=%s",
            task_id, mode, created_from,
        )

        if mode == COLLABORATION_MODE_AUTO:
            # Auto: no gate — log the decision and proceed
            return False, ""

        if mode == COLLABORATION_MODE_APPROVE:
            # Approve: require explicit owner approval (reuse approval-request service)
            ok_approved, approved_result = self.approval_request_service.list_requests(
                task_id=task_id, status="approved"
            )
            if ok_approved and approved_result.get("approvals"):
                logger.info(
                    "Collaboration gate passed (approve mode) | task_id=%s | approval_id=%s",
                    task_id,
                    approved_result["approvals"][0].get("id"),
                )
                return False, ""

            # No approved request — create a pending one if not already pending
            ok_pending, pending_result = self.approval_request_service.list_requests(
                task_id=task_id, status="pending"
            )
            if ok_pending and not pending_result.get("approvals"):
                title = full_task.get("title") or f"Task {task_id}"
                await self.approval_request_service.create_request(
                    title=f"Architect plan approval required: {title}",
                    summary=(
                        "High-risk architect-planned task requires owner approval before execution. "
                        f"Collaboration mode: {mode}"
                    ),
                    requested_by="task-engine",
                    requested_channel="engine",
                    project_id=project_id,
                    task_id=task_id,
                    context={"collaboration_mode": mode, "task_id": task_id},
                )

            logger.warning(
                "Collaboration gate blocked execution (approve mode) | task_id=%s",
                task_id,
            )
            return True, f"Awaiting owner approval for architect-planned task (collaboration_mode={mode})"

        if mode == COLLABORATION_MODE_REVIEW:
            # Review: the task must have passed through owner-qa before executing
            current_status = str(full_task.get("status") or "").lower()
            if current_status in {"owner-qa", "approved"}:
                # Task has already been reviewed by owner — proceed
                return False, ""

            # Transition task to owner-qa for review
            ok, res = await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status="owner-qa",
                changed_by="task-engine",
                reason=f"Architect-planned task requires owner review (collaboration_mode={mode})",
            )
            if ok:
                logger.info(
                    "Collaboration gate: task sent to owner-qa for review | task_id=%s | mode=%s",
                    task_id, mode,
                )
            else:
                logger.warning(
                    "Collaboration gate: failed to transition to owner-qa | task_id=%s | error=%s",
                    task_id, res.get("error"),
                )
            return True, f"Architect-planned task pending owner review (collaboration_mode={mode})"

        return False, ""

    async def _pass_approval_gate(
        self,
        task_id: str,
        full_task: dict[str, Any],
        model_routing: dict[str, Any] | None,
        project_id: str | None,
    ) -> bool:
        """Return True when execution may proceed; False when awaiting approval.

        When approval is required but no approved request exists yet, a pending
        approval request is created so operators know a decision is needed.
        The task stays in its current poll-eligible state until approved.
        """
        required, approval_reason = check_approval_required(full_task, model_routing)
        if not required:
            return True

        # Check for an existing approved request for this task
        ok_approved, approved_result = self.approval_request_service.list_requests(
            task_id=task_id, status="approved"
        )
        if ok_approved and approved_result.get("approvals"):
            logger.info(
                "Approval gate passed | task_id=%s | reason=%s | approval_id=%s",
                task_id,
                approval_reason,
                approved_result["approvals"][0].get("id"),
            )
            return True

        # No approved request — create a pending one if not already pending
        ok_pending, pending_result = self.approval_request_service.list_requests(
            task_id=task_id, status="pending"
        )
        if ok_pending and not pending_result.get("approvals"):
            title = full_task.get("title") or f"Task {task_id}"
            await self.approval_request_service.create_request(
                title=f"Approval required: {title}",
                summary=(
                    f"High-risk task requires human approval before execution. "
                    f"Routing reason: {approval_reason}"
                ),
                requested_by="task-engine",
                requested_channel="engine",
                project_id=project_id,
                task_id=task_id,
                context={"approval_reason": approval_reason, "task_id": task_id},
            )

        logger.warning(
            "Approval gate blocked execution | task_id=%s | reason=%s",
            task_id,
            approval_reason,
        )
        return False

    def _get_project_policy(self, task: dict[str, Any]) -> dict[str, Any] | None:
        """Return the active engine policy for the task's project, if any."""
        project_id = self._resolve_project_id(task)
        if not project_id:
            return None
        return self.engine_policy_service.get_active_policy(project_id)

    def _project_config_for_policy(self, policy: dict[str, Any] | None) -> ProjectConfig:
        """Return the effective execution config after project policy overrides."""
        if not policy:
            return self.project_config

        isolation_policy = policy.get("isolation_policy") or {}
        if not isinstance(isolation_policy, dict):
            return self.project_config

        worktree_mode = isolation_policy.get("worktree_mode")
        isolation = self._map_worktree_mode_to_isolation(worktree_mode)
        if isolation is None or isolation == self.project_config.isolation:
            return self.project_config

        return replace(self.project_config, isolation=isolation)

    def _review_config_for_policy(self, policy: dict[str, Any] | None) -> ReviewConfig:
        """Return the effective architect review config for one project."""
        if not policy:
            return self.review_config

        review_policy = policy.get("review_policy") or {}
        if not isinstance(review_policy, dict):
            return self.review_config

        review_mode = review_policy.get("review_mode")
        if not isinstance(review_mode, str) or not review_mode.strip():
            return self.review_config

        if review_mode == self.review_config.review_mode:
            return self.review_config

        return replace(self.review_config, review_mode=review_mode)

    @staticmethod
    def _map_worktree_mode_to_isolation(worktree_mode: Any) -> str | None:
        """Map persisted isolation policy values to runner config values."""
        if not isinstance(worktree_mode, str):
            return None

        mode = worktree_mode.strip()
        if mode == "shared":
            return "shared"
        if mode in {"isolated", "per-task", "git-worktree"}:
            return "git-worktree"
        if mode == "worktree":
            # Detect conflicts, create a git worktree for overlapping tasks
            return "on-conflict-worktree"
        if mode == "queue":
            # Detect conflicts, queue overlapping tasks — same runtime behaviour as shared
            return "shared"
        return None

    def _build_runtime_context(
        self,
        task: dict[str, Any],
        *,
        stage: str,
        execution_run_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Build normalized runtime context for engine notifications."""
        context = {
            "project_id": self._resolve_project_id(task),
            "source_app": self._resolve_source_app(task),
            "bootstrap_plan_id": self._resolve_bootstrap_plan_id(task),
            "stage": stage,
            "execution_run_id": execution_run_id,
            "session_id": session_id,
            "agent_id": agent_id,
        }
        return {key: value for key, value in context.items() if value is not None}

    @staticmethod
    def _extract_git_status_path(status_line: str) -> str | None:
        """Extract the repo-relative path from one `git status --porcelain` line."""
        if not status_line:
            return None
        trimmed = status_line.rstrip("\n")
        if len(trimmed) < 4:
            return None
        path_portion = trimmed[3:]
        if " -> " in path_portion:
            path_portion = path_portion.split(" -> ", 1)[1]
        normalized = path_portion.strip().replace("\\", "/").lstrip("./")
        return normalized or None

    @staticmethod
    def _build_file_signature(base_path: str, relative_path: str) -> str:
        """Build a stable signature for one repo-relative path."""
        absolute_path = os.path.join(base_path, relative_path)
        if not os.path.exists(absolute_path):
            return "__missing__"
        if os.path.isdir(absolute_path):
            return "__dir__"
        with open(absolute_path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    @staticmethod
    def _extract_partial_context(result: "CCExecutionResult") -> dict[str, Any]:
        """Extract partial work context from a timed-out or incomplete execution.

        Parses the CC JSON stdout stream to discover which files were touched
        and what the agent communicated before the timeout.
        """
        partial_context: dict[str, Any] = {
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
        }

        files_modified: list[str] = []
        assistant_messages: list[str] = []
        _write_tools = {"Write", "Edit", "NotebookEdit"}

        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue

            line_type = data.get("type", "")

            if line_type == "tool_use":
                tool_name = data.get("tool", {}).get("name", "") or data.get("name", "")
                tool_input = data.get("tool", {}).get("input", {}) or data.get("input", {})
                if tool_name in _write_tools and isinstance(tool_input, dict):
                    fp = tool_input.get("file_path") or tool_input.get("path")
                    if fp and fp not in files_modified:
                        files_modified.append(fp)

            elif line_type == "assistant":
                content = data.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                assistant_messages.append(text[:200])
                                break
                elif isinstance(content, str) and content.strip():
                    assistant_messages.append(content.strip()[:200])

        if files_modified:
            partial_context["files_modified"] = files_modified
        if assistant_messages:
            partial_context["partial_output"] = assistant_messages[-1]

        return partial_context

    async def _capture_dirty_file_snapshot(self, cwd: str | None = None) -> dict[str, str]:
        """Capture signatures for all currently dirty git paths in the workspace."""
        project_path = cwd or self.project_config.project_path
        process = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--porcelain",
            cwd=project_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        if process.returncode != 0:
            raise RuntimeError(f"git status failed: {stderr.decode().strip()[:200]}")

        snapshot: dict[str, str] = {}
        for line in stdout.decode().splitlines():
            if path := self._extract_git_status_path(line):
                snapshot[path] = self._build_file_signature(project_path, path)
        return snapshot

    async def _collect_changed_files_since(
        self,
        before_snapshot: dict[str, str],
        cwd: str | None = None,
    ) -> list[str]:
        """Return dirty repo-relative files whose signatures changed since the snapshot."""
        after_snapshot = await self._capture_dirty_file_snapshot(cwd=cwd)
        changed_files: list[str] = []
        for path in sorted(set(before_snapshot) | set(after_snapshot)):
            if before_snapshot.get(path) != after_snapshot.get(path):
                changed_files.append(path)
        return changed_files

    async def _build_boundary_validation(
        self,
        task: dict[str, Any],
        before_snapshot: dict[str, str] | None,
        snapshot_error: str | None = None,
    ) -> dict[str, Any]:
        """Build boundary validation metadata for one task execution."""
        if not task_has_boundary_rules(task):
            return validate_task_boundaries(task, [])

        if snapshot_error:
            validation = validate_task_boundaries(task, [])
            validation["status"] = "unavailable"
            validation["reason"] = snapshot_error
            return validation

        try:
            changed_files = await self._collect_changed_files_since(before_snapshot or {})
        except Exception as exc:
            validation = validate_task_boundaries(task, [])
            validation["status"] = "unavailable"
            validation["reason"] = str(exc)
            return validation

        return validate_task_boundaries(task, changed_files)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the engine's poll loop and health monitor."""
        if self._running:
            logger.warning("TaskEngine already running")
            return

        self._running = True
        await self._startup_orphan_recovery()
        self._loop_task = asyncio.create_task(self._run_loop())
        self._watchdog_task = asyncio.create_task(self._pid_watchdog_loop())
        await self.health_monitor.start()

        budget_config = self.cost_budget_service._get_budget_config(self.project_id)
        pool_info = (
            f"pool={self.agent_pool.pool_id}({self.agent_pool._project_limit(self.project_id or '')}slots)"
            if self.agent_pool else "pool=none"
        )
        logger.info(
            f"TaskEngine started | project_id={self.project_id} | "
            f"poll_interval={self.poll_interval}s | "
            f"max_parallel={self.spawner.max_parallel} | "
            f"global_limit={'∞' if not self.global_tracker else self.global_tracker.max_global} | "
            f"{pool_info} | "
            f"isolation={self.project_config.isolation} | "
            f"budget_daily=${budget_config.get('daily_budget_usd', 'N/A')} | "
            f"budget_weekly=${budget_config.get('weekly_budget_usd', 'N/A')}"
        )

    def get_status(self) -> dict:
        """Return current engine status for monitoring."""
        return {
            "project_id": self.project_id,
            "running": self._running,
            "max_parallel": self.spawner.max_parallel,
            "slots_used": self.spawner.running_count,
            "slots_available": self.spawner.max_parallel - self.spawner.running_count,
            "active_tasks": list(self._execution_tasks.keys()),
            "poll_cycles": self._poll_cycle_count,
        }

    async def stop(self) -> None:
        """Graceful shutdown: wait for running tasks, then kill stragglers."""
        if not self._running:
            return

        self._running = False
        logger.info("TaskEngine stopping — waiting for running tasks")

        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

        await self.health_monitor.stop()

        if self._execution_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *self._execution_tasks.values(),
                        return_exceptions=True,
                    ),
                    timeout=self.shutdown_grace,
                )
            except TimeoutError:
                logger.warning("Shutdown grace period exceeded — killing remaining")
                for task_id in list(self._execution_tasks):
                    await self.spawner.kill(task_id)

        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

        logger.info("TaskEngine stopped")

    # ------------------------------------------------------------------
    # PID watchdog — detects silently dead CC processes
    # ------------------------------------------------------------------

    async def _pid_watchdog_loop(self) -> None:
        """Background loop that checks CC process liveness every _pid_check_interval seconds."""
        while self._running:
            await asyncio.sleep(self._pid_check_interval)
            if not self._running:
                break
            try:
                await self._check_pid_liveness()
            except Exception as e:
                logger.error(f"PID watchdog error: {e}", exc_info=True)

    async def _check_pid_liveness(self) -> None:
        """Check each tracked task's CC process. Force-fail runs whose process has died silently."""
        for task_id in list(self._execution_tasks.keys()):
            exec_task = self._execution_tasks.get(task_id)
            if exec_task is None or exec_task.done():
                continue

            # Resolve process handle via duck-typed get_process (supported by both runner adapters)
            get_process = getattr(self.spawner, "get_process", None)
            if get_process is None:
                inner = getattr(self.spawner, "_spawner", None)
                get_process = getattr(inner, "get_process", None) if inner else None
            if get_process is None:
                continue

            process = get_process(task_id)
            if process is None:
                # Not in the runner's _running dict — could be in review phase or already cleaned up
                continue

            if process.returncode is not None:
                pid = getattr(process, "pid", "unknown")
                exit_code = process.returncode
                logger.error(
                    f"Dead CC process detected by watchdog | "
                    f"task_id={task_id} | pid={pid} | exit_code={exit_code} | "
                    f"action=force_failing"
                )
                # Cancel the stuck asyncio task before releasing the slot
                exec_task.cancel()
                self._execution_tasks.pop(task_id, None)
                if self.global_tracker and self.project_id:
                    self.global_tracker.release(self.project_id)
                if self.agent_pool and self.project_id:
                    self.agent_pool.release(self.project_id)
                reason = f"CC process died unexpectedly (pid={pid}, exit_code={exit_code})"
                await self._force_fail_orphaned_run(task_id, reason=reason)

    async def _force_fail_orphaned_run(
        self, task_id: str, reason: str, run_id: str | None = None
    ) -> None:
        """Fail an orphaned execution run and apply the task failure policy."""
        if run_id:
            await self._update_execution_run(
                run_id,
                status="failed",
                finished_at=datetime.now().isoformat(),
                error_summary=reason[:500],
            )
        else:
            ok, result = self.execution_run_service.list_runs(task_id=task_id, status="running", limit=10)
            if ok:
                for run in result.get("runs", []):
                    await self._update_execution_run(
                        run["id"],
                        status="failed",
                        finished_at=datetime.now().isoformat(),
                        error_summary=reason[:500],
                    )

        ok, task_result = self.task_service.get_task(task_id)
        task = task_result.get("task", {"id": task_id}) if ok else {"id": task_id}
        await self._handle_task_failure(task_id, task, reason)

    async def _startup_orphan_recovery(self) -> None:
        """Recover orphaned execution runs left by a previous engine instance.

        Queries execution_runs with status='running' older than the timeout
        threshold and force-fails them so capacity is reclaimed on startup.
        """
        threshold_seconds = self.default_timeout + 600
        try:
            ok, result = self.execution_run_service.list_runs(
                project_id=self.project_id,
                status="running",
                limit=100,
            )
            if not ok:
                logger.warning(
                    f"Startup orphan recovery: failed to list runs | error={result.get('error')}"
                )
                return

            runs = result.get("runs", [])
            if not runs:
                logger.info("Startup orphan recovery: no running runs found")
                return

            now = datetime.now()
            orphaned: list[dict[str, Any]] = []
            for run in runs:
                started_at_str = run.get("started_at")
                if not started_at_str:
                    continue
                try:
                    started_dt = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
                    elapsed = (now - started_dt.replace(tzinfo=None)).total_seconds()
                    if elapsed > threshold_seconds:
                        orphaned.append(run)
                except (ValueError, TypeError):
                    continue

            if not orphaned:
                logger.info(
                    f"Startup orphan recovery: {len(runs)} running run(s) checked, none orphaned "
                    f"(threshold={threshold_seconds}s)"
                )
                return

            logger.warning(
                f"Startup orphan recovery: found {len(orphaned)} orphaned run(s) | "
                f"threshold={threshold_seconds}s"
            )
            for run in orphaned:
                run_id = run["id"]
                task_id = run.get("task_id", "unknown")
                started_dt = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
                elapsed_s = int((now - started_dt.replace(tzinfo=None)).total_seconds())
                reason = (
                    f"Orphaned run recovered at engine startup "
                    f"(elapsed={elapsed_s}s, threshold={threshold_seconds}s)"
                )
                logger.error(
                    f"Force-failing orphaned run | run_id={run_id} | task_id={task_id} | "
                    f"elapsed={elapsed_s}s"
                )
                await self._force_fail_orphaned_run(task_id, reason=reason, run_id=run_id)
        except Exception as e:
            logger.error(f"Startup orphan recovery failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Retry and escalation policy
    # ------------------------------------------------------------------

    def _calculate_retry_delay(self, attempt: int) -> float:
        """Exponential backoff: base_delay * 2^attempt, capped at max_delay."""
        return min(float(self._retry_delay) * (2 ** attempt), float(self._max_retry_delay))

    async def _schedule_retry(self, task_id: str, attempt: int, reason: str) -> None:
        """Sleep for the backoff delay, then re-queue the task as assigned."""
        delay = self._calculate_retry_delay(attempt)
        logger.info(
            f"Retry scheduled | task_id={task_id} | attempt={attempt + 1} | "
            f"delay={delay:.0f}s | reason={reason[:200]}"
        )
        await asyncio.sleep(delay)
        ok, res = await self.lifecycle_service.execute_transition(
            task_id=task_id,
            new_status="assigned",
            changed_by="task-engine",
            reason=f"Auto-retry attempt {attempt + 1}: {reason[:200]}",
        )
        if ok:
            logger.info(f"Task re-queued for retry | task_id={task_id} | attempt={attempt + 1}")
        else:
            logger.error(
                f"Retry re-queue failed | task_id={task_id} | attempt={attempt + 1} | "
                f"error={res.get('error')}"
            )

    async def _handle_task_failure(
        self,
        task_id: str,
        task: dict[str, Any],
        reason: str,
        runtime: dict[str, Any] | None = None,
    ) -> None:
        """Apply retry-or-escalate policy after a task execution failure.

        If retry attempts remain: transitions to 'failed', then schedules a
        background retry after exponential-backoff delay.
        If retry budget is exhausted and escalation is enabled: transitions to
        'escalated' and sends an escalation notification.
        If retry budget is exhausted and escalation is disabled: stays 'failed'.
        """
        retry_count = int(task.get("retry_count") or 0)
        max_retries = int(task.get("max_retries") or 3)
        logger.warning(
            f"Task failure | task_id={task_id} | attempt={retry_count} | "
            f"max_retries={max_retries} | escalation_enabled={self._escalation_enabled} | "
            f"reason={reason[:200]}"
        )

        if retry_count < max_retries:
            await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status="failed",
                changed_by="task-engine",
                reason=reason,
            )
            await self.notifier.on_task_failed(task_id, reason, runtime=runtime)
            asyncio.create_task(self._schedule_retry(task_id, retry_count, reason))
        elif self._escalation_enabled:
            escalation_reason = f"Max retries ({max_retries}) exhausted: {reason[:300]}"
            await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status="escalated",
                changed_by="task-engine",
                reason=escalation_reason,
            )
            await self.notifier.on_task_escalated(task_id, escalation_reason, runtime=runtime)
            logger.warning(
                f"Task escalated after retry budget exhausted | task_id={task_id} | "
                f"attempts={max_retries}"
            )
        else:
            await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status="failed",
                changed_by="task-engine",
                reason=reason,
            )
            await self.notifier.on_task_failed(task_id, reason, runtime=runtime)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while self._running:
            try:
                self._reap_completed()
                await self._poll_cycle()
            except Exception as e:
                logger.error(f"Poll cycle error: {e}", exc_info=True)
            await asyncio.sleep(self.poll_interval)

    def _reap_completed(self) -> None:
        done = [tid for tid, t in self._execution_tasks.items() if t.done()]
        for tid in done:
            self._execution_tasks.pop(tid, None)
            if self.global_tracker and self.project_id:
                self.global_tracker.release(self.project_id)
            if self.agent_pool and self.project_id:
                self.agent_pool.release(self.project_id)

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    def _has_capacity(self, priority: str = "medium") -> bool:
        """Check per-project, global, and pool capacity.

        The priority is forwarded to the agent pool so high/critical tasks
        can access priority-reserved slots.
        """
        if not self.spawner.has_capacity:
            return False
        if self.global_tracker and not self.global_tracker.has_capacity():
            return False
        if self.agent_pool and self.project_id:
            if not self.agent_pool.has_capacity(self.project_id, priority):
                return False
        return True

    def _shared_checkout_conflicts_enabled(self) -> bool:
        """Return whether shared-checkout conflict prediction should run."""
        # Both "shared" (queue on conflict) and "on-conflict-worktree" (worktree on conflict)
        # use conflict detection; only per-task worktree skips it entirely.
        return self.project_config.isolation in {"shared", "on-conflict-worktree"}

    def _fetch_active_conflict_tasks(self) -> list[dict[str, Any]]:
        """Fetch currently executing tasks eligible for overlap prediction."""
        if not self._shared_checkout_conflicts_enabled():
            return []

        success, result = self.task_service.list_tasks(
            project_id=self.project_id,
            status="executing",
            include_closed=False,
            include_archived=False,
        )
        if not success:
            logger.warning(
                f"Failed to fetch executing tasks for conflict detection: {result.get('error')}"
            )
            return []

        tasks = result.get("tasks", [])
        return [task for task in tasks if task.get("status") == "executing"]

    def _predict_task_conflict(
        self,
        candidate_task: dict[str, Any],
        active_tasks: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Predict overlap against currently active or already reserved tasks."""
        if not self._shared_checkout_conflicts_enabled():
            return None

        for active_task in active_tasks:
            if conflict := predict_task_overlap(candidate_task, active_task):
                return conflict
        return None

    async def _poll_cycle(self) -> None:
        if not self._has_capacity():
            return

        self._poll_cycle_count += 1

        if self.project_id is None:
            logger.warning("No project_id set — polling ALL projects (may cause duplicate pickup)")

        # Auto-assign approved tasks every 3 cycles to avoid extra DB queries each poll
        if self._poll_cycle_count % 3 == 1:
            await self._assign_approved_tasks()

        success, result = self.task_service.list_tasks(
            project_id=self.project_id,
            status="assigned",
            include_closed=False,
            include_archived=False,
        )

        if not success:
            logger.warning(f"Failed to fetch assigned tasks: {result.get('error')}")
            return

        tasks = result.get("tasks", [])
        if not tasks:
            return

        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        tasks.sort(key=lambda t: (
            priority_order.get(t.get("priority", "medium"), 2),
            t.get("created_at", ""),
        ))

        # Check budget before spawning tasks
        if self.project_id:
            budget_ok, budget_details = self.cost_budget_service.check_budget(self.project_id)
            if not budget_ok:
                reason = budget_details.get("reason", "Budget exceeded")
                logger.warning(f"Budget exceeded — pausing project | project_id={self.project_id} | {reason}")
                await self.notifier.on_budget_exceeded(self.project_id, reason)
                return
            if budget_details.get("reason") == "approaching_limit":
                logger.warning(f"Budget approaching limit | project_id={self.project_id}")
                await self.notifier.on_budget_warning(self.project_id, budget_details.get("status", {}))

        # Filter out tasks blocked by unresolved dependencies
        executable_tasks = self._filter_blocked_tasks(tasks)
        reserved_tasks = self._fetch_active_conflict_tasks()

        for task in executable_tasks:
            task_priority = task.get("priority", "medium")
            if not self._has_capacity(task_priority):
                break
            task_id = task["id"]
            if task_id in self._execution_tasks:
                continue

            isolation_override: str | None = None
            conflict = self._predict_task_conflict(task, reserved_tasks)
            if conflict:
                if self.project_config.isolation == "on-conflict-worktree":
                    # Run conflicting task in a dedicated git worktree to avoid checkout conflicts
                    isolation_override = "git-worktree"
                    logger.info(
                        "Worktree isolation triggered by predicted overlap | "
                        f"task_id={task_id} | active_task_id={conflict.get('active_task_id')} | "
                        f"roots={conflict.get('overlapping_roots')}"
                    )
                else:
                    logger.info(
                        "Task queued due to predicted overlap | "
                        f"task_id={task_id} | active_task_id={conflict.get('active_task_id')} | "
                        f"roots={conflict.get('overlapping_roots')}"
                    )
                    continue

            # Acquire global slot before spawning
            if self.global_tracker and self.project_id:
                if not self.global_tracker.try_acquire(self.project_id):
                    logger.info(f"Global capacity limit reached | project_id={self.project_id}")
                    break

            # Acquire pool slot (priority-aware, per-project limited)
            if self.agent_pool and self.project_id:
                if not self.agent_pool.try_acquire(self.project_id, task_priority):
                    logger.info(
                        f"Pool capacity limit reached | "
                        f"pool_id={self.agent_pool.pool_id} | project_id={self.project_id} | "
                        f"priority={task_priority}"
                    )
                    # Release the global slot we just acquired
                    if self.global_tracker and self.project_id:
                        self.global_tracker.release(self.project_id)
                    continue

            reserved_tasks.append(task)
            # Wrap with task-level timeout guard: default_timeout + 5 min grace
            # to catch stuck tasks even if CC process-level timeout fails
            task_timeout = self.default_timeout + 300
            exec_task = asyncio.create_task(
                self._execute_task_with_timeout(task, task_timeout, isolation_override=isolation_override)
            )
            self._execution_tasks[task_id] = exec_task

    def _filter_blocked_tasks(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove tasks whose blocked_by dependencies are not all done.

        Collects all blocker IDs, fetches their statuses in a single query,
        then filters out any task that has unresolved blockers.
        """
        # Collect all unique blocker IDs across tasks
        blocker_ids: set[str] = set()
        for task in tasks:
            blocked_by = task.get("blocked_by") or []
            blocker_ids.update(blocked_by)

        if not blocker_ids:
            return tasks

        # Batch-fetch blocker statuses in a single query
        blocker_statuses: dict[str, str] = {}
        try:
            response = (
                self.task_service.supabase_client.table("archon_tasks")
                .select("id, status")
                .in_("id", list(blocker_ids))
                .execute()
            )
            for row in response.data or []:
                blocker_statuses[row["id"]] = row["status"]
        except Exception as e:
            logger.error(f"Failed to fetch blocker statuses: {e}", exc_info=True)
            return []  # Fail safe: don't execute anything if we can't check blockers

        executable: list[dict[str, Any]] = []
        for task in tasks:
            blocked_by = task.get("blocked_by") or []
            if not blocked_by:
                executable.append(task)
                continue

            unresolved = [
                bid for bid in blocked_by
                if blocker_statuses.get(bid, "unknown") != "done"
            ]

            if unresolved:
                logger.info(
                    "Task blocked by unresolved dependencies | "
                    f"task_id={task['id']} | "
                    f"blocker_ids={unresolved} | "
                    f"blocker_statuses={ {bid: blocker_statuses.get(bid, 'unknown') for bid in unresolved} }"
                )
            else:
                executable.append(task)

        return executable

    def _recheck_blocked_by(self, task_id: str, blocked_by: list[str]) -> list[str]:
        """Re-fetch and return the IDs of any blockers that are not yet done.

        Used as a last-moment guard before spawning to close the race window
        between _filter_blocked_tasks and actual task execution.

        Returns an empty list when all blockers are done (task is clear to run).
        On DB error, returns all blocker IDs (fail-safe: do not spawn).
        """
        if not blocked_by:
            return []

        try:
            response = (
                self.task_service.supabase_client.table("archon_tasks")
                .select("id, status")
                .in_("id", blocked_by)
                .execute()
            )
            statuses = {row["id"]: row["status"] for row in response.data or []}
            return [bid for bid in blocked_by if statuses.get(bid, "unknown") != "done"]
        except Exception as e:
            logger.error(
                f"Failed to re-check blocker statuses before spawn | "
                f"task_id={task_id} | error={e}",
                exc_info=True,
            )
            return blocked_by  # Fail safe

    async def _assign_approved_tasks(self) -> None:
        """Transition approved tasks to assigned so they get picked up for execution."""
        success, result = self.task_service.list_tasks(
            project_id=self.project_id,
            status="approved",
            include_closed=False,
            include_archived=False,
        )

        if not success or not result.get("tasks"):
            return

        for task in result["tasks"]:
            task_id = task["id"]
            ok, res = await self.lifecycle_service.execute_transition(
                task_id=task_id, new_status="assigned", changed_by="task-engine",
            )
            if ok:
                logger.info(f"Auto-assigned approved task | task_id={task_id}")
            else:
                logger.warning(f"Failed to auto-assign task | task_id={task_id} | error={res.get('error')}")

    # ------------------------------------------------------------------
    # Single task execution
    # ------------------------------------------------------------------

    async def _execute_task_with_timeout(
        self,
        task: dict[str, Any],
        timeout_seconds: int,
        isolation_override: str | None = None,
    ) -> CCExecutionResult:
        """Wrap _execute_task with a hard timeout guard.

        If the CC process-level timeout fails to fire (e.g. process dies
        without reporting, review loop hangs), this outer guard ensures the
        task slot is freed and the task is marked failed.
        """
        task_id = task["id"]
        try:
            return await asyncio.wait_for(
                self._execute_task(task, isolation_override=isolation_override), timeout=timeout_seconds,
            )
        except TimeoutError:
            logger.error(
                f"Task-level timeout exceeded | task_id={task_id} | "
                f"timeout={timeout_seconds}s — force-failing task"
            )
            # Kill any lingering CC process for this task
            try:
                await self.spawner.kill(task_id)
            except Exception:
                pass
            timeout_reason = f"Execution timed out after {timeout_seconds // 60}m"
            # Store partial context in execution_result before state transition
            await self.task_service.update_task(
                task_id=task_id,
                update_fields={
                    "execution_result": {
                        "timed_out": True,
                        "exit_code": -1,
                        "duration_seconds": timeout_seconds,
                        "partial_context": {
                            "timed_out": True,
                            "duration_seconds": timeout_seconds,
                            "reason": timeout_reason,
                        },
                    }
                },
            )
            await self._handle_task_failure(task_id, task, timeout_reason)
            return CCExecutionResult(
                success=False,
                stdout="",
                stderr=f"Task-level timeout after {timeout_seconds}s",
                exit_code=-1,
                duration_seconds=timeout_seconds,
            )

    async def _execute_task(
        self, task: dict[str, Any], isolation_override: str | None = None
    ) -> CCExecutionResult:
        pipeline_steps = task.get("pipeline_steps") or []
        if pipeline_steps:
            return await self._execute_pipeline_task(task, pipeline_steps, isolation_override=isolation_override)

        state = TaskExecutionState(
            task=task,
            task_id=task["id"],
            isolation_override=isolation_override,
        )
        state = await self._run_stage_chain(
            state,
            self._build_execute_stage_chain(),
            chain_name="execute",
        )
        if state.final_result is not None:
            return state.final_result

        logger.error(f"Execution middleware chain completed without result | task_id={state.task_id}")
        return CCExecutionResult(
            success=False,
            stdout="",
            stderr="Execution middleware chain completed without result",
            exit_code=-1,
            duration_seconds=0,
        )

    async def _execute_pipeline_task(
        self,
        task: dict[str, Any],
        pipeline_steps: list[dict[str, Any]],
        isolation_override: str | None = None,
    ) -> CCExecutionResult:
        """Execute a task with pipeline_steps sequentially with checkpoint persistence.

        Each step runs as an independent CC session. Its result is stored as a checkpoint
        in execution_result.pipeline_checkpoints. If a step fails, the task can be retried
        from the last successful checkpoint without re-running earlier steps.

        Pipeline progress is visible via execution_run records — one per step — with
        pipeline_step_index and pipeline_step_stage in their metadata.
        """
        task_id = task["id"]
        logger.info(
            f"Pipeline execution started | task_id={task_id} | steps={len(pipeline_steps)}"
        )

        # Load existing checkpoints from any prior partial execution
        existing_checkpoints = self._load_pipeline_checkpoints(task)
        last_completed_index = len(existing_checkpoints) - 1

        if last_completed_index >= 0:
            logger.info(
                f"Pipeline resuming from checkpoint | task_id={task_id} | "
                f"last_completed_step={last_completed_index} | "
                f"skipping={last_completed_index + 1} steps"
            )

        cumulative_duration = sum(cp.get("duration_seconds") or 0 for cp in existing_checkpoints)
        checkpoints = list(existing_checkpoints)
        last_result: CCExecutionResult | None = None

        for step_index, step in enumerate(pipeline_steps):
            step_stage = step.get("stage", f"step-{step_index}")

            # Skip already-completed steps (resume from checkpoint)
            if step_index <= last_completed_index:
                logger.info(
                    f"Pipeline step skipped (checkpoint exists) | "
                    f"task_id={task_id} | step={step_index} | stage={step_stage}"
                )
                continue

            logger.info(
                f"Pipeline step starting | task_id={task_id} | "
                f"step={step_index}/{len(pipeline_steps) - 1} | stage={step_stage}"
            )

            # Build a synthetic task for this step, overriding execution_prompt
            step_task = self._build_step_task(task, step, step_index, checkpoints)
            state = TaskExecutionState(
                task=step_task,
                task_id=task_id,
                isolation_override=isolation_override,
            )

            # Run the middleware chain for this step excluding notifier_publish;
            # pipeline completion is handled after all steps finish.
            state = await self._run_stage_chain(
                state,
                self._build_pipeline_step_stage_chain(),
                chain_name=f"pipeline-step-{step_index}",
            )

            step_result = state.final_result or state.runner_result
            if step_result is None:
                error_msg = f"Pipeline step {step_index} ({step_stage}) produced no result"
                logger.error(f"{error_msg} | task_id={task_id}")
                await self._handle_task_failure(task_id, task, error_msg)
                return CCExecutionResult(
                    success=False,
                    stdout="",
                    stderr=error_msg,
                    exit_code=-1,
                    duration_seconds=cumulative_duration,
                )

            cumulative_duration += step_result.duration_seconds or 0

            if not step_result.success or step_result.parsed.get("result", "").upper() == "FAILURE":
                error_msg = (
                    step_result.parsed.get("summary")
                    or step_result.stderr[:500]
                    or f"Pipeline step {step_index} ({step_stage}) failed"
                )
                logger.warning(
                    f"Pipeline step failed | task_id={task_id} | "
                    f"step={step_index} | stage={step_stage} | error={error_msg}"
                )
                # Checkpoints up to the previous step are already persisted;
                # failed step is not added so retry re-runs from this index.
                await self._persist_pipeline_checkpoints(task_id, checkpoints)
                await self._handle_task_failure(task_id, task, error_msg)
                return CCExecutionResult(
                    success=False,
                    stdout=step_result.stdout,
                    stderr=step_result.stderr,
                    exit_code=step_result.exit_code,
                    duration_seconds=cumulative_duration,
                    parsed=step_result.parsed,
                )

            checkpoint_data = self._build_step_checkpoint(
                step_index, step_stage, step, step_result, state
            )
            checkpoints.append(checkpoint_data)
            await self._persist_pipeline_checkpoints(task_id, checkpoints)
            last_result = step_result
            logger.info(
                f"Pipeline step completed | task_id={task_id} | "
                f"step={step_index} | stage={step_stage}"
            )

        # All steps completed — publish final result
        if last_result is None:
            # All steps were already satisfied by existing checkpoints on retry
            last_result = CCExecutionResult(
                success=True,
                stdout="All pipeline steps already completed via checkpoints",
                stderr="",
                exit_code=0,
                duration_seconds=cumulative_duration,
                parsed={"result": "SUCCESS", "summary": "All pipeline steps completed"},
            )

        await self._publish_pipeline_completion(task_id, task, checkpoints, last_result, cumulative_duration)
        return last_result

    def _build_pipeline_step_stage_chain(self) -> TaskExecutionStageChain:
        """Return the execution chain for a single pipeline step.

        Omits _stage_notifier_publish — pipeline completion is handled by
        _execute_pipeline_task after all steps finish.
        """
        return (
            self._stage_claim,
            self._stage_policy_load,
            self._stage_guidance_injection,
            self._stage_boundary_injection,
            self._stage_workspace_acquire,
            self._stage_lifecycle_restore,
            self._stage_runner_execute,
            self._stage_changed_file_validation,
            self._stage_artifact_capture,
        )

    @staticmethod
    def _build_step_task(
        task: dict[str, Any],
        step: dict[str, Any],
        step_index: int,
        prior_checkpoints: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a step-specific task dict that overrides execution_prompt with the step template.

        Injects prior checkpoint summaries as context so each step can build on
        the previous step's output.
        """
        step_task = dict(task)
        prompt_template = step.get("prompt_template") or ""
        if prior_checkpoints:
            checkpoint_context = "\n".join(
                f"## Step {cp['step_index']} ({cp['stage']}) Result\n{cp.get('summary', 'completed')}"
                for cp in prior_checkpoints
            )
            prompt_template = (
                f"## Pipeline Context (prior steps)\n{checkpoint_context}\n\n"
                f"## Current Step ({step_index})\n{prompt_template}"
            )
        if prompt_template:
            step_task["execution_prompt"] = prompt_template
        step_task["_pipeline_step_index"] = step_index
        step_task["_pipeline_step_stage"] = step.get("stage", f"step-{step_index}")
        return step_task

    @staticmethod
    def _build_step_checkpoint(
        step_index: int,
        step_stage: str,
        step: dict[str, Any],
        result: CCExecutionResult,
        state: TaskExecutionState,
    ) -> dict[str, Any]:
        """Build a checkpoint record for a completed pipeline step."""
        return {
            "step_index": step_index,
            "stage": step_stage,
            "checkpoint": step.get("checkpoint"),
            "execution_run_id": state.execute_run_id,
            "result": result.parsed.get("result", "SUCCESS") if result.success else "FAILURE",
            "summary": result.parsed.get("summary", ""),
            "files_changed": result.parsed.get("files_changed"),
            "duration_seconds": result.duration_seconds,
            "completed_at": datetime.now().isoformat(),
        }

    async def _persist_pipeline_checkpoints(
        self,
        task_id: str,
        checkpoints: list[dict[str, Any]],
    ) -> None:
        """Persist pipeline checkpoints to the task's execution_result."""
        ok, full = self.task_service.get_task(task_id)
        existing_result = full["task"].get("execution_result") or {} if ok else {}
        last_index = checkpoints[-1]["step_index"] if checkpoints else -1
        updated_result = {
            **existing_result,
            "pipeline_checkpoints": checkpoints,
            "pipeline_last_completed_step": last_index,
        }
        await self.task_service.update_task(
            task_id=task_id,
            update_fields={"execution_result": updated_result},
        )

    @staticmethod
    def _load_pipeline_checkpoints(task: dict[str, Any]) -> list[dict[str, Any]]:
        """Load existing pipeline checkpoints from a task's execution_result."""
        execution_result = task.get("execution_result") or {}
        checkpoints = execution_result.get("pipeline_checkpoints")
        if isinstance(checkpoints, list):
            return [cp for cp in checkpoints if isinstance(cp, dict)]
        return []

    async def _publish_pipeline_completion(
        self,
        task_id: str,
        task: dict[str, Any],
        checkpoints: list[dict[str, Any]],
        last_result: CCExecutionResult,
        total_duration: float,
    ) -> None:
        """Finalize pipeline task after all steps complete successfully."""
        total_files = sum(cp.get("files_changed") or 0 for cp in checkpoints)
        summary_parts = [f"Pipeline completed: {len(checkpoints)} steps"]
        if total_files:
            summary_parts.append(f"files_modified={total_files}")

        pipeline_result = {
            "exit_code": 0,
            "duration_seconds": total_duration,
            "timed_out": False,
            "result": "SUCCESS",
            "summary": " | ".join(summary_parts),
            "pipeline_checkpoints": checkpoints,
            "pipeline_last_completed_step": len(checkpoints) - 1,
            "files_changed": total_files,
        }
        await self.task_service.update_task(
            task_id=task_id,
            update_fields={"execution_result": pipeline_result},
        )

        await self.lifecycle_service.execute_transition(
            task_id=task_id,
            new_status="architect-review",
            changed_by="task-engine",
        )
        runtime = self._build_runtime_context(task, stage="execute", session_id=task_id)
        await self.notifier.on_task_completed(task_id, pipeline_result, runtime=runtime)
        await self._run_architect_review(task_id, pipeline_result)

    async def _run_stage_chain(
        self,
        state: TaskExecutionState,
        stages: TaskExecutionStageChain,
        *,
        chain_name: str,
    ) -> TaskExecutionState:
        """Run one ordered middleware chain and enforce the stage state contract."""
        for stage in stages:
            next_state = await stage(state)
            if not isinstance(next_state, TaskExecutionState):
                stage_name = getattr(stage, "__name__", stage.__class__.__name__)
                raise TypeError(
                    f"{chain_name} stage {stage_name} returned {type(next_state).__name__}; "
                    "expected TaskExecutionState"
                )
            state = next_state
            if state.final_result is not None:
                return state
        return state

    def _build_execute_stage_chain(self) -> TaskExecutionStageChain:
        """Return the ordered execution middleware chain.

        Each stage accepts ``TaskExecutionState`` and returns the updated state.
        Add a new stage by appending it to this tuple.
        """
        return (
            self._stage_claim,
            self._stage_policy_load,
            self._stage_guidance_injection,
            self._stage_boundary_injection,
            self._stage_workspace_acquire,
            self._stage_lifecycle_restore,
            self._stage_runner_execute,
            self._stage_changed_file_validation,
            self._stage_artifact_capture,
            self._stage_notifier_publish,
        )

    def _build_completion_stage_chain(self) -> TaskExecutionStageChain:
        """Return post-run stages shared by `_execute_task` and `_on_cc_complete`."""
        return (
            self._stage_changed_file_validation,
            self._stage_artifact_capture,
            self._stage_notifier_publish,
        )

    async def _stage_claim(self, state: TaskExecutionState) -> TaskExecutionState:
        """Claim the task payload and fail fast when blockers still exist."""
        blocked_by = state.task.get("blocked_by") or []
        still_blocked = self._recheck_blocked_by(state.task_id, blocked_by)
        if still_blocked:
            logger.warning(
                "Task blocked at spawn time — aborting execution | "
                f"task_id={state.task_id} | blocker_ids={still_blocked}"
            )
            state.final_result = CCExecutionResult(
                success=False,
                stdout="",
                stderr=f"Task blocked by unresolved dependencies: {still_blocked}",
                exit_code=-1,
                duration_seconds=0,
            )
            return state

        ok, full = self.task_service.get_task(state.task_id)
        if not ok:
            logger.error(f"Failed to get task for prompt | task_id={state.task_id}")
            state.final_result = CCExecutionResult(
                success=False,
                stdout="",
                stderr="Failed to fetch task",
                exit_code=-1,
                duration_seconds=0,
            )
            return state

        state.full_task = full["task"]
        return state

    async def _stage_policy_load(self, state: TaskExecutionState) -> TaskExecutionState:
        """Load routing policy, approval state, and the execution lifecycle claim."""
        if state.full_task is None:
            return state

        state.project_id = self._resolve_project_id(state.full_task)
        state.retry_index = int(state.full_task.get("retry_count") or 0)
        state.policy = self._get_project_policy(state.full_task)
        state.effective_project_config = self._project_config_for_policy(state.policy)

        if state.isolation_override and state.effective_project_config.isolation != state.isolation_override:
            state.effective_project_config = replace(
                state.effective_project_config,
                isolation=state.isolation_override,
            )
            logger.info(
                f"Isolation overridden for task | task_id={state.task_id} | isolation={state.isolation_override}"
            )

        state.model_routing = state.policy.get("model_routing") or None if state.policy else None
        if os.environ.get("LEANKIT_ENGINE_DISABLE_CODEX", "").lower() in ("1", "true", "yes"):
            state.model_routing = state.model_routing or {}
            state.model_routing["disable_codex"] = True

        if state.model_routing and state.model_routing.get("force_model"):
            state.full_task = {**state.full_task, "_policy_force_model": state.model_routing["force_model"]}

        # Collaboration mode gate: check how architect-planned tasks should be handled
        collaboration_blocked, collaboration_reason = await self._check_collaboration_gate(
            state.task_id,
            state.full_task,
            state.model_routing,
            state.project_id,
        )
        if collaboration_blocked:
            state.final_result = CCExecutionResult(
                success=False,
                stdout="",
                stderr=collaboration_reason,
                exit_code=-2,
                duration_seconds=0,
            )
            return state

        if not await self._pass_approval_gate(
            state.task_id,
            state.full_task,
            state.model_routing,
            state.project_id,
        ):
            state.final_result = CCExecutionResult(
                success=False,
                stdout="",
                stderr="Awaiting approval for high-risk task",
                exit_code=-2,
                duration_seconds=0,
            )
            return state

        ok, res = await self.lifecycle_service.execute_transition(
            task_id=state.task_id,
            new_status="executing",
            changed_by="task-engine",
        )
        if not ok:
            logger.error(
                f"Cannot transition to executing | task_id={state.task_id} | error={res.get('error')}"
            )
            state.final_result = CCExecutionResult(
                success=False,
                stdout="",
                stderr=res.get("error", ""),
                exit_code=-1,
                duration_seconds=0,
            )
        return state

    async def _stage_guidance_injection(self, state: TaskExecutionState) -> TaskExecutionState:
        """Build the unified prompt and record prompt injection statistics."""
        if state.full_task is None:
            return state

        state.prompt, state.injection_stats = await self.prompt_builder.build(
            task=state.full_task,
            build_command=self.project_config.build_command,
        )
        logger.info(
            f"Injection stats | task_id={state.task_id} | "
            f"learnings={state.injection_stats['learnings']} | "
            f"patterns={state.injection_stats['patterns']} | "
            f"kb_chunks={state.injection_stats['kb_chunks']} | "
            f"tokens={state.injection_stats['tokens']}"
        )
        return state

    async def _stage_boundary_injection(self, state: TaskExecutionState) -> TaskExecutionState:
        """Capture the pre-run workspace snapshot used for boundary validation."""
        if state.full_task is None or not task_has_boundary_rules(state.full_task):
            return state

        try:
            state.boundary_snapshot = await self._capture_dirty_file_snapshot()
        except Exception as exc:
            state.boundary_snapshot_error = str(exc)
        return state

    async def _stage_workspace_acquire(self, state: TaskExecutionState) -> TaskExecutionState:
        """Resolve runner, token profile, runtime metadata, and execution-run tracking."""
        if state.full_task is None or state.effective_project_config is None:
            return state

        try:
            state.runner, state.runner_key, state.routing_reason = self._get_runner_adapter(
                state.full_task,
                model_routing=state.model_routing,
                stage="execute",
            )
        except ValueError as exc:
            reason = str(exc)
            logger.error(f"Runner selection failed | task_id={state.task_id} | error={reason}")
            await self._handle_task_failure(state.task_id, state.full_task, reason)
            state.final_result = CCExecutionResult(
                success=False,
                stdout="",
                stderr=reason,
                exit_code=-1,
                duration_seconds=0,
            )
            return state

        effective_force_model = (
            (state.model_routing.get("force_model") if state.model_routing else None)
            or self.project_config.force_model
        )
        state.selected_model = state.runner.select_model(
            task=state.full_task,
            force_model=effective_force_model,
        )
        token_profiles = _load_token_profiles()
        policy_token_profile_name: str | None = None
        if state.model_routing:
            token_profile_overrides = state.model_routing.get("token_profile_overrides") or {}
            policy_token_profile_name = token_profile_overrides.get("execute") or token_profile_overrides.get("*")
        if state.model_routing:
            fallback_policy = state.model_routing.get("fallback_policy") or {}
            if isinstance(fallback_policy, dict) and "model_fallback_chain" in fallback_policy:
                state.model_fallback_chain = fallback_policy["model_fallback_chain"]
        state.token_profile_name = policy_token_profile_name or select_token_profile(state.full_task)
        state.token_profile = token_profiles.get(state.token_profile_name)
        if state.token_profile:
            logger.info(
                f"Token profile selected | task_id={state.task_id} | profile={state.token_profile_name} | "
                f"max_tokens={state.token_profile.get('max_tokens')} | "
                f"model_hint={state.token_profile.get('model_hint')}"
            )

        state.runner_source_app = self._runner_source_app(state.runner, state.runner_key)
        state.execute_run_id = await self._create_execution_run(
            task_id=state.task_id,
            project_id=state.project_id,
            stage="execute",
            status="running",
            session_id=state.task_id,
            model=state.selected_model,
            retry_index=state.retry_index,
            metadata={
                "runner_key": state.runner_key,
                "source_app": state.runner_source_app,
                "runner_routing_reason": state.routing_reason,
                "token_profile_name": state.token_profile_name,
                "token_profile": state.token_profile,
                "context_snapshot": {
                    "task_id": state.task_id,
                    "title": state.full_task.get("title"),
                    "acceptance_criteria": state.full_task.get("acceptance_criteria") or [],
                    "files_in_scope": state.full_task.get("files_in_scope") or [],
                    "retry_index": state.retry_index,
                },
            },
        )
        # Create run-local workspace directory keyed by execution_run_id
        if state.execute_run_id:
            try:
                workspace_manager = RunWorkspaceManager()
                workspace_manager.cleanup_expired()
                state.run_workspace_ctx = workspace_manager.create(state.execute_run_id)
            except Exception as exc:
                logger.warning(
                    f"RunWorkspace creation failed (non-fatal) | "
                    f"run_id={state.execute_run_id} | error={exc}"
                )
        state.execute_runtime = self._build_runtime_context(
            state.full_task,
            stage="execute",
            execution_run_id=state.execute_run_id,
            session_id=state.task_id,
        )
        return state

    async def _stage_lifecycle_restore(self, state: TaskExecutionState) -> TaskExecutionState:
        """Restore execution lifecycle observers before handing control to the runner."""
        if state.full_task is None or state.runner is None:
            return state

        await self.notifier.on_task_started(state.full_task, runtime=state.execute_runtime)
        state.exec_health_monitor = ExecutionHealthMonitor()

        async def _on_stream(tid: str, stream_event: dict[str, Any]) -> None:
            await self.notifier.on_agent_status(
                task_id=tid,
                agent_id=tid,
                stream_event=stream_event,
                runtime=self._build_runtime_context(
                    state.full_task or {"id": tid},
                    stage="execute",
                    execution_run_id=state.execute_run_id,
                    session_id=tid,
                    agent_id=tid,
                ),
            )
            if state.abort_reasons:
                return
            if state.exec_health_monitor is None:
                return
            state.exec_health_monitor.on_stream_event(tid, stream_event)
            health_status = state.exec_health_monitor.check_health(tid)
            if health_status != HEALTH_HEALTHY:
                reason = state.exec_health_monitor.get_abort_reason(tid)
                state.abort_reasons.append(reason)
                logger.warning(
                    f"Execution health violation | task_id={tid} | status={health_status} | reason={reason}"
                )
                await state.runner.kill(tid)

        state.stream_callback = _on_stream
        return state

    async def _stage_runner_execute(self, state: TaskExecutionState) -> TaskExecutionState:
        """Run the task with the selected runner and apply runner fallback when needed."""
        if (
            state.full_task is None
            or state.runner is None
            or state.prompt is None
            or state.effective_project_config is None
        ):
            return state

        runtime_metadata = {
            "source_app": self._resolve_source_app(state.full_task),
            "task_id": state.task_id,
            "execution_run_id": state.execute_run_id,
            "session_id": state.task_id,
            "agent_id": state.task_id,
            "stage": "execute",
        }
        state.runner_result = await state.runner.spawn(
            task_id=state.task_id,
            prompt=state.prompt,
            config=state.effective_project_config,
            task=state.full_task,
            runtime_metadata=runtime_metadata,
            on_stream_event=state.stream_callback,
            token_profile=state.token_profile,
            model_fallback_chain=state.model_fallback_chain,
            workspace_context=state.run_workspace_ctx,
        )

        if is_runner_level_failure(state.runner_result) and not state.abort_reasons:
            fallback_chain = get_fallback_runner_chain(
                state.runner_key,
                set(self.runner_adapters.keys()),
                state.model_routing,
            )
            for fallback_runner_key in fallback_chain:
                fallback_runner = self.runner_adapters.get(fallback_runner_key)
                if fallback_runner is None:
                    continue
                logger.warning(
                    f"Runner {state.runner_key} failed at runner level, trying fallback {fallback_runner_key} | "
                    f"task_id={state.task_id} | error={state.runner_result.stderr[:200]}"
                )
                fallback_result = await fallback_runner.spawn(
                    task_id=state.task_id,
                    prompt=state.prompt,
                    config=state.effective_project_config,
                    task=state.full_task,
                    runtime_metadata=runtime_metadata,
                    on_stream_event=state.stream_callback,
                    token_profile=state.token_profile,
                    model_fallback_chain=state.model_fallback_chain,
                    workspace_context=state.run_workspace_ctx,
                )
                state.runner_fallback_decisions.append(
                    {
                        "attempted_runner": state.runner_key,
                        "fallback_runner": fallback_runner_key,
                        "trigger": "runner_level_failure",
                        "original_error": state.runner_result.stderr[:200],
                        "fallback_success": not is_runner_level_failure(fallback_result),
                    }
                )
                if not is_runner_level_failure(fallback_result):
                    state.runner = fallback_runner
                    state.runner_key = fallback_runner_key
                    state.runner_source_app = self._runner_source_app(fallback_runner, fallback_runner_key)
                    state.runner_result = fallback_result
                    logger.info(
                        f"Runner fallback succeeded | task_id={state.task_id} | "
                        f"fallback_runner={fallback_runner_key}"
                    )
                    break

        if state.runner_fallback_decisions:
            await self._update_execution_run(
                state.execute_run_id,
                metadata={
                    "runner_key": state.runner_key,
                    "runner_fallback_decisions": state.runner_fallback_decisions,
                },
            )

        if state.abort_reasons and state.runner_result is not None:
            abort_reason = state.abort_reasons[0]
            metrics_snapshot = {}
            if state.exec_health_monitor is not None:
                metrics_snapshot = state.exec_health_monitor.get_metrics_snapshot(state.task_id)
            logger.warning(
                f"Task aborted by execution health monitor | task_id={state.task_id} | "
                f"reason={abort_reason} | metrics={metrics_snapshot}"
            )
            await self._update_execution_run(
                state.execute_run_id,
                status="failed",
                finished_at=datetime.now().isoformat(),
                duration_seconds=state.runner_result.duration_seconds,
                error_summary=abort_reason[:500],
            )
            await self.lifecycle_service.execute_transition(
                task_id=state.task_id,
                new_status="failed",
                changed_by="task-engine",
                reason=abort_reason,
            )
            await self.notifier.on_task_escalated(
                state.task_id,
                abort_reason,
                runtime=self._build_runtime_context(
                    state.full_task,
                    stage="execute",
                    execution_run_id=state.execute_run_id,
                    session_id=state.task_id,
                ),
            )
            state.final_result = state.runner_result
        return state

    async def _stage_changed_file_validation(self, state: TaskExecutionState) -> TaskExecutionState:
        """Load the latest task payload and compute changed-file boundary validation."""
        if state.runner_result is None:
            return state

        ok, full = self.task_service.get_task(state.task_id)
        state.current_task = full["task"] if ok else {"id": state.task_id}
        state.boundary_validation = await self._build_boundary_validation(
            state.current_task,
            before_snapshot=state.boundary_snapshot,
            snapshot_error=state.boundary_snapshot_error,
        )
        return state

    async def _stage_artifact_capture(self, state: TaskExecutionState) -> TaskExecutionState:
        """Persist execution artifacts, metrics, and task-level result metadata."""
        if state.runner_result is None:
            return state

        boundary_validation = state.boundary_validation or {"changed_files": []}
        state.execution_result = {
            "exit_code": state.runner_result.exit_code,
            "duration_seconds": state.runner_result.duration_seconds,
            "timed_out": state.runner_result.timed_out,
            **state.runner_result.parsed,
            "changed_files": boundary_validation.get("changed_files", []),
            "boundary_validation": boundary_validation,
        }
        if state.injection_stats:
            state.execution_result["injection"] = state.injection_stats
        if state.runner_result.stderr:
            state.execution_result["stderr_preview"] = state.runner_result.stderr[:1000]
        if state.runner_result.stdout:
            state.execution_result["stdout"] = state.runner_result.stdout

        state.executed_by = {
            "model": state.runner_result.parsed.get("model_used", "unknown"),
            "session_id": state.task_id,
            "source_app": state.runner_source_app,
            "runner_key": state.runner_key,
            "duration_seconds": state.runner_result.duration_seconds,
        }
        if state.routing_reason:
            state.executed_by["runner_routing_reason"] = state.routing_reason
        if state.runner_result.parsed.get("fallback_from"):
            state.executed_by["fallback_from"] = state.runner_result.parsed["fallback_from"]

        state.run_status = (
            "completed"
            if state.runner_result.success and state.runner_result.parsed.get("result", "").upper() != "FAILURE"
            else "failed"
        )
        state.run_metadata = {
            "result": state.runner_result.parsed.get("result"),
            "files_changed": state.runner_result.parsed.get("files_changed"),
            "tests_added": state.runner_result.parsed.get("tests_added"),
            "runner_key": state.runner_key,
            "runner_routing_reason": state.routing_reason,
            "changed_files": boundary_validation.get("changed_files", []),
            "boundary_validation": boundary_validation,
        }
        if state.runner_fallback_decisions:
            state.run_metadata["runner_fallback_decisions"] = state.runner_fallback_decisions
        if state.runner_result.parsed.get("model_fallback_chain"):
            state.run_metadata["model_fallback_chain"] = state.runner_result.parsed["model_fallback_chain"]
        injected_env_keys = state.runner_result.parsed.get("injected_env_keys")
        if injected_env_keys is not None:
            state.run_metadata["injected_env_keys"] = injected_env_keys

        if state.runner_result.timed_out or not state.runner_result.success:
            partial_context = self._extract_partial_context(state.runner_result)
            if (
                state.runner_result.timed_out
                or partial_context.get("files_modified")
                or partial_context.get("partial_output")
            ):
                state.run_metadata["partial_context"] = partial_context
                state.execution_result["partial_context"] = partial_context

        state.llm_metrics = {}
        if state.runner_result.parsed.get("llm_input_tokens") is not None:
            state.llm_metrics["token_input"] = state.runner_result.parsed["llm_input_tokens"]
        if state.runner_result.parsed.get("llm_output_tokens") is not None:
            state.llm_metrics["token_output"] = state.runner_result.parsed["llm_output_tokens"]
        if state.runner_result.parsed.get("llm_total_tokens") is not None:
            state.llm_metrics["total_tokens"] = state.runner_result.parsed["llm_total_tokens"]
        if state.runner_result.parsed.get("llm_thinking_tokens") is not None:
            state.llm_metrics["thinking_tokens"] = state.runner_result.parsed["llm_thinking_tokens"]
        if state.runner_result.parsed.get("llm_cost_usd") is not None:
            state.llm_metrics["cost_usd"] = state.runner_result.parsed["llm_cost_usd"]

        summary_parts: list[str] = []
        if state.runner_result.parsed.get("summary"):
            summary_parts.append(state.runner_result.parsed["summary"])
        files_changed = state.runner_result.parsed.get("files_changed")
        if files_changed is not None:
            summary_parts.append(f"files_modified={files_changed}")
        tests_added = state.runner_result.parsed.get("tests_added")
        if tests_added is not None:
            summary_parts.append(f"tests_passed={tests_added}")
        if state.runner_result.timed_out:
            summary_parts.append(f"timed_out=true duration={state.runner_result.duration_seconds:.0f}s")
        state.result_summary = " | ".join(summary_parts) if summary_parts else None
        if state.result_summary:
            state.execution_result["run_result_summary"] = state.result_summary

        # Scan workspace for artifacts and attach manifest to run metadata
        if state.run_workspace_ctx is not None:
            try:
                workspace_manager = RunWorkspaceManager()
                project_path = (
                    state.effective_project_config.project_path
                    if state.effective_project_config
                    else None
                )
                artifact_manifest = workspace_manager.scan_artifacts(
                    state.run_workspace_ctx,
                    project_path=project_path,
                )
                state.run_metadata["artifact_manifest"] = artifact_manifest
            except Exception as exc:
                logger.warning(
                    f"RunWorkspace artifact scan failed (non-fatal) | "
                    f"run_id={state.execute_run_id} | error={exc}"
                )

        workspace_path_str = (
            str(state.run_workspace_ctx.log_dir)
            if state.run_workspace_ctx is not None
            else None
        )
        await self._update_execution_run(
            state.execute_run_id,
            status=state.run_status,
            finished_at=datetime.now().isoformat(),
            duration_seconds=state.runner_result.duration_seconds,
            model=state.runner_result.parsed.get("model_used"),
            result_summary=state.result_summary,
            error_summary=(state.runner_result.stderr[:500] if state.runner_result.stderr else None),
            workspace_path=workspace_path_str,
            metadata=state.run_metadata,
            **state.llm_metrics,
        )
        await self.task_service.update_task(
            task_id=state.task_id,
            update_fields={
                "execution_result": state.execution_result,
                "executed_by": state.executed_by,
            },
        )
        if self.project_id and state.execution_result is not None:
            self.cost_budget_service.record_task_cost(self.project_id, state.task_id, state.execution_result)
        return state

    async def _stage_notifier_publish(self, state: TaskExecutionState) -> TaskExecutionState:
        """Publish post-run notifications and dispatch follow-up review or failure handling."""
        if state.runner_result is None:
            return state

        current_task = state.current_task or {"id": state.task_id}
        boundary_validation = state.boundary_validation or {}
        boundary_status = boundary_validation.get("status")
        if state.runner_result.success and boundary_status in {"violation", "unavailable"}:
            violation_reason = boundary_validation.get("reason")
            if not violation_reason:
                changed = boundary_validation.get("changed_files", [])
                subject = changed[0] if changed else "unknown"
                if boundary_status == "unavailable":
                    violation_reason = "Task editing boundaries could not be validated"
                else:
                    violation_reason = f"Task editing boundary violated: {subject}"
            await self.lifecycle_service.execute_transition(
                task_id=state.task_id,
                new_status="escalated",
                changed_by="task-engine",
                reason=violation_reason,
            )
            await self.notifier.on_task_escalated(
                state.task_id,
                violation_reason,
                runtime=self._build_runtime_context(
                    current_task,
                    stage="execute",
                    execution_run_id=state.execute_run_id,
                    session_id=state.task_id,
                ),
            )
            logger.warning(
                f"Task escalated due to boundary validation | task_id={state.task_id} | status={boundary_status}"
            )
            state.final_result = state.runner_result
            return state

        if state.runner_result.success and state.runner_result.parsed.get("result", "").upper() != "FAILURE":
            await self.lifecycle_service.execute_transition(
                task_id=state.task_id,
                new_status="architect-review",
                changed_by="task-engine",
            )
            await self.notifier.on_task_completed(
                state.task_id,
                state.execution_result or {},
                runtime=self._build_runtime_context(
                    current_task,
                    stage="execute",
                    execution_run_id=state.execute_run_id,
                    session_id=state.task_id,
                ),
            )
            await self._run_architect_review(state.task_id, state.execution_result or {})
            state.final_result = state.runner_result
            return state

        reason = state.runner_result.parsed.get("summary") or state.runner_result.stderr[:500] or "CC session failed"
        await self._handle_task_failure(
            state.task_id,
            current_task,
            reason,
            runtime=self._build_runtime_context(
                current_task,
                stage="execute",
                execution_run_id=state.execute_run_id,
                session_id=state.task_id,
            ),
        )
        state.final_result = state.runner_result
        return state

    async def _on_cc_complete(
        self,
        task_id: str,
        result: CCExecutionResult,
        injection_stats: dict[str, Any] | None = None,
        execute_run_id: str | None = None,
        runner_key: str = DEFAULT_RUNNER_KEY,
        runner_source_app: str = DEFAULT_RUNNER_KEY,
        routing_reason: str | None = None,
        boundary_snapshot: dict[str, str] | None = None,
        boundary_snapshot_error: str | None = None,
        runner_fallback_decisions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Handle CC completion using the same post-run middleware stages."""
        state = TaskExecutionState(
            task={"id": task_id},
            task_id=task_id,
            injection_stats=injection_stats,
            boundary_snapshot=boundary_snapshot,
            boundary_snapshot_error=boundary_snapshot_error,
            runner_key=runner_key,
            runner_source_app=runner_source_app,
            routing_reason=routing_reason,
            execute_run_id=execute_run_id,
            runner_result=result,
            runner_fallback_decisions=runner_fallback_decisions or [],
        )
        await self._run_stage_chain(
            state,
            self._build_completion_stage_chain(),
            chain_name="completion",
        )

    async def _run_architect_review(
        self,
        task_id: str,
        execution_result: dict[str, Any],
    ) -> None:
        ok, full = self.task_service.get_task(task_id)
        if not ok:
            logger.error(f"Cannot fetch task for review | task_id={task_id}")
            return

        task = full["task"]
        project_id = self._resolve_project_id(task)
        policy = self._get_project_policy(task)
        effective_review_config = self._review_config_for_policy(policy)
        model_routing = policy.get("model_routing") or None if policy else None
        collaboration_mode = resolve_collaboration_mode(task, model_routing)
        architect_run_id = await self._create_execution_run(
            task_id=task_id,
            project_id=project_id,
            stage="architect-review",
            status="reviewing",
            session_id=task_id,
            model=effective_review_config.model or effective_review_config.provider or "self-review",
            retry_index=int(task.get("retry_count") or 0),
            metadata={
                "mode": effective_review_config.review_mode,
                "collaboration_mode": collaboration_mode,
            },
        )

        review_result, action = await self.architect_reviewer.review(
            task, execution_result, config=effective_review_config,
        )

        # Calculate quality gate score
        quality_gate = calculate_quality_gate_score(review_result, execution_result)

        # Build review history entry (append-only)
        existing_history = task.get("review_history") or []
        if not isinstance(existing_history, list):
            existing_history = []
        history_entry = build_review_history_entry(
            review=review_result,
            action=action,
            quality_gate=quality_gate,
            task=task,
            existing_history=existing_history,
        )
        updated_history = existing_history + [history_entry.to_dict()]

        # Store review on task
        review_data: dict[str, Any] = {
            "verdict": review_result.verdict,
            "confidence": review_result.confidence,
            "findings": review_result.findings,
            "feedback": review_result.feedback,
            "summary": review_result.summary,
            "mode": review_result.mode,
            "quality_gate_score": quality_gate.to_dict(),
            "collaboration_mode": collaboration_mode,
        }
        if action.warning:
            review_data["warning"] = action.warning
        if review_result.error:
            review_data["error"] = review_result.error
        if action.escalation_reason:
            review_data["escalation_reason"] = action.escalation_reason

        # Store multi-perspective breakdown if available
        if review_result.mode == "multi-perspective" and review_result.raw_response:
            import json
            try:
                review_data["multi_perspective"] = json.loads(review_result.raw_response)
            except (json.JSONDecodeError, TypeError):
                pass

        # Build reviewed_by entry and append to existing array
        review_entry = {
            "stage": "architect-review",
            "agent": "architect-reviewer",
            "model": review_result.provider or "self-review",
            "actor": action.changed_by,
            "verdict": review_result.verdict,
            "confidence": review_result.confidence,
            "mode": review_result.mode,
            "collaboration_mode": collaboration_mode,
        }
        existing_reviewed_by = task.get("reviewed_by") or []
        if not isinstance(existing_reviewed_by, list):
            existing_reviewed_by = []
        updated_reviewed_by = existing_reviewed_by + [review_entry]

        update_fields: dict[str, Any] = {
            "architect_review": review_data,
            "reviewed_by": updated_reviewed_by,
            "review_history": updated_history,
        }
        if action.next_status == "escalated":
            update_fields["rejection_reason"] = action.reason

        await self.task_service.update_task(task_id=task_id, update_fields=update_fields)

        logger.info(
            f"Quality gate | task_id={task_id} | "
            f"score={quality_gate.compound_score} | gate={quality_gate.gate_result} | "
            f"review_round={history_entry.review_number}"
        )

        # Lifecycle transition — intercept "review" to route through code-review stage
        if action.next_status == "review":
            # Auto-create bug tasks from self-review findings before code review
            if review_result.findings:
                bug_tasks = await self.bug_task_creator.create_bug_tasks_from_findings(
                    parent_task=task,
                    findings=review_result.findings,
                )
                if bug_tasks:
                    review_data["bug_tasks_created"] = [
                        {"id": bt.get("id"), "severity": bt.get("priority"), "status": bt.get("status")}
                        for bt in bug_tasks
                    ]
                    await self.task_service.update_task(
                        task_id=task_id, update_fields={"architect_review": review_data}
                    )

            if effective_review_config.independent_review_enabled:
                # Self-review approved → transition to code-review for independent CC review
                await self.lifecycle_service.execute_transition(
                    task_id=task_id,
                    new_status="code-review",
                    changed_by="architect-reviewer",
                    reason=action.reason,
                )
                # Run independent code review (fresh CC session)
                await self._run_code_review(task_id, execution_result)
            else:
                # Independent review disabled → go directly to review
                await self.lifecycle_service.execute_transition(
                    task_id=task_id,
                    new_status="review",
                    changed_by="architect-reviewer",
                    reason=action.reason,
                )
                await self.notifier.on_task_review_ready(
                    task_id,
                    review_data,
                    runtime=self._build_runtime_context(
                        task,
                        stage="architect-review",
                        execution_run_id=architect_run_id,
                        session_id=task_id,
                    ),
                )
        else:
            # Non-review transitions (assigned/escalated) — apply directly
            await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status=action.next_status,
                changed_by=action.changed_by,
                reason=action.reason,
            )

            if action.next_status == "escalated":
                await self.notifier.on_task_escalated(
                    task_id,
                    action.reason,
                    runtime=self._build_runtime_context(
                        task,
                        stage="architect-review",
                        execution_run_id=architect_run_id,
                        session_id=task_id,
                    ),
                )

            # Auto-create bug tasks from review findings
            if review_result.findings:
                bug_tasks = await self.bug_task_creator.create_bug_tasks_from_findings(
                    parent_task=task,
                    findings=review_result.findings,
                )
                if bug_tasks:
                    review_data["bug_tasks_created"] = [
                        {"id": bt.get("id"), "severity": bt.get("priority"), "status": bt.get("status")}
                        for bt in bug_tasks
                    ]
                    await self.task_service.update_task(
                        task_id=task_id, update_fields={"architect_review": review_data}
                    )

        # Process learnings and code patterns from CC output (regardless of review outcome)
        learnings = execution_result.get("learnings", [])
        code_patterns = execution_result.get("code_patterns", [])
        if learnings or code_patterns:
            await self.learning_processor.process(
                task, learnings, code_patterns, execution_run_id=architect_run_id
            )

        await self._update_execution_run(
            architect_run_id,
            status="completed",
            finished_at=datetime.now().isoformat(),
            result_summary=review_result.summary,
            error_summary=review_result.error,
            metadata={
                "verdict": review_result.verdict,
                "confidence": review_result.confidence,
                "next_status": action.next_status,
                "mode": review_result.mode,
            },
        )

        logger.info(
            "Architect review applied | task_id=%s | verdict=%s → %s | collaboration_mode=%s",
            task_id, review_result.verdict, action.next_status, collaboration_mode,
        )

    # ------------------------------------------------------------------
    # Independent Code Review (Stage 2)
    # ------------------------------------------------------------------

    # Max review cycles before escalation
    MAX_CODE_REVIEW_CYCLES = 2

    @staticmethod
    def _build_code_review_prompt(
        task: dict[str, Any],
        git_diff: str,
    ) -> str:
        """Build a reviewer prompt for an independent code review CC session.

        This prompt is intentionally different from the coder prompt —
        it focuses on reviewing changes, not implementing them.
        """
        title = task.get("title", "Untitled")
        description = task.get("description") or "_No description_"
        criteria = task.get("acceptance_criteria") or []

        criteria_text = "\n".join(
            f"- {c.get('text', str(c)) if isinstance(c, dict) else str(c)}"
            for c in criteria
        ) or "- Task completed as described"

        return "\n".join([
            "# Independent Code Review",
            "",
            "You are a code reviewer. Review the git diff below against the task requirements.",
            "You are NOT the author of this code. Provide an independent, objective review.",
            "",
            "## Review Criteria",
            "1. CORRECTNESS: Does the implementation match the requirements and acceptance criteria?",
            "2. TESTS: Are there sufficient tests? Edge cases covered?",
            "3. SECURITY: Any vulnerabilities? (XSS, injection, auth bypass, key exposure)",
            "4. CONVENTIONS: Does the code follow project standards?",
            "5. BACKWARD COMPAT: Any breaking changes to existing APIs?",
            "6. PERFORMANCE: N+1 queries? Large allocations? Unnecessary complexity?",
            "",
            f"## Task: {title}",
            "",
            "### Description",
            description,
            "",
            "### Acceptance Criteria",
            criteria_text,
            "",
            "## Git Diff to Review",
            "```diff",
            git_diff[:50000] if git_diff else "_No diff available_",
            "```",
            "",
            "## Required Output",
            "After reviewing the diff, output EXACTLY these fields:",
            "",
            "CODE_REVIEW_VERDICT: APPROVE|REQUEST_CHANGES",
            'CODE_REVIEW_FINDINGS: [{"severity":"critical|warning|suggestion","category":"...","description":"...","file":"...","line":"..."}]',
            "",
            "If approving with no findings: CODE_REVIEW_FINDINGS: []",
            "If requesting changes, include specific actionable findings.",
        ])

    async def _get_git_diff(self, task_id: str) -> str:
        """Get the git diff for the task's changes."""
        import asyncio

        try:
            process = await asyncio.create_subprocess_exec(
                "git", "diff", "HEAD~1",
                cwd=self.project_config.project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=30,
            )
            if process.returncode == 0 and stdout:
                return stdout.decode()
            logger.warning(f"git diff failed | task_id={task_id} | stderr={stderr.decode()[:200]}")
            return ""
        except Exception as e:
            logger.warning(f"git diff error | task_id={task_id} | error={e}")
            return ""

    async def _run_code_review(
        self,
        task_id: str,
        execution_result: dict[str, Any],
    ) -> None:
        """Run independent code review using a fresh CC session with Sonnet."""
        ok, full = self.task_service.get_task(task_id)
        if not ok:
            logger.error(f"Cannot fetch task for code review | task_id={task_id}")
            return

        task = full["task"]
        policy = self._get_project_policy(task)
        effective_project_config = self._project_config_for_policy(policy)
        model_routing = policy.get("model_routing") if policy else None
        review_runner, review_runner_key, review_routing_reason = self._get_runner_adapter(
            task,
            model_routing=model_routing,
            stage="code-review",
        )
        review_model = self._runner_review_model(review_runner)
        review_cycle = task.get("review_cycle") or 0

        # Check max review cycles
        if review_cycle >= self.MAX_CODE_REVIEW_CYCLES:
            logger.warning(
                f"Max code review cycles reached | task_id={task_id} | cycles={review_cycle}"
            )
            await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status="escalated",
                changed_by="code-reviewer",
                reason=f"Max code review cycles ({self.MAX_CODE_REVIEW_CYCLES}) exceeded",
            )
            await self.notifier.on_task_escalated(
                task_id,
                f"Max code review cycles ({self.MAX_CODE_REVIEW_CYCLES}) exceeded",
                runtime=self._build_runtime_context(
                    task,
                    stage="code-review",
                    session_id=f"{task_id}-review",
                ),
            )
            return

        code_review_run_id = await self._create_execution_run(
            task_id=task_id,
            project_id=self._resolve_project_id(task),
            stage="code-review",
            status="reviewing",
            session_id=f"{task_id}-review",
            model=review_model,
            retry_index=review_cycle,
            metadata={
                "review_cycle": review_cycle,
                "runner_key": review_runner_key,
                "source_app": self._runner_source_app(review_runner, review_runner_key),
                "runner_routing_reason": review_routing_reason,
            },
        )

        # Get git diff
        git_diff = await self._get_git_diff(task_id)

        # Build reviewer prompt
        prompt = self._build_code_review_prompt(task, git_diff)

        # Spawn fresh CC session with Sonnet model (cost saving)
        review_config = ProjectConfig(
            project_path=effective_project_config.project_path,
            build_command=effective_project_config.build_command,
            isolation=effective_project_config.isolation,
            repository_url=effective_project_config.repository_url,
            force_model=review_model,
        )

        logger.info(
            f"Spawning code review runner session | task_id={task_id} | runner={review_runner_key} | model={review_model}"
        )

        review_result = await review_runner.spawn(
            task_id=f"{task_id}-review",
            prompt=prompt,
            config=review_config,
            timeout=300,  # 5 min timeout for review
            task=task,
            runtime_metadata={
                "source_app": self._resolve_source_app(task),
                "task_id": task_id,
                "execution_run_id": code_review_run_id,
                "session_id": f"{task_id}-review",
                "agent_id": f"{task_id}-review",
                "stage": "code-review",
            },
        )

        # Parse verdict from CC output
        verdict = review_result.parsed.get("code_review_verdict", "").upper()
        findings = review_result.parsed.get("code_review_findings", [])
        new_cycle = review_cycle + 1

        # Store code review result on task
        code_review_data: dict[str, Any] = {
            "verdict": verdict or "UNKNOWN",
            "findings": findings,
            "review_cycle": new_cycle,
            "model": review_model,
            "runner_key": review_runner_key,
            "runner_routing_reason": review_routing_reason,
            "duration_seconds": review_result.duration_seconds,
            "exit_code": review_result.exit_code,
        }
        if review_result.stderr:
            code_review_data["stderr_preview"] = review_result.stderr[:500]

        # Build quality gate score for the code review
        code_review_as_review = ArchitectReviewResult(
            verdict="approve" if verdict == "APPROVE" else "changes-requested",
            confidence=0.85 if verdict == "APPROVE" else 0.6,
            findings=findings if isinstance(findings, list) else [],
            summary=f"Code review: {verdict}",
            mode="independent-review",
            provider=review_model,
        )
        quality_gate = calculate_quality_gate_score(code_review_as_review, execution_result)
        code_review_data["quality_gate_score"] = quality_gate.to_dict()

        # Build review history entry (append-only)
        existing_history = task.get("review_history") or []
        if not isinstance(existing_history, list):
            existing_history = []

        code_review_action = ReviewAction(
            next_status="review" if verdict == "APPROVE" else "assigned",
            reason=f"Code review: {verdict}",
            changed_by="code-reviewer",
        )
        history_entry = build_review_history_entry(
            review=code_review_as_review,
            action=code_review_action,
            quality_gate=quality_gate,
            task=task,
            existing_history=existing_history,
        )
        updated_history = existing_history + [history_entry.to_dict()]

        # Build reviewed_by entry for code review stage
        review_entry = {
            "stage": "code-review",
            "agent": "code-reviewer",
            "model": review_model,
            "actor": "code-reviewer",
            "verdict": verdict or "UNKNOWN",
            "confidence": code_review_as_review.confidence,
            "mode": "independent-review",
        }
        existing_reviewed_by = task.get("reviewed_by") or []
        if not isinstance(existing_reviewed_by, list):
            existing_reviewed_by = []
        updated_reviewed_by = existing_reviewed_by + [review_entry]

        # Persist code_review data, review_history, reviewed_by, and review_cycle at top level
        await self.task_service.update_task(
            task_id=task_id,
            update_fields={
                "code_review": code_review_data,
                "review_history": updated_history,
                "reviewed_by": updated_reviewed_by,
                "review_cycle": new_cycle,
                "quality_score": quality_gate.compound_score,
            },
        )

        run_status = "completed" if verdict in {"APPROVE", "REQUEST_CHANGES"} else "failed"
        await self._update_execution_run(
            code_review_run_id,
            status=run_status,
            finished_at=datetime.now().isoformat(),
            duration_seconds=review_result.duration_seconds,
            model=review_model,
            result_summary=f"Code review: {verdict or 'UNKNOWN'}",
            error_summary=(review_result.stderr[:500] if review_result.stderr else None),
            metadata={
                "verdict": verdict or "UNKNOWN",
                "findings_count": len(findings) if isinstance(findings, list) else 0,
                "review_cycle": new_cycle,
                "runner_key": review_runner_key,
                "runner_routing_reason": review_routing_reason,
            },
        )

        logger.info(
            f"Code review quality gate | task_id={task_id} | "
            f"score={quality_gate.compound_score} | gate={quality_gate.gate_result} | "
            f"cycle={new_cycle}/{self.MAX_CODE_REVIEW_CYCLES}"
        )

        # Decide next action based on verdict
        if verdict == "APPROVE":
            await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status="review",
                changed_by="code-reviewer",
                reason="Code review approved",
            )
            await self.notifier.on_task_review_ready(
                task_id,
                code_review_data,
                runtime=self._build_runtime_context(
                    task,
                    stage="code-review",
                    execution_run_id=code_review_run_id,
                    session_id=f"{task_id}-review",
                ),
            )
            logger.info(f"Code review approved | task_id={task_id}")

        elif verdict == "REQUEST_CHANGES":
            # Check if we have review cycles left for auto-retry
            has_critical = any(
                f.get("severity") == "critical" for f in findings if isinstance(f, dict)
            )
            feedback = "; ".join(
                f.get("description", "") for f in findings[:5] if isinstance(f, dict)
            ) or "Changes requested by code reviewer"

            if has_critical and new_cycle >= self.MAX_CODE_REVIEW_CYCLES:
                # Critical findings + max cycles → escalate
                await self.lifecycle_service.execute_transition(
                    task_id=task_id,
                    new_status="escalated",
                    changed_by="code-reviewer",
                    reason=f"Critical findings after {new_cycle} review cycles: {feedback}",
                )
                await self.notifier.on_task_escalated(
                    task_id,
                    f"Critical findings after {new_cycle} review cycles",
                    runtime=self._build_runtime_context(
                        task,
                        stage="code-review",
                        execution_run_id=code_review_run_id,
                        session_id=f"{task_id}-review",
                    ),
                )
            else:
                # Auto-retry: send back to assigned with feedback
                await self.lifecycle_service.execute_transition(
                    task_id=task_id,
                    new_status="assigned",
                    changed_by="code-reviewer",
                    reason=feedback,
                )
                await self.notifier.on_code_review_changes_requested(
                    task_id,
                    code_review_data,
                    runtime=self._build_runtime_context(
                        task,
                        stage="code-review",
                        execution_run_id=code_review_run_id,
                        session_id=f"{task_id}-review",
                    ),
                )

            logger.info(
                f"Code review requested changes | task_id={task_id} | "
                f"cycle={new_cycle}/{self.MAX_CODE_REVIEW_CYCLES} | critical={has_critical}"
            )

        else:
            # Unknown verdict or CC failure → escalate
            logger.warning(f"Code review verdict unknown | task_id={task_id} | verdict={verdict}")
            await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status="escalated",
                changed_by="code-reviewer",
                reason=f"Code review returned unknown verdict: {verdict}",
            )
            await self.notifier.on_task_escalated(
                task_id,
                f"Code review returned unknown verdict: {verdict}",
                runtime=self._build_runtime_context(
                    task,
                    stage="code-review",
                    execution_run_id=code_review_run_id,
                    session_id=f"{task_id}-review",
                ),
            )
