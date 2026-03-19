"""
Task Engine — main daemon loop for LeanKit V3.

Polls for assigned tasks, spawns Claude Code sessions,
monitors execution, handles completions/failures,
pushes notifications, and monitors engine health.

Usage:
    engine = TaskEngine(project_path="/path/to/repo")
    await engine.start()   # runs until stop() is called
    await engine.stop()    # graceful shutdown
"""

import asyncio
from typing import Any

from ...config.logfire_config import get_logger
from ..cost_budget_service import CostBudgetService
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
from .capacity_tracker import GlobalCapacityTracker
from .cc_spawner import MODEL_SONNET, CCExecutionResult, CCSpawner, ProjectConfig
from .health_monitor import HealthMonitor
from .learning_processor import LearningProcessor
from .notifier import Notifier
from .prompt_builder import PromptBuilder

logger = get_logger(__name__)


class TaskEngine:
    """Daemon that polls for assigned tasks and spawns CC sessions."""

    def __init__(
        self,
        project_path: str,
        project_id: str | None = None,
        poll_interval: int = 30,
        max_parallel: int = 5,
        default_timeout: int = 600,
        shutdown_grace: int = 60,
        build_command: str = "pnpm build && pnpm test",
        isolation: str = "shared",
        repository_url: str | None = None,
        review_config: ReviewConfig | None = None,
        global_tracker: GlobalCapacityTracker | None = None,
    ):
        self.project_id = project_id
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

        self.task_service = TaskService()
        self.lifecycle_service = TaskLifecycleService()
        self.architect_reviewer = ArchitectReviewer()
        self.notifier = Notifier()
        self.cost_budget_service = CostBudgetService()
        self.learning_processor = LearningProcessor(notifier=self.notifier)
        self.bug_task_creator = BugTaskCreator(task_service=self.task_service)
        self.prompt_builder = PromptBuilder(learning_processor=self.learning_processor)
        self.health_monitor = HealthMonitor(
            task_service=self.task_service,
            notifier=self.notifier,
        )
        self.spawner = CCSpawner(
            default_timeout=default_timeout,
            max_parallel=max_parallel,
        )

        self._running = False
        self._loop_task: asyncio.Task[None] | None = None
        self._execution_tasks: dict[str, asyncio.Task[CCExecutionResult]] = {}
        self._poll_cycle_count: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the engine's poll loop and health monitor."""
        if self._running:
            logger.warning("TaskEngine already running")
            return

        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())
        await self.health_monitor.start()
        logger.info(
            f"TaskEngine started | project_id={self.project_id} | "
            f"poll_interval={self.poll_interval}s | "
            f"max_parallel={self.spawner.max_parallel} | "
            f"global_limit={'∞' if not self.global_tracker else self.global_tracker.max_global} | "
            f"isolation={self.project_config.isolation}"
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

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    def _has_capacity(self) -> bool:
        """Check both per-project and global capacity."""
        if not self.spawner.has_capacity:
            return False
        if self.global_tracker and not self.global_tracker.has_capacity():
            return False
        return True

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

        for task in executable_tasks:
            if not self._has_capacity():
                break
            task_id = task["id"]
            if task_id in self._execution_tasks:
                continue

            # Acquire global slot before spawning
            if self.global_tracker and self.project_id:
                if not self.global_tracker.try_acquire(self.project_id):
                    logger.info(f"Global capacity limit reached | project_id={self.project_id}")
                    break

            exec_task = asyncio.create_task(self._execute_task(task))
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

            unresolved = []
            for bid in blocked_by:
                status = blocker_statuses.get(bid, "unknown")
                if status != "done":
                    unresolved.append(f"{bid} (status: {status})")

            if unresolved:
                logger.info(
                    f"Task blocked | task_id={task['id']} | "
                    f"blocked_by=[{', '.join(unresolved)}]"
                )
            else:
                executable.append(task)

        return executable

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

    async def _execute_task(self, task: dict[str, Any]) -> CCExecutionResult:
        task_id = task["id"]

        # Transition to executing
        ok, res = await self.lifecycle_service.execute_transition(
            task_id=task_id, new_status="executing", changed_by="task-engine",
        )
        if not ok:
            logger.error(f"Cannot transition to executing | task_id={task_id} | error={res.get('error')}")
            return CCExecutionResult(
                success=False, stdout="", stderr=res.get("error", ""), exit_code=-1, duration_seconds=0,
            )

        # Get full task
        ok, full = self.task_service.get_task(task_id)
        if not ok:
            logger.error(f"Failed to get task for prompt | task_id={task_id}")
            return CCExecutionResult(
                success=False, stdout="", stderr="Failed to fetch task", exit_code=-1, duration_seconds=0,
            )
        full_task = full["task"]

        # Notify: task started
        await self.notifier.on_task_started(full_task)

        # Build unified prompt (always includes assessment + self-review)
        prompt, injection_stats = await self.prompt_builder.build(
            task=full_task,
            build_command=self.project_config.build_command,
        )

        logger.info(
            f"Injection stats | task_id={task_id} | "
            f"learnings={injection_stats['learnings']} | "
            f"patterns={injection_stats['patterns']} | "
            f"kb_chunks={injection_stats['kb_chunks']} | "
            f"tokens={injection_stats['tokens']}"
        )

        # Build stream callback for real-time agent status forwarding
        async def _on_stream(tid: str, stream_event: dict) -> None:
            await self.notifier.on_agent_status(
                task_id=tid,
                agent_id=tid,
                stream_event=stream_event,
            )

        # Spawn CC (with model routing based on task complexity/priority)
        result = await self.spawner.spawn(
            task_id=task_id, prompt=prompt, config=self.project_config, task=full_task,
            on_stream_event=_on_stream,
        )

        # Handle completion
        await self._on_cc_complete(task_id, result, injection_stats)
        return result

    async def _on_cc_complete(
        self,
        task_id: str,
        result: CCExecutionResult,
        injection_stats: dict[str, Any] | None = None,
    ) -> None:
        """Handle CC completion: store result, review, notify."""
        execution_result = {
            "exit_code": result.exit_code,
            "duration_seconds": result.duration_seconds,
            "timed_out": result.timed_out,
            **result.parsed,
        }
        if injection_stats:
            execution_result["injection"] = injection_stats
        if result.stderr:
            execution_result["stderr_preview"] = result.stderr[:1000]
        if result.stdout:
            execution_result["stdout"] = result.stdout

        # Build executed_by metadata from CC result
        executed_by = {
            "model": result.parsed.get("model_used", "unknown"),
            "session_id": task_id,
            "source_app": "claude-code-cli",
            "duration_seconds": result.duration_seconds,
        }
        if result.parsed.get("fallback_from"):
            executed_by["fallback_from"] = result.parsed["fallback_from"]

        # Store execution result and executed_by
        await self.task_service.update_task(
            task_id=task_id,
            update_fields={
                "execution_result": execution_result,
                "executed_by": executed_by,
            },
        )

        # Record cost for budget tracking
        if self.project_id:
            self.cost_budget_service.record_task_cost(self.project_id, task_id, execution_result)

        if result.success and result.parsed.get("result", "").upper() != "FAILURE":
            # Success → architect-review → auto-review
            await self.lifecycle_service.execute_transition(
                task_id=task_id, new_status="architect-review", changed_by="task-engine",
            )

            await self.notifier.on_task_completed(task_id, execution_result)

            await self._run_architect_review(task_id, execution_result)
        else:
            reason = result.parsed.get("summary") or result.stderr[:500] or "CC session failed"
            await self.lifecycle_service.execute_transition(
                task_id=task_id, new_status="failed", changed_by="task-engine", reason=reason,
            )
            await self.notifier.on_task_failed(task_id, reason)
            logger.warning(f"Task execution failed | task_id={task_id} | reason={reason[:200]}")

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

        review_result, action = await self.architect_reviewer.review(
            task, execution_result, config=self.review_config,
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

            if self.review_config.independent_review_enabled:
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
                await self.notifier.on_task_review_ready(task_id, review_data)
        else:
            # Non-review transitions (assigned/escalated) — apply directly
            await self.lifecycle_service.execute_transition(
                task_id=task_id,
                new_status=action.next_status,
                changed_by=action.changed_by,
                reason=action.reason,
            )

            if action.next_status == "escalated":
                await self.notifier.on_task_escalated(task_id, action.reason)

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
            await self.learning_processor.process(task, learnings, code_patterns)

        logger.info(
            f"Architect review applied | task_id={task_id} | "
            f"verdict={review_result.verdict} → {action.next_status}"
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
                task_id, f"Max code review cycles ({self.MAX_CODE_REVIEW_CYCLES}) exceeded"
            )
            return

        # Get git diff
        git_diff = await self._get_git_diff(task_id)

        # Build reviewer prompt
        prompt = self._build_code_review_prompt(task, git_diff)

        # Spawn fresh CC session with Sonnet model (cost saving)
        review_config = ProjectConfig(
            project_path=self.project_config.project_path,
            build_command=self.project_config.build_command,
            isolation=self.project_config.isolation,
            repository_url=self.project_config.repository_url,
            force_model=MODEL_SONNET,
        )

        logger.info(f"Spawning code review CC session | task_id={task_id} | model={MODEL_SONNET}")

        review_result = await self.spawner.spawn(
            task_id=f"{task_id}-review",
            prompt=prompt,
            config=review_config,
            timeout=300,  # 5 min timeout for review
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
            "model": MODEL_SONNET,
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
            provider=MODEL_SONNET,
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
            "model": MODEL_SONNET,
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
            await self.notifier.on_task_review_ready(task_id, code_review_data)
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
                    task_id, f"Critical findings after {new_cycle} review cycles"
                )
            else:
                # Auto-retry: send back to assigned with feedback
                await self.lifecycle_service.execute_transition(
                    task_id=task_id,
                    new_status="assigned",
                    changed_by="code-reviewer",
                    reason=feedback,
                )
                await self.notifier.on_code_review_changes_requested(task_id, code_review_data)

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
                task_id, f"Code review returned unknown verdict: {verdict}"
            )
