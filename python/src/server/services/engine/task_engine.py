"""
Task Engine — main daemon loop for LeanKit V3.

Polls for assigned tasks, spawns Claude Code sessions,
monitors execution, and handles completions/failures.

Usage:
    engine = TaskEngine(project_path="/path/to/repo")
    await engine.start()   # runs until stop() is called
    await engine.stop()    # graceful shutdown
"""

import asyncio
from typing import Any

from ...config.logfire_config import get_logger
from ..projects.task_lifecycle_service import TaskLifecycleService
from ..projects.task_service import TaskService
from .architect_reviewer import ArchitectReviewer, ReviewConfig
from .cc_spawner import CCExecutionResult, CCSpawner, ProjectConfig
from .prompt_builder import PromptBuilder

logger = get_logger(__name__)


class TaskEngine:
    """Daemon that polls for assigned tasks and spawns CC sessions."""

    def __init__(
        self,
        project_path: str,
        poll_interval: int = 30,
        max_parallel: int = 5,
        default_timeout: int = 600,
        shutdown_grace: int = 60,
        build_command: str = "pnpm build && pnpm test",
        isolation: str = "shared",
        repository_url: str | None = None,
        review_config: ReviewConfig | None = None,
    ):
        self.project_config = ProjectConfig(
            project_path=project_path,
            build_command=build_command,
            isolation=isolation,
            repository_url=repository_url,
        )
        self.review_config = review_config or ReviewConfig()
        self.poll_interval = poll_interval
        self.shutdown_grace = shutdown_grace

        self.task_service = TaskService()
        self.lifecycle_service = TaskLifecycleService()
        self.prompt_builder = PromptBuilder()
        self.architect_reviewer = ArchitectReviewer()
        self.spawner = CCSpawner(
            default_timeout=default_timeout,
            max_parallel=max_parallel,
        )

        self._running = False
        self._loop_task: asyncio.Task[None] | None = None
        # task_id → asyncio.Task wrapping the CC execution
        self._execution_tasks: dict[str, asyncio.Task[CCExecutionResult]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the engine's poll loop."""
        if self._running:
            logger.warning("TaskEngine already running")
            return

        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())
        logger.info(
            f"TaskEngine started | poll_interval={self.poll_interval}s | "
            f"max_parallel={self.spawner.max_parallel} | "
            f"isolation={self.project_config.isolation}"
        )

    async def stop(self) -> None:
        """Graceful shutdown: wait for running tasks, then kill stragglers."""
        if not self._running:
            return

        self._running = False
        logger.info("TaskEngine stopping — waiting for running tasks")

        # Wait for in-flight executions up to shutdown_grace
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
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Poll cycle: check completions → pick new tasks → sleep."""
        while self._running:
            try:
                self._reap_completed()
                await self._poll_cycle()
            except Exception as e:
                logger.error(f"Poll cycle error: {e}", exc_info=True)

            await asyncio.sleep(self.poll_interval)

    def _reap_completed(self) -> None:
        """Remove finished asyncio.Tasks from the tracking dict."""
        done = [tid for tid, t in self._execution_tasks.items() if t.done()]
        for tid in done:
            self._execution_tasks.pop(tid, None)

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    async def _poll_cycle(self) -> None:
        """Fetch assigned tasks and spawn executions for available slots."""
        if not self.spawner.has_capacity:
            return

        success, result = self.task_service.list_tasks(
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

        # Sort by priority (critical > high > medium > low), then created_at
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        tasks.sort(key=lambda t: (
            priority_order.get(t.get("priority", "medium"), 2),
            t.get("created_at", ""),
        ))

        for task in tasks:
            if not self.spawner.has_capacity:
                break

            task_id = task["id"]

            # Skip if already executing
            if task_id in self._execution_tasks:
                continue

            # Launch execution in background
            exec_task = asyncio.create_task(self._execute_task(task))
            self._execution_tasks[task_id] = exec_task

    # ------------------------------------------------------------------
    # Single task execution
    # ------------------------------------------------------------------

    async def _execute_task(self, task: dict[str, Any]) -> CCExecutionResult:
        """Execute a single task: transition → build prompt → spawn → handle result."""
        task_id = task["id"]

        # Transition to executing
        ok, res = await self.lifecycle_service.execute_transition(
            task_id=task_id,
            new_status="executing",
            changed_by="task-engine",
        )
        if not ok:
            logger.error(f"Cannot transition to executing | task_id={task_id} | error={res.get('error')}")
            return CCExecutionResult(
                success=False, stdout="", stderr=res.get("error", ""), exit_code=-1, duration_seconds=0,
            )

        # Get full task data for prompt building
        ok, full = self.task_service.get_task(task_id)
        if not ok:
            logger.error(f"Failed to get task for prompt | task_id={task_id}")
            return CCExecutionResult(
                success=False, stdout="", stderr="Failed to fetch task", exit_code=-1, duration_seconds=0,
            )

        full_task = full["task"]

        # Include self-review instructions when global mode is self-review.
        # CC assessment may override to API at review time, but self-review
        # data in stdout is always useful as fallback.
        include_self_review = self.review_config.review_mode == "self-review"

        # Build prompt
        prompt = await self.prompt_builder.build(
            task=full_task,
            build_command=self.project_config.build_command,
            include_self_review=include_self_review,
        )

        # Spawn CC
        result = await self.spawner.spawn(
            task_id=task_id,
            prompt=prompt,
            config=self.project_config,
        )

        # Handle completion
        await self._on_cc_complete(task_id, result)
        return result

    async def _on_cc_complete(
        self,
        task_id: str,
        result: CCExecutionResult,
    ) -> None:
        """Handle CC session completion: update task and transition state.

        On success: transition to architect-review, run auto-review, apply decision.
        On failure: transition directly to failed.
        """
        execution_result = {
            "exit_code": result.exit_code,
            "duration_seconds": result.duration_seconds,
            "timed_out": result.timed_out,
            **result.parsed,
        }
        if result.stderr:
            execution_result["stderr_preview"] = result.stderr[:1000]

        # Store execution result on the task
        await self.task_service.update_task(
            task_id=task_id,
            update_fields={"execution_result": execution_result},
        )

        if result.success and result.parsed.get("result", "").upper() != "FAILURE":
            # Success → architect-review
            await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status="architect-review",
                changed_by="task-engine",
            )
            logger.info(f"Task execution succeeded → architect-review | task_id={task_id}")

            # Run auto-review and apply decision
            await self._run_architect_review(task_id, execution_result)
        else:
            # Failure
            reason = (
                result.parsed.get("summary")
                or result.stderr[:500]
                or "CC session failed"
            )
            await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status="failed",
                changed_by="task-engine",
                reason=reason,
            )
            logger.warning(f"Task execution failed | task_id={task_id} | reason={reason[:200]}")

    async def _run_architect_review(
        self,
        task_id: str,
        execution_result: dict[str, Any],
    ) -> None:
        """Run architect auto-review and transition based on decision."""
        # Get full task for review
        ok, full = self.task_service.get_task(task_id)
        if not ok:
            logger.error(f"Cannot fetch task for review | task_id={task_id}")
            return

        task = full["task"]

        review_result, action = await self.architect_reviewer.review(
            task, execution_result, config=self.review_config,
        )

        # Store review on task
        review_data: dict[str, Any] = {
            "verdict": review_result.verdict,
            "confidence": review_result.confidence,
            "findings": review_result.findings,
            "feedback": review_result.feedback,
            "summary": review_result.summary,
        }
        if action.warning:
            review_data["warning"] = action.warning
        if review_result.error:
            review_data["error"] = review_result.error
        if action.escalation_reason:
            review_data["escalation_reason"] = action.escalation_reason

        update_fields: dict[str, Any] = {"architect_review": review_data}

        # When escalating, store structured reason in rejection_reason
        if action.next_status == "escalated":
            update_fields["rejection_reason"] = action.reason

        await self.task_service.update_task(
            task_id=task_id,
            update_fields=update_fields,
        )

        # Apply lifecycle transition
        await self.lifecycle_service.execute_transition(
            task_id=task_id,
            new_status=action.next_status,
            changed_by=action.changed_by,
            reason=action.reason,
        )

        logger.info(
            f"Architect review applied | task_id={task_id} | "
            f"verdict={review_result.verdict} → {action.next_status}"
        )
