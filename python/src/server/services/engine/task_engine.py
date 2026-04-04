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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

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
from .evaluator_templates import (
    build_contract_aware_evaluator,
    build_owner_review_summary,
    resolve_evaluator_template,
)
from .execution_health_monitor import (
    HEALTH_HEALTHY,
    ExecutionHealthMonitor,
)
from .health_monitor import HealthMonitor
from .learning_processor import LearningProcessor
from .notifier import EVENT_TASK_CONTRACT_GATE_BLOCKED, Notifier
from .prompt_builder import PromptBuilder
from .review_prompts import build_adversarial_code_review_prompt
from .run_workspace import RunWorkspaceContext, RunWorkspaceManager
from .runner_adapter import (
    DEFAULT_RUNNER_KEY,
    ClaudeCodeRunnerAdapter,
    ExecutionRunner,
    get_engine_default_runner_key,
)
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
from .auto_approval import evaluate_auto_approval
from .coordinator_service import CoordinatorService
from .lifecycle_hooks import LifecycleHooks
from .task_boundaries import task_has_boundary_rules, validate_task_boundaries
from .task_conflicts import predict_task_overlap
from .task_decomposer import TaskDecomposer
from .task_scheduler import score_and_sort_tasks

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
        self.default_runner_key = get_engine_default_runner_key()
        self.runner_adapters: dict[str, ExecutionRunner] = {}
        self._run_heartbeat_interval_seconds = 15.0
        self._run_missing_process_grace_seconds = max(float(self.poll_interval * 2), 120.0)
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

        # Coordinator mode services (C-P7)
        self.coordinator_service = CoordinatorService(
            task_service=self.task_service,
            lifecycle_service=self.lifecycle_service,
            notifier=self.notifier,
            execution_run_service=self.execution_run_service,
        )
        self.task_decomposer = TaskDecomposer()

        # Adaptive polling state (C-P5-04)
        self._adaptive_poll_intervals = (2, 5, 15, 30)  # seconds: active→recent→idle→deep_idle
        self._adaptive_idle_cycles: int = 0  # consecutive cycles with no work
        self._base_poll_interval: int = poll_interval

    @property
    def spawner(self) -> ExecutionRunner:
        """Backward-compatible default runner alias."""
        return self._spawner

    @spawner.setter
    def spawner(self, runner: ExecutionRunner) -> None:
        """Keep legacy `spawner` access while registering the default adapter."""
        self._spawner = runner
        runner_key = getattr(runner, "runner_key", None)
        if not isinstance(runner_key, str) or not runner_key.strip():
            runner_key = DEFAULT_RUNNER_KEY
        self.runner_adapters[runner_key] = runner

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
            now_iso = datetime.now().isoformat()
            ok, result = await self.execution_run_service.create_run(
                task_id=task_id,
                project_id=project_id,
                stage=stage,
                status=status,
                engine_id="task-engine",
                session_id=session_id,
                model=model,
                retry_index=retry_index,
                started_at=now_iso,
                heartbeat_at=now_iso,
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

    @staticmethod
    def _parse_iso_timestamp(raw_value: Any) -> datetime | None:
        """Parse a timestamp string into a timezone-aware datetime when possible."""
        if not isinstance(raw_value, str) or not raw_value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    def _execution_run_last_activity_at(self, run: dict[str, Any]) -> datetime | None:
        """Return the freshest known activity timestamp for an execution run."""
        for field_name in ("heartbeat_at", "updated_at", "started_at"):
            parsed = self._parse_iso_timestamp(run.get(field_name))
            if parsed is not None:
                return parsed
        return None

    def _execution_run_activity_age_seconds(self, run: dict[str, Any]) -> float | None:
        """Return seconds since the last known execution-run heartbeat or update."""
        activity_at = self._execution_run_last_activity_at(run)
        if activity_at is None:
            return None
        return (datetime.now(UTC) - activity_at).total_seconds()

    def _runner_get_process(self, runner: ExecutionRunner, task_id: str) -> asyncio.subprocess.Process | None:
        """Best-effort access to a runner's subprocess handle for a task."""
        get_process = getattr(runner, "get_process", None)
        if get_process is None:
            inner = getattr(runner, "_spawner", None)
            get_process = getattr(inner, "get_process", None) if inner else None
        if get_process is None:
            return None
        try:
            return get_process(task_id)
        except Exception:
            return None

    def _find_runner_process(
        self, task_id: str
    ) -> tuple[str | None, ExecutionRunner | None, asyncio.subprocess.Process | None]:
        """Find which registered runner currently owns a live subprocess for a task."""
        for runner_key, runner in self.runner_adapters.items():
            process = self._runner_get_process(runner, task_id)
            if process is not None:
                return runner_key, runner, process
        return None, None, None

    async def _kill_task_process(self, task_id: str) -> bool:
        """Best-effort kill across all registered runners for a task."""
        killed = False
        for runner in self.runner_adapters.values():
            try:
                runner_killed = await runner.kill(task_id)
                killed = runner_killed or killed
            except Exception:
                continue
        return killed

    async def _get_active_execute_run(self, task_id: str) -> dict[str, Any] | None:
        """Fetch the active execute-stage run for a task, if one exists."""
        ok, result = self.execution_run_service.list_runs(
            task_id=task_id,
            status="running",
            stage="execute",
            limit=1,
        )
        if not ok:
            return None
        runs = result.get("runs") or []
        return runs[0] if runs else None

    async def _touch_execution_run_heartbeat(self, run_id: str | None) -> None:
        """Persist a heartbeat for an in-flight execute run."""
        if not run_id:
            return
        await self._update_execution_run(run_id, heartbeat_at=datetime.now().isoformat())

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

    @staticmethod
    def _get_auto_approval_tier(policy: dict[str, Any] | None) -> int:
        """Read auto_approval_tier from engine policy review_policy (B-P4-03)."""
        if not policy:
            return 0
        review_policy = policy.get("review_policy") or {}
        tier = review_policy.get("auto_approval_tier", 0)
        try:
            return int(tier)
        except (TypeError, ValueError):
            return 0

    def _review_config_for_policy(self, policy: dict[str, Any] | None) -> ReviewConfig:
        """Return the effective architect review config for one project."""
        if not policy:
            return self.review_config

        review_policy = policy.get("review_policy") or {}
        if not isinstance(review_policy, dict):
            return self.review_config

        overrides: dict[str, Any] = {}

        review_mode = review_policy.get("review_mode")
        if isinstance(review_mode, str) and review_mode.strip():
            overrides["review_mode"] = review_mode

        provider = review_policy.get("provider")
        if isinstance(provider, str) and provider.strip():
            overrides["provider"] = provider

        model = review_policy.get("model")
        if isinstance(model, str) and model.strip():
            overrides["model"] = model

        for field_name in ("temperature", "confidence_approve_threshold", "confidence_retry_threshold"):
            value = review_policy.get(field_name)
            if isinstance(value, (int, float)):
                overrides[field_name] = float(value)

        for field_name in ("max_tokens", "timeout"):
            value = review_policy.get(field_name)
            if isinstance(value, int) and value > 0:
                overrides[field_name] = value

        for field_name in (
            "security_override_to_api",
            "api_fallback_to_self_review",
            "independent_review_enabled",
        ):
            value = review_policy.get(field_name)
            if isinstance(value, bool):
                overrides[field_name] = value

        if not overrides:
            return self.review_config

        return replace(self.review_config, **overrides)

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
                    await self._kill_task_process(task_id)

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

            _runner_key, _runner, process = self._find_runner_process(task_id)
            if process is None:
                active_run = await self._get_active_execute_run(task_id)
                if active_run is None:
                    # The execute-stage run is already terminal, so the task may be in post-execute review.
                    continue
                age_seconds = self._execution_run_activity_age_seconds(active_run)
                if age_seconds is None or age_seconds <= self._run_missing_process_grace_seconds:
                    continue
                logger.error(
                    "Tracked execute run lost its runner process without completing | "
                    f"task_id={task_id} | run_id={active_run.get('id')} | stale_for={int(age_seconds)}s"
                )
                exec_task.cancel()
                self._execution_tasks.pop(task_id, None)
                if self.global_tracker and self.project_id:
                    self.global_tracker.release(self.project_id)
                if self.agent_pool and self.project_id:
                    self.agent_pool.release(self.project_id)
                reason = (
                    "Runner process disappeared without completion "
                    f"(no heartbeat for {int(age_seconds)}s)"
                )
                await self._force_fail_orphaned_run(
                    task_id,
                    reason=reason,
                    run_id=active_run.get("id"),
                )
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

        await self._reconcile_stale_execute_runs()

    async def _reconcile_stale_execute_runs(self) -> None:
        """Fail execute-stage runs that are marked running but have no live runtime owner."""
        ok, result = self.execution_run_service.list_runs(
            project_id=self.project_id,
            status="running",
            stage="execute",
            limit=100,
        )
        if not ok:
            return

        for run in result.get("runs", []):
            task_id = run.get("task_id")
            run_id = run.get("id")
            if not isinstance(task_id, str) or not task_id:
                continue
            if not isinstance(run_id, str) or not run_id:
                continue
            if task_id in self._execution_tasks:
                continue

            _runner_key, _runner, process = self._find_runner_process(task_id)
            if process is not None and process.returncode is None:
                continue

            age_seconds = self._execution_run_activity_age_seconds(run)
            if age_seconds is None or age_seconds <= self._run_missing_process_grace_seconds:
                continue

            logger.error(
                "Stale execute run detected outside active watchdog map | "
                f"task_id={task_id} | run_id={run_id} | stale_for={int(age_seconds)}s"
            )
            await self._force_fail_orphaned_run(
                task_id,
                reason=f"Execute run lost runtime ownership (stale for {int(age_seconds)}s)",
                run_id=run_id,
            )

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
    # Coordinator parent advancement (C-P7-03)
    # ------------------------------------------------------------------

    async def _advance_coordinator_parents(self) -> None:
        """Find coordinator parents whose children are all terminal and advance them."""
        if not self.project_id:
            return

        try:
            # Find coordinator tasks in executing/on-hold states
            for status in ("executing", "on-hold"):
                ok, result = self.task_service.list_tasks(
                    project_id=self.project_id,
                    status=status,
                    include_closed=False,
                    include_archived=False,
                    exclude_large_fields=True,
                )
                if not ok:
                    continue

                for task in result.get("tasks", []):
                    if task.get("decomposition_mode") != "coordinator":
                        continue
                    # Skip tasks we're actively executing
                    if task["id"] in self._execution_tasks:
                        continue

                    await self.coordinator_service.advance_parent_if_ready(task["id"])

        except Exception as e:
            logger.error(f"Coordinator parent advancement failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Stalled run detection + auto-recovery (D-P2-02)
    # ------------------------------------------------------------------

    async def _detect_stalled_runs(self) -> None:
        """Detect execution runs that have not made progress within policy thresholds.

        Uses heartbeat_at from execution runs to determine staleness.
        Configurable via engine_policy.capacity_policy:
        - stalled_warning_seconds (default 300): emit warning
        - stalled_kill_seconds (default 600): kill run and trigger recovery

        Recovery ladder: kill → retry with escalated model → alert operator
        """
        if not self.project_id:
            return

        # Read thresholds from engine policy
        warning_threshold = 300
        kill_threshold = 600
        policy = self.engine_policy_service.get_active_policy(self.project_id)
        if policy:
            capacity = policy.get("capacity_policy") or {}
            warning_threshold = capacity.get("stalled_warning_seconds", 300)
            kill_threshold = capacity.get("stalled_kill_seconds", 600)

        try:
            ok, result = self.execution_run_service.list_runs(
                project_id=self.project_id,
                status="running",
                limit=50,
            )
            if not ok:
                return

            for run in result.get("runs", []):
                run_id = run.get("id")
                task_id = run.get("task_id")
                if not run_id or not task_id:
                    continue

                age_seconds = self._execution_run_activity_age_seconds(run)
                if age_seconds is None:
                    continue

                if age_seconds >= kill_threshold:
                    logger.error(
                        f"Stalled run killed | task_id={task_id} | run_id={run_id} | "
                        f"stalled_for={int(age_seconds)}s | threshold={kill_threshold}s"
                    )
                    # Kill the process
                    await self._kill_task_process(task_id)
                    # Fail the run
                    await self._update_execution_run(
                        run_id,
                        status="failed",
                        finished_at=datetime.now().isoformat(),
                        error_summary=f"Run stalled for {int(age_seconds)}s (threshold {kill_threshold}s)",
                    )
                    # Apply retry-or-escalate policy
                    ok_task, task_result = self.task_service.get_task(task_id)
                    task = task_result.get("task", {"id": task_id}) if ok_task else {"id": task_id}
                    await self._handle_task_failure(
                        task_id, task,
                        reason=f"Stalled run auto-killed after {int(age_seconds)}s",
                    )
                    await self.notifier.emit(
                        "health.stalled_run_killed",
                        task_id=task_id,
                        data={
                            "run_id": run_id,
                            "task_id": task_id,
                            "project_id": self.project_id,
                            "stalled_seconds": int(age_seconds),
                            "threshold": kill_threshold,
                        },
                        is_critical=True,
                    )

                elif age_seconds >= warning_threshold:
                    logger.warning(
                        f"Stalled run warning | task_id={task_id} | run_id={run_id} | "
                        f"stalled_for={int(age_seconds)}s | threshold={warning_threshold}s"
                    )
                    await self.notifier.emit(
                        "health.stalled_run_warning",
                        task_id=task_id,
                        data={
                            "run_id": run_id,
                            "task_id": task_id,
                            "project_id": self.project_id,
                            "stalled_seconds": int(age_seconds),
                            "warning_threshold": warning_threshold,
                            "kill_threshold": kill_threshold,
                        },
                    )

        except Exception as e:
            logger.error(f"Stalled run detection failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Lifecycle reconciliation watchdog (D-P2-04)
    # ------------------------------------------------------------------

    async def _reconcile_task_lifecycle(self) -> None:
        """Detect tasks stuck in executing/review states with no active run.

        Finds tasks where:
        - status is 'executing', 'architect-review', or 'code-review'
        - latest execution run is terminal (completed/failed/cancelled)
        - no active execution run exists for the task

        For each, advances the task to the correct next state.
        """
        if not self.project_id:
            return

        active_task_statuses = {"executing", "architect-review", "code-review"}
        terminal_run_statuses = {"completed", "failed", "cancelled"}

        try:
            # Fetch tasks in active execution states
            for status in active_task_statuses:
                ok, result = self.task_service.list_tasks(
                    project_id=self.project_id,
                    status=status,
                    include_closed=False,
                    include_archived=False,
                    exclude_large_fields=True,
                )
                if not ok:
                    continue

                tasks = result.get("tasks", [])
                for task in tasks:
                    task_id = task["id"]

                    # Skip tasks we're actively executing
                    if task_id in self._execution_tasks:
                        continue

                    # Check if any run is still active
                    ok_runs, runs_result = self.execution_run_service.list_runs(
                        task_id=task_id,
                        limit=5,
                    )
                    if not ok_runs:
                        continue

                    runs = runs_result.get("runs", [])
                    if not runs:
                        # No runs at all — task stuck without run, fail it
                        logger.warning(
                            f"Lifecycle watchdog: task in '{status}' with no runs | task_id={task_id}"
                        )
                        await self.lifecycle_service.execute_transition(
                            task_id=task_id,
                            new_status="failed",
                            changed_by="lifecycle-watchdog",
                            reason=f"Task stuck in '{status}' with no execution runs",
                        )
                        await self.notifier.emit(
                            "task.lifecycle_reconciled",
                            task_id=task_id,
                            data={
                                "previous_status": status,
                                "new_status": "failed",
                                "reason": "no_runs",
                                "project_id": self.project_id,
                            },
                        )
                        continue

                    # Check if any run is still active (running/queued/reviewing)
                    has_active_run = any(
                        r.get("status") not in terminal_run_statuses
                        for r in runs
                    )
                    if has_active_run:
                        continue

                    # All runs are terminal — determine next task state from latest run
                    latest_run = runs[0]  # list_runs returns most recent first
                    latest_status = latest_run.get("status")

                    if latest_status == "completed":
                        # Run completed but task didn't advance — advance it now
                        next_status = self._next_status_after_completed_run(status)
                        logger.warning(
                            f"Lifecycle watchdog: advancing stuck task | "
                            f"task_id={task_id} | {status} → {next_status} | "
                            f"run_id={latest_run.get('id')}"
                        )
                        await self.lifecycle_service.execute_transition(
                            task_id=task_id,
                            new_status=next_status,
                            changed_by="lifecycle-watchdog",
                            reason=f"Run completed but task stuck in '{status}'",
                        )
                    else:
                        # Latest run failed/cancelled — mark task failed
                        logger.warning(
                            f"Lifecycle watchdog: failing stuck task | "
                            f"task_id={task_id} | {status} → failed | "
                            f"latest_run_status={latest_status}"
                        )
                        await self.lifecycle_service.execute_transition(
                            task_id=task_id,
                            new_status="failed",
                            changed_by="lifecycle-watchdog",
                            reason=f"All runs terminal (latest: {latest_status}), task stuck in '{status}'",
                        )

                    await self.notifier.emit(
                        "task.lifecycle_reconciled",
                        task_id=task_id,
                        data={
                            "previous_status": status,
                            "new_status": next_status if latest_status == "completed" else "failed",
                            "latest_run_id": latest_run.get("id"),
                            "latest_run_status": latest_status,
                            "project_id": self.project_id,
                        },
                    )

        except Exception as e:
            logger.error(f"Lifecycle reconciliation watchdog failed: {e}", exc_info=True)

    @staticmethod
    def _next_status_after_completed_run(current_task_status: str) -> str:
        """Determine what status a task should advance to after its run completes."""
        advancement = {
            "executing": "architect-review",
            "architect-review": "code-review",
            "code-review": "review",
        }
        return advancement.get(current_task_status, "review")

    # ------------------------------------------------------------------
    # Dependency auto-unblock reconciliation (C-P5-03)
    # ------------------------------------------------------------------

    async def _reconcile_blocked_dependencies(self) -> None:
        """Auto-clear satisfied blocked_by references and detect invalid blockers.

        Scans tasks with status in blocked-eligible states that have blocked_by
        entries pointing to terminal tasks (done, cancelled, failed). Clears those
        entries so the task can proceed.

        Called periodically from _poll_cycle (every 3 cycles, same cadence as
        _assign_approved_tasks).
        """
        if not self.project_id:
            return

        terminal_statuses = {"done", "cancelled", "failed"}

        try:
            # Fetch tasks that have blocked_by set and are in non-terminal states
            ok, result = self.task_service.list_tasks(
                project_id=self.project_id,
                include_closed=False,
                include_archived=False,
            )
            if not ok:
                return

            all_tasks = result.get("tasks", [])
            blocked_tasks = [
                t for t in all_tasks
                if (t.get("blocked_by") or []) and t.get("status") not in terminal_statuses
            ]

            if not blocked_tasks:
                return

            # Collect all unique blocker IDs
            blocker_ids: set[str] = set()
            for task in blocked_tasks:
                blocker_ids.update(task.get("blocked_by") or [])

            if not blocker_ids:
                return

            # Batch-fetch blocker statuses
            blocker_statuses: dict[str, str | None] = {}
            try:
                response = (
                    self.task_service.supabase_client.table("archon_tasks")
                    .select("id, status")
                    .in_("id", list(blocker_ids))
                    .execute()
                )
                found_ids: set[str] = set()
                for row in response.data or []:
                    blocker_statuses[row["id"]] = row["status"]
                    found_ids.add(row["id"])

                # Mark non-existent blocker IDs
                for bid in blocker_ids:
                    if bid not in found_ids:
                        blocker_statuses[bid] = None  # non-existent
            except Exception as e:
                logger.error(f"Auto-unblock: failed to fetch blocker statuses: {e}", exc_info=True)
                return

            # Process each blocked task
            for task in blocked_tasks:
                task_id = task["id"]
                blocked_by = task.get("blocked_by") or []
                resolved: list[str] = []
                invalid: list[str] = []
                still_blocking: list[str] = []

                for bid in blocked_by:
                    status = blocker_statuses.get(bid)
                    if status is None:
                        invalid.append(bid)
                        resolved.append(bid)
                    elif status in terminal_statuses:
                        resolved.append(bid)
                    else:
                        still_blocking.append(bid)

                if not resolved:
                    continue

                # Compute remaining blockers
                remaining = still_blocking

                # Update the task to remove resolved blockers
                try:
                    update_data: dict[str, Any] = {
                        "blocked_by": remaining if remaining else [],
                    }
                    self.task_service.supabase_client.table("archon_tasks").update(
                        update_data
                    ).eq("id", task_id).execute()

                    logger.info(
                        f"Auto-unblock: cleared {len(resolved)} blocker(s) | "
                        f"task_id={task_id} | "
                        f"cleared={resolved} | "
                        f"remaining={remaining}"
                    )

                    if invalid:
                        logger.warning(
                            f"Auto-unblock: invalid blocker(s) detected | "
                            f"task_id={task_id} | "
                            f"invalid_ids={invalid}"
                        )

                    # Emit event via notifier
                    await self.notifier.emit(
                        "task.auto_unblocked",
                        task_id=task_id,
                        data={
                            "task_id": task_id,
                            "project_id": self.project_id,
                            "cleared_blockers": resolved,
                            "invalid_blockers": invalid,
                            "remaining_blockers": remaining,
                        },
                    )
                except Exception as e:
                    logger.error(
                        f"Auto-unblock: failed to update task | "
                        f"task_id={task_id} | error={e}",
                        exc_info=True,
                    )

        except Exception as e:
            logger.error(f"Auto-unblock reconciliation failed: {e}", exc_info=True)

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
            # Check feedback policy — escalate rather than retry if no actionable feedback exists
            can_retry, policy_reason = await self._validate_retry_feedback_policy(task_id, retry_count)
            if not can_retry:
                await self.lifecycle_service.execute_transition(
                    task_id=task_id,
                    new_status="failed",
                    changed_by="task-engine",
                    reason=policy_reason,
                )
                if self._escalation_enabled:
                    await self.lifecycle_service.execute_transition(
                        task_id=task_id,
                        new_status="escalated",
                        changed_by="task-engine",
                        reason=policy_reason,
                    )
                    await self.notifier.on_task_escalated(task_id, policy_reason, runtime=runtime)
                    logger.warning(
                        f"Task escalated — retry feedback policy blocked retry | task_id={task_id} | "
                        f"reason={policy_reason}"
                    )
                else:
                    await self.notifier.on_task_failed(task_id, policy_reason, runtime=runtime)
                return

            await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status="failed",
                changed_by="task-engine",
                reason=reason,
            )
            await self.notifier.on_task_failed(task_id, reason, runtime=runtime)

            # Emit recovery event for observability (non-fatal)
            try:
                retry_strategy = "model_escalation"
                if self.project_id:
                    policy = self.engine_policy_service.get_active_policy(self.project_id)
                    if policy:
                        capacity = policy.get("capacity_policy") or {}
                        retry_strategy = capacity.get("retry_strategy") or "model_escalation"

                await self.notifier.emit(
                    "execution.recovery_attempted",
                    task_id=task_id,
                    data={
                        "task_id": task_id,
                        "project_id": task.get("project_id"),
                        "strategy": retry_strategy,
                        "attempt_number": retry_count + 1,
                        "max_retries": max_retries,
                        "original_error": reason[:300],
                        "outcome": "retry_scheduled",
                    },
                )
            except Exception:
                pass  # recovery event is advisory, must not block retry

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
    # Review feedback artifacts
    # ------------------------------------------------------------------

    @staticmethod
    def _map_findings_to_feedback(raw_findings: list[Any]) -> list[dict[str, Any]]:
        """Convert raw review findings to structured feedback records.

        Severity-to-score mapping: critical=1.0, high=3.0, medium=6.0, low=8.0.
        Explicit score fields are preserved (clamped to 0–10).
        Non-dict entries are skipped.
        """
        _severity_score = {"critical": 1.0, "high": 3.0, "medium": 6.0, "low": 8.0}
        result = []
        for f in raw_findings:
            if not isinstance(f, dict):
                continue
            criterion = f.get("type") or f.get("category") or "general"
            if "score" in f:
                score = float(f["score"])
                score = max(0.0, min(10.0, score))
            else:
                severity = str(f.get("severity", "medium")).lower()
                score = _severity_score.get(severity, 6.0)
            result.append({
                "criterion": criterion,
                "score": score,
                "passed": False,
                "details": f.get("description", ""),
                "evidence": f.get("evidence", ""),
            })
        return result

    async def _write_review_feedback(
        self,
        task_id: str,
        run_id: str | None,
        task: dict[str, Any],
        findings: list[Any],
        overall_score: float | None,
        verdict: str,
        suggested_retry_direction: str | None,
        reviewer_identity: str,
        stage: str,
    ) -> None:
        """Persist a review feedback artifact to archon_review_feedback.

        Extracts contract_revision from task.current_contract.version if present.
        DB errors are non-fatal — the review pipeline must continue regardless.
        """
        contract_revision: int | None = None
        current_contract = task.get("current_contract")
        if isinstance(current_contract, dict):
            contract_revision = current_contract.get("version")

        mapped_findings = self._map_findings_to_feedback(findings)

        row: dict[str, Any] = {
            "task_id": task_id,
            "run_id": run_id,
            "contract_revision": contract_revision,
            "findings": mapped_findings,
            "overall_score": overall_score,
            "verdict": verdict,
            "suggested_retry_direction": suggested_retry_direction,
            "reviewer_identity": reviewer_identity,
        }

        try:
            self.task_service.supabase_client.table("archon_review_feedback").insert(row).execute()
        except Exception as e:
            logger.warning(f"Review feedback DB write failed (non-fatal) | task_id={task_id} | error={e}")

        try:
            await self.notifier.on_review_feedback_written(task_id, {
                "verdict": verdict,
                "reviewer_identity": reviewer_identity,
                "overall_score": overall_score,
                "stage": stage,
                "contract_revision": contract_revision,
            })
        except Exception as e:
            logger.warning(f"Review feedback event emit failed (non-fatal) | task_id={task_id} | error={e}")

    async def _fetch_latest_review_feedback(self, task_id: str) -> dict[str, Any] | None:
        """Fetch the most recent review feedback record for a task. Returns None on any error."""
        try:
            result = (
                self.task_service.supabase_client
                .table("archon_review_feedback")
                .select("*")
                .eq("task_id", task_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            return rows[0] if rows else None
        except Exception as e:
            logger.warning(f"Fetch review feedback failed (non-fatal) | task_id={task_id} | error={e}")
            return None

    async def _validate_retry_feedback_policy(
        self, task_id: str, retry_count: int
    ) -> tuple[bool, str]:
        """Check whether a retry is allowed based on the review feedback policy.

        First failure (retry_count == 0) always proceeds.
        Subsequent retries require feedback with at least findings or a suggested_retry_direction.
        """
        if retry_count == 0:
            return True, ""

        feedback = await self._fetch_latest_review_feedback(task_id)
        if feedback is None:
            return False, f"No review feedback found for task {task_id} on attempt {retry_count + 1}"

        has_findings = bool(feedback.get("findings"))
        has_direction = bool((feedback.get("suggested_retry_direction") or "").strip())
        if not has_findings and not has_direction:
            return False, f"Review feedback for task {task_id} is ambiguous — no findings or retry direction"

        return True, ""

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while self._running:
            try:
                self._reap_completed()
                had_work_before = bool(self._execution_tasks)
                await self._poll_cycle()
                has_work_after = bool(self._execution_tasks)

                # Adaptive poll interval (C-P5-04)
                poll_mode = self._get_poll_mode()
                if poll_mode == "adaptive":
                    sleep_seconds = self._compute_adaptive_interval(had_work_before, has_work_after)
                else:
                    sleep_seconds = self.poll_interval
            except Exception as e:
                logger.error(f"Poll cycle error: {e}", exc_info=True)
                sleep_seconds = self.poll_interval
            await asyncio.sleep(sleep_seconds)

    def _get_poll_mode(self) -> str:
        """Read poll_mode from engine policy capacity_policy."""
        if not self.project_id:
            return "fixed"
        policy = self.engine_policy_service.get_active_policy(self.project_id)
        if policy:
            capacity = policy.get("capacity_policy") or {}
            return capacity.get("poll_mode", "fixed")
        return "fixed"

    def _compute_adaptive_interval(self, had_work_before: bool, has_work_after: bool) -> float:
        """Compute adaptive poll interval based on recent activity.

        Tiers:
        - Active (just spawned/running tasks): 2s
        - Recent activity (just completed): 5s
        - Idle (3+ cycles no work): 15s
        - Deep idle (10+ cycles no work): 30s
        """
        if has_work_after:
            self._adaptive_idle_cycles = 0
            return self._adaptive_poll_intervals[0]  # 2s — active

        if had_work_before and not has_work_after:
            self._adaptive_idle_cycles = 0
            return self._adaptive_poll_intervals[1]  # 5s — just completed

        self._adaptive_idle_cycles += 1

        if self._adaptive_idle_cycles >= 10:
            return self._adaptive_poll_intervals[3]  # 30s — deep idle
        if self._adaptive_idle_cycles >= 3:
            return self._adaptive_poll_intervals[2]  # 15s — idle

        return self._adaptive_poll_intervals[1]  # 5s — recent

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
        try:
            await self._reconcile_stale_execute_runs()
        except Exception as exc:
            logger.warning(f"Stale execute run reconcile failed during poll cycle: {exc}")

        # Lifecycle reconciliation every 3 cycles (D-P2-04)
        if self._poll_cycle_count % 3 == 0:
            try:
                await self._reconcile_task_lifecycle()
            except Exception as exc:
                logger.warning(f"Lifecycle reconciliation failed during poll cycle: {exc}")

        if not self._has_capacity():
            return

        self._poll_cycle_count += 1

        if self.project_id is None:
            logger.warning("No project_id set — polling ALL projects (may cause duplicate pickup)")

        # Auto-assign approved tasks every 3 cycles to avoid extra DB queries each poll
        if self._poll_cycle_count % 3 == 1:
            await self._assign_approved_tasks()

        # Auto-unblock tasks with satisfied dependencies every 3 cycles (C-P5-03)
        if self._poll_cycle_count % 3 == 2:
            try:
                await self._reconcile_blocked_dependencies()
            except Exception as exc:
                logger.warning(f"Dependency auto-unblock failed during poll cycle: {exc}")

        # Stalled run detection every 6 cycles (D-P2-02)
        if self._poll_cycle_count % 6 == 3:
            try:
                await self._detect_stalled_runs()
            except Exception as exc:
                logger.warning(f"Stalled run detection failed during poll cycle: {exc}")

        # Coordinator parent advancement every 3 cycles (C-P7-03)
        if self._poll_cycle_count % 3 == 1:
            try:
                await self._advance_coordinator_parents()
            except Exception as exc:
                logger.warning(f"Coordinator parent advancement failed during poll cycle: {exc}")

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

        # Determine scheduling mode from engine policy
        scheduling_mode = "fifo"
        all_project_tasks: list[dict[str, Any]] | None = None
        if self.project_id:
            policy = self.engine_policy_service.get_active_policy(self.project_id)
            if policy:
                capacity = policy.get("capacity_policy") or {}
                scheduling_mode = capacity.get("scheduling_mode", "fifo")

        if scheduling_mode == "priority_weighted" and self.project_id:
            # Fetch all non-terminal tasks for blocking_count computation
            ok, all_result = self.task_service.list_tasks(
                project_id=self.project_id,
                include_closed=False,
                include_archived=False,
            )
            if ok:
                all_project_tasks = all_result.get("tasks", [])

        tasks = score_and_sort_tasks(tasks, all_project_tasks, mode=scheduling_mode)

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
            elif res.get("contract_gate"):
                contract_id = res.get("contract_id", "")
                contract_status = res.get("contract_status", "unknown")
                blocked_transition = res.get("blocked_transition", "approved → assigned")
                logger.warning(
                    "Contract gate blocked auto-assign | task_id=%s | contract_id=%s | "
                    "contract_status=%s | blocked_transition=%s",
                    task_id,
                    contract_id,
                    contract_status,
                    blocked_transition,
                )
                await self.notifier.emit(
                    EVENT_TASK_CONTRACT_GATE_BLOCKED,
                    task_id=task_id,
                    data={
                        "task_id": task_id,
                        "contract_id": contract_id,
                        "contract_status": contract_status,
                        "blocked_transition": blocked_transition,
                        "reason": res.get("error", ""),
                    },
                )
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
                await self._kill_task_process(task_id)
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
        except Exception as exc:
            logger.error(
                "Task execution crashed before completion | task_id=%s | error=%s",
                task_id,
                exc,
                exc_info=True,
            )
            try:
                await self._kill_task_process(task_id)
            except Exception:
                pass

            crash_reason = str(exc)[:500] or "Runner execution crashed unexpectedly"
            ok, result = self.execution_run_service.list_runs(
                task_id=task_id,
                status="running",
                stage="execute",
                limit=1,
            )
            if ok and result.get("runs"):
                await self._update_execution_run(
                    result["runs"][0]["id"],
                    status="failed",
                    finished_at=datetime.now().isoformat(),
                    error_summary=crash_reason,
                )

            await self.task_service.update_task(
                task_id=task_id,
                update_fields={
                    "execution_result": {
                        "exit_code": -1,
                        "crashed": True,
                        "stderr_preview": crash_reason,
                    }
                },
            )
            await self._handle_task_failure(task_id, task, crash_reason)
            return CCExecutionResult(
                success=False,
                stdout="",
                stderr=crash_reason,
                exit_code=-1,
                duration_seconds=0,
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
            self._stage_contract_negotiation,
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
            self._stage_pre_validation,
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

    async def _stage_pre_validation(self, state: TaskExecutionState) -> TaskExecutionState:
        """Pre-execution scope validation gate (B-P4-02).

        Validates task readiness before investing in policy loading and prompt
        building. Catches issues that would otherwise waste an entire execution run.

        Checks:
        1. Contract lock gate: if task has a contract, it must be locked
        2. allowed_paths validity: paths must be parseable globs
        3. Dependencies: final re-check that blocked_by is clear
        """
        if state.full_task is None:
            return state

        task = state.full_task
        task_id = state.task_id
        issues: list[str] = []

        # 1. Contract lock gate
        current_contract = task.get("current_contract")
        if isinstance(current_contract, dict):
            contract_status = current_contract.get("negotiation_status", "")
            if contract_status and contract_status != "locked":
                issues.append(f"Contract not locked (status={contract_status})")

        # 2. allowed_paths validity
        allowed_paths = task.get("allowed_paths") or []
        if allowed_paths:
            for path in allowed_paths:
                if not isinstance(path, str) or not path.strip():
                    issues.append(f"Invalid allowed_path entry: {path!r}")
                    break

        # 3. Dependencies final check
        blocked_by = task.get("blocked_by") or []
        if blocked_by:
            still_blocked = self._recheck_blocked_by(task_id, blocked_by)
            if still_blocked:
                issues.append(f"Unresolved blockers: {still_blocked}")

        if issues:
            reason = f"Pre-validation failed: {'; '.join(issues)}"
            logger.warning(f"Pre-validation gate | task_id={task_id} | issues={issues}")
            await self.notifier.emit(
                "engine.pre_validation_failed",
                task_id=task_id,
                data={
                    "task_id": task_id,
                    "issues": issues,
                    "project_id": task.get("project_id"),
                },
            )
            state.final_result = CCExecutionResult(
                success=False,
                stdout="",
                stderr=reason,
                exit_code=-3,
                duration_seconds=0,
            )
            return state

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
        if os.environ.get("LEANKIT_ENGINE_DISABLE_CLAUDE_CODE", "").lower() in ("1", "true", "yes"):
            state.model_routing = state.model_routing or {}
            state.model_routing["disable_claude_code"] = True

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

    async def _stage_contract_negotiation(self, state: TaskExecutionState) -> TaskExecutionState:
        """Adversarially enrich the draft contract before execution begins.

        Reads the current draft contract from archon_task_contracts, calls
        enrich_contract_adversarially() to tighten thresholds and add missing
        edge cases, then persists the enriched criteria back with
        negotiation_status='negotiated'. Contracts already negotiated or locked
        are skipped (idempotent on retry). After enrichment, locks the contract.
        """
        if state.full_task is None or state.final_result is not None:
            return state

        contract_id = state.full_task.get("current_contract_id")
        if not contract_id:
            # Not contract-managed — skip silently
            return state

        ok, result = self.task_service.get_contract(contract_id)
        if not ok:
            logger.warning(
                f"Could not fetch contract for negotiation | task_id={state.task_id} | error={result.get('error')}"
            )
            return state

        contract = result["contract"]
        neg_status = contract.get("negotiation_status", "draft")
        if neg_status in ("negotiated", "locked"):
            logger.info(
                f"Contract already {neg_status}, skipping negotiation | task_id={state.task_id}"
            )
            return state

        # Build proposed criteria from DB contract acceptance_criteria
        db_criteria: list[dict[str, Any]] = contract.get("acceptance_criteria") or []
        proposed_criteria = [
            {
                "criterion": c.get("description") or c.get("name") or "",
                "threshold": c.get("threshold") or "",
                "category": c.get("category") or "functional",
            }
            for c in db_criteria
            if c.get("description") or c.get("name")
        ]

        from .review_prompts import enrich_contract_adversarially
        enriched = enrich_contract_adversarially(state.full_task, proposed_criteria)

        # Map enriched criteria back to DB format
        db_enriched: list[dict[str, Any]] = [
            {
                "name": c.get("criterion", "")[:100],
                "description": c.get("criterion", ""),
                "threshold": c.get("threshold") or "",
                "category": c.get("category", "functional"),
                "added_by": c.get("added_by"),
            }
            for c in enriched
        ]

        # Update contract with enriched criteria
        update_ok, _ = self.task_service.update_contract(
            contract_id=contract_id,
            update_fields={
                "acceptance_criteria": db_enriched,
                "negotiation_status": "negotiated",
                "source_stage": "pre-execute",
                "negotiated_by": "task-engine",
            },
        )

        if update_ok:
            added = len(enriched) - len(proposed_criteria)
            logger.info(
                f"Contract negotiated | task_id={state.task_id} | "
                f"criteria_before={len(proposed_criteria)} | after={len(enriched)} | added={added}"
            )
            # Lock the contract immediately after negotiation
            lock_ok, _ = self.task_service.lock_contract(contract_id, locked_by="task-engine")
            if lock_ok:
                logger.info(f"Contract locked | task_id={state.task_id} | contract_id={contract_id}")
            else:
                logger.warning(f"Contract lock failed | task_id={state.task_id} | contract_id={contract_id}")
        else:
            logger.warning(f"Contract negotiation update failed | task_id={state.task_id}")

        return state

    async def _stage_guidance_injection(self, state: TaskExecutionState) -> TaskExecutionState:
        """Build the unified prompt and record prompt injection statistics.

        For retry attempts (retry_index > 0), fetches the latest structured
        review feedback and passes it to the prompt builder so retries get
        targeted context instead of raw execution_result dumps.
        """
        if state.full_task is None:
            return state

        # Fetch structured review feedback for retry runs (B-P4-01)
        review_feedback: dict[str, Any] | None = None
        retry_count = state.full_task.get("retry_count") or 0
        if retry_count > 0:
            review_feedback = await self._fetch_latest_review_feedback(state.task_id)
            if review_feedback:
                logger.info(
                    f"Retry context injected | task_id={state.task_id} | "
                    f"retry={retry_count} | "
                    f"verdict={review_feedback.get('verdict')} | "
                    f"findings_count={len(review_feedback.get('findings') or [])}"
                )
                # Store retry_context summary in run metadata for auditability
                if state.run_metadata is None:
                    state.run_metadata = {}
                state.run_metadata["retry_context"] = {
                    "source_run_id": review_feedback.get("run_id"),
                    "verdict": review_feedback.get("verdict"),
                    "reviewer_identity": review_feedback.get("reviewer_identity"),
                    "findings_count": len(review_feedback.get("findings") or []),
                    "failed_criteria": [
                        f.get("criterion") for f in (review_feedback.get("findings") or [])
                        if isinstance(f, dict) and not f.get("passed", True)
                    ],
                    "suggested_direction": review_feedback.get("suggested_retry_direction"),
                    "contract_revision": review_feedback.get("contract_revision"),
                }
            else:
                logger.warning(
                    f"No review feedback for retry | task_id={state.task_id} | retry={retry_count}"
                )

        # Load formal contract criteria from archon_task_contracts and inject
        # into task dict so the execute-stage prompt builder can reference it.
        # The mirror field (architect_review.locked_contract) is preserved as
        # a fallback.  (H4-R1: execute path must be formal-contract-first.)
        task_for_prompt = state.full_task
        formal_criteria = self._load_formal_contract_criteria(state.full_task)
        if formal_criteria:
            task_for_prompt = {**state.full_task, "_formal_contract_criteria": formal_criteria}

        state.prompt, state.injection_stats = await self.prompt_builder.build(
            task=task_for_prompt,
            build_command=self.project_config.build_command,
            review_feedback=review_feedback,
        )
        logger.info(
            f"Injection stats | task_id={state.task_id} | "
            f"learnings={state.injection_stats['learnings']} | "
            f"patterns={state.injection_stats['patterns']} | "
            f"kb_chunks={state.injection_stats['kb_chunks']} | "
            f"tokens={state.injection_stats['tokens']} | "
            f"guidance_hash={state.injection_stats.get('guidance_pack_hash', '')}"
        )

        # Guidance pack token budget check
        if state.policy:
            capacity = state.policy.get("capacity_policy") or {}
            budget = capacity.get("guidance_pack_budget_tokens")
            if isinstance(budget, int) and budget > 0:
                pack_tokens = state.injection_stats.get("tokens", 0)
                if pack_tokens > budget:
                    logger.warning(
                        f"Guidance pack exceeds token budget | task_id={state.task_id} | "
                        f"pack_tokens={pack_tokens} | budget={budget}"
                    )
                    await self.notifier.emit(
                        "health.guidance_pack_over_budget",
                        task_id=state.task_id,
                        data={
                            "task_id": state.task_id,
                            "pack_tokens": pack_tokens,
                            "budget_tokens": budget,
                            "project_id": state.project_id,
                        },
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

        # Lifecycle hook: capture run start context
        start_ctx = LifecycleHooks.capture_run_start(
            task=state.full_task,
            model=state.selected_model,
            token_profile=state.token_profile_name,
            contract=state.full_task.get("current_contract"),
            runner_key=state.runner_key,
        )
        if state.run_metadata is None:
            state.run_metadata = {}
        state.run_metadata.setdefault("lifecycle", {})["start"] = start_ctx

        state.exec_health_monitor = ExecutionHealthMonitor()

        # Apply per-profile tool budget from engine policy (C-P6-02)
        if state.token_profile_name and state.policy:
            capacity = (state.policy.get("capacity_policy") or {})
            tool_budgets = capacity.get("tool_call_budget") or {}
            profile_budget = tool_budgets.get(state.token_profile_name)
            if isinstance(profile_budget, int) and profile_budget > 0:
                state.exec_health_monitor.set_task_total_limit(state.task_id, profile_budget)

        last_heartbeat_monotonic = 0.0

        async def _on_stream(tid: str, stream_event: dict[str, Any]) -> None:
            nonlocal last_heartbeat_monotonic
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
            now_monotonic = asyncio.get_running_loop().time()
            if (
                state.execute_run_id
                and (now_monotonic - last_heartbeat_monotonic) >= self._run_heartbeat_interval_seconds
            ):
                last_heartbeat_monotonic = now_monotonic
                await self._touch_execution_run_heartbeat(state.execute_run_id)
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
            # Store token breakdown in run metadata for analytics
            breakdown = state.injection_stats.get("prompt_token_breakdown")
            if breakdown:
                state.run_metadata["prompt_token_breakdown"] = breakdown
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
        # Preserve any metadata set by earlier stages (e.g. retry_context from B-P4-01)
        prior_metadata = state.run_metadata or {}
        state.run_metadata = {
            **prior_metadata,
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

        # Lifecycle hook: capture run stop context
        stop_metrics = {}
        if state.exec_health_monitor is not None:
            snapshot = state.exec_health_monitor.get_metrics_snapshot(state.task_id)
            stop_metrics["total_tool_calls"] = snapshot.get("total_count", 0)
        stop_ctx = LifecycleHooks.capture_run_stop(
            result=state.runner_result.parsed if state.runner_result else None,
            runner_result=state.runner_result,
            metrics=stop_metrics,
        )
        state.run_metadata.setdefault("lifecycle", {})["stop"] = stop_ctx

        # Record tool call metrics in run metadata (C-P6-02)
        if state.exec_health_monitor is not None:
            health_snapshot = state.exec_health_monitor.get_metrics_snapshot(state.task_id)
            state.run_metadata["total_tool_calls"] = health_snapshot.get("total_count", 0)
            state.run_metadata["tool_budget_limit"] = state.exec_health_monitor.get_task_total_limit(state.task_id)
            if state.token_profile_name:
                state.run_metadata["profile_used"] = state.token_profile_name

        if state.runner_result.timed_out or not state.runner_result.success:
            partial_context = self._extract_partial_context(state.runner_result)
            if (
                state.runner_result.timed_out
                or partial_context.get("files_modified")
                or partial_context.get("partial_output")
            ):
                state.run_metadata["partial_context"] = partial_context
                state.execution_result["partial_context"] = partial_context

            # Lifecycle hook: capture timeout context for retry continuity
            if state.runner_result.timed_out:
                timeout_ctx = LifecycleHooks.capture_run_timeout(
                    runner_result=state.runner_result,
                    elapsed_seconds=state.runner_result.duration_seconds or 0,
                    partial_files=partial_context.get("files_modified"),
                    partial_output=partial_context.get("partial_output"),
                )
                state.run_metadata.setdefault("lifecycle", {})["timeout"] = timeout_ctx

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
        _contract = task.get("current_contract") or {}
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
                "contract_id": _contract.get("id"),
                "contract_version": _contract.get("version"),
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
                # Independent review disabled → go directly to review.
                # Lock proposed contract into the JSONB mirror field so downstream
                # owner-review can reference it without a formal contract record.
                proposed = (execution_result or {}).get("proposed_contract")
                if isinstance(proposed, list):
                    review_data["locked_contract"] = proposed

                # Build evaluator payloads for architect-review and the upcoming owner-review
                # using the updated task state (with locked_contract now in architect_review).
                local_task = {**task, "architect_review": review_data}
                ar_evaluator_template = resolve_evaluator_template("architect-review", policy)
                ar_contract_evaluator = build_contract_aware_evaluator(
                    "architect-review", local_task, execution_result, policy=policy
                )
                owner_template = resolve_evaluator_template("owner-review", policy)
                owner_summary = build_owner_review_summary(
                    local_task, execution_result, review_context={"stage": "architect-review"}
                )
                owner_evaluator = build_contract_aware_evaluator(
                    "owner-review", local_task, execution_result, policy=policy
                )
                review_data["evaluator_template"] = ar_evaluator_template
                review_data["contract_evaluator"] = ar_contract_evaluator
                review_data["owner_review"] = {
                    "template": owner_template,
                    "contract_evaluator": owner_evaluator,
                    "summary": owner_summary,
                }
                review_data["summary"] = owner_summary["comparison_digest"]

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

            # Persist feedback artifact for rejection/escalation (enables retry context + audit)
            await self._write_review_feedback(
                task_id=task_id,
                run_id=architect_run_id,
                task=task,
                findings=review_result.findings or [],
                overall_score=review_result.confidence,
                verdict=review_result.verdict,
                suggested_retry_direction=review_result.feedback or action.reason,
                reviewer_identity=action.changed_by or "architect-reviewer",
                stage="architect-review",
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

    def _load_formal_contract_criteria(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        """Load contract criteria from archon_task_contracts using current_contract_id.

        Returns the acceptance_criteria list from the formal contract record, or an
        empty list if no current_contract_id is set or the record cannot be fetched.
        The caller injects the returned value into the task dict as
        ``_formal_contract_criteria`` before passing it to prompt builders.
        """
        contract_id = task.get("current_contract_id")
        if not contract_id:
            return []
        ok, result = self.task_service.get_contract(contract_id)
        if not ok:
            logger.warning(
                "Could not load formal contract | task_id=%s | contract_id=%s | error=%s",
                task.get("id"),
                contract_id,
                result.get("error"),
            )
            return []
        criteria = result["contract"].get("acceptance_criteria")
        if not isinstance(criteria, list):
            return []
        return criteria

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

        _cr_contract = task.get("current_contract") or {}
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
                "contract_id": _cr_contract.get("id"),
                "contract_version": _cr_contract.get("version"),
            },
        )

        # Get git diff
        git_diff = await self._get_git_diff(task_id)

        # Load formal contract from archon_task_contracts and inject into task dict
        # so the contract-aware prompt builder can reference it.  The mirror field
        # (architect_review.locked_contract) is preserved as a fallback.
        formal_criteria = self._load_formal_contract_criteria(task)
        if formal_criteria:
            task = {**task, "_formal_contract_criteria": formal_criteria}

        # Build contract-aware reviewer prompt using the formal contract criteria
        prompt = build_adversarial_code_review_prompt(task, git_diff, policy=policy)

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

        # Build contract-aware evaluator payloads for code-review and owner-review stages.
        # Formal contract criteria are already injected into `task` as `_formal_contract_criteria`
        # when `current_contract_id` is set; the JSONB mirror is the fallback.
        code_review_context = {"stage": "code-review", "verdict": verdict}
        cr_evaluator_template = resolve_evaluator_template("code-review", policy)
        cr_contract_evaluator = build_contract_aware_evaluator("code-review", task, execution_result, policy=policy)
        owner_template = resolve_evaluator_template("owner-review", policy)
        owner_review_summary_data = build_owner_review_summary(
            task, execution_result, review_context=code_review_context
        )
        owner_review_evaluator = build_contract_aware_evaluator(
            "owner-review", task, execution_result, review_context=code_review_context, policy=policy
        )
        code_review_data["evaluator_template"] = cr_evaluator_template
        code_review_data["contract_evaluator"] = cr_contract_evaluator
        code_review_data["summary"] = owner_review_summary_data["comparison_digest"]
        code_review_data["owner_review"] = {
            "template": owner_template,
            "contract_evaluator": owner_review_evaluator,
            "summary": owner_review_summary_data,
        }

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
            # Check auto-approval policy (B-P4-03)
            auto_tier = self._get_auto_approval_tier(policy)
            can_auto, auto_reason = evaluate_auto_approval(
                stage="code-review",
                task=task,
                execution_result=execution_result,
                tier=auto_tier,
                boundary_validation=task.get("_boundary_validation"),
            )

            if can_auto:
                # Auto-approve: skip manual "review" → go directly to "done"
                await self.lifecycle_service.execute_transition(
                    task_id=task_id,
                    new_status="review",
                    changed_by="code-reviewer",
                    reason="Code review approved",
                )
                await self.lifecycle_service.execute_transition(
                    task_id=task_id,
                    new_status="done",
                    changed_by="auto-approval",
                    reason=f"Auto-approved (tier {auto_tier}): {auto_reason}",
                )
                await self.notifier.emit(
                    "task.auto_approved",
                    task_id=task_id,
                    data={
                        "tier": auto_tier,
                        "stage": "code-review",
                        "reason": auto_reason,
                        "project_id": self._resolve_project_id(task),
                    },
                )
                await self.notifier.on_task_done(task_id)
                logger.info(
                    f"Task auto-approved | task_id={task_id} | tier={auto_tier} | reason={auto_reason}"
                )
            else:
                # Normal flow: manual review required
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
                logger.info(f"Code review approved (manual review required) | task_id={task_id}")

        elif verdict == "REQUEST_CHANGES":
            # Check if we have review cycles left for auto-retry
            has_critical = any(
                f.get("severity") == "critical" for f in findings if isinstance(f, dict)
            )
            feedback = "; ".join(
                f.get("description", "") for f in findings[:5] if isinstance(f, dict)
            ) or "Changes requested by code reviewer"

            # Persist feedback artifact for changes-requested (enables retry context + audit)
            cr_verdict = "escalate" if (has_critical and new_cycle >= self.MAX_CODE_REVIEW_CYCLES) else "changes-requested"
            await self._write_review_feedback(
                task_id=task_id,
                run_id=code_review_run_id,
                task=task,
                findings=findings if isinstance(findings, list) else [],
                overall_score=code_review_as_review.confidence,
                verdict=cr_verdict,
                suggested_retry_direction=feedback,
                reviewer_identity="code-reviewer",
                stage="code-review",
            )

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
