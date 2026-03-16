"""
Claude Code Spawner for LeanKit V3 Task Engine.

Spawns Claude Code CLI sessions in subprocess, optionally
inside git worktrees for isolation.

Usage:
    spawner = CCSpawner()
    result = await spawner.spawn(task_id, prompt, project_config)
"""

import asyncio
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)


@dataclass
class CCExecutionResult:
    """Result of a Claude Code CLI execution."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    parsed: dict[str, Any] = field(default_factory=dict)
    timed_out: bool = False


@dataclass
class ProjectConfig:
    """Configuration for a project execution environment."""

    project_path: str
    build_command: str = "pnpm build && pnpm test"
    isolation: str = "shared"  # "shared" | "git-worktree"
    repository_url: str | None = None


# Regex patterns for structured output parsing
_RESULT_RE = re.compile(r"RESULT:\s*(SUCCESS|FAILURE)", re.IGNORECASE)
_FILES_RE = re.compile(r"FILES_CHANGED:\s*(\d+)", re.IGNORECASE)
_TESTS_RE = re.compile(r"TESTS_ADDED:\s*(\d+)", re.IGNORECASE)
_SUMMARY_RE = re.compile(r"SUMMARY:\s*(.+)", re.IGNORECASE)

# Task assessment patterns
_ASSESS_RE = re.compile(r"TASK_ASSESSMENT:\s*(simple|complex)", re.IGNORECASE)
_EST_FILES_RE = re.compile(r"ESTIMATED_FILES:\s*(\d+)", re.IGNORECASE)
_EST_RISK_RE = re.compile(r"ESTIMATED_RISK:\s*(low|medium|high)", re.IGNORECASE)
_ASSESS_REASON_RE = re.compile(r"ASSESSMENT_REASONING:\s*(.+)", re.IGNORECASE)


class CCSpawner:
    """Spawns and manages Claude Code CLI sessions."""

    def __init__(
        self,
        cc_binary: str = "claude",
        default_timeout: int = 600,
        max_parallel: int = 5,
        worktree_base: str = ".leankit-worktrees",
    ):
        self.cc_binary = cc_binary
        self.default_timeout = default_timeout
        self.max_parallel = max_parallel
        self.worktree_base = worktree_base
        self._running: dict[str, asyncio.subprocess.Process] = {}

    @property
    def running_count(self) -> int:
        return len(self._running)

    @property
    def has_capacity(self) -> bool:
        return self.running_count < self.max_parallel

    # ------------------------------------------------------------------
    # Git worktree management
    # ------------------------------------------------------------------

    def _worktree_path(self, project_path: str, task_id: str) -> str:
        return str(Path(project_path) / self.worktree_base / task_id)

    def create_worktree(self, project_path: str, task_id: str) -> tuple[str | None, str | None]:
        """Create a git worktree for isolated task execution.

        Returns:
            Tuple of (worktree_path, error_message).
        """
        wt_path = self._worktree_path(project_path, task_id)
        branch = f"task/{task_id}"

        if Path(wt_path).exists():
            logger.info(f"Worktree already exists | path={wt_path}")
            return wt_path, None

        Path(wt_path).parent.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch, wt_path, "HEAD"],
            capture_output=True,
            text=True,
            cwd=project_path,
        )

        if result.returncode != 0:
            # Branch may already exist — retry without -b
            if "already exists" in result.stderr:
                result = subprocess.run(
                    ["git", "worktree", "add", wt_path, branch],
                    capture_output=True,
                    text=True,
                    cwd=project_path,
                )

            if result.returncode != 0:
                err = f"Failed to create worktree: {result.stderr.strip()}"
                logger.error(err)
                return None, err

        logger.info(f"Worktree created | path={wt_path} | branch={branch}")
        return wt_path, None

    def remove_worktree(self, project_path: str, task_id: str) -> tuple[bool, str | None]:
        """Remove a git worktree after task completion.

        Returns:
            Tuple of (success, error_message).
        """
        wt_path = self._worktree_path(project_path, task_id)
        branch = f"task/{task_id}"

        # Remove worktree via git
        result = subprocess.run(
            ["git", "worktree", "remove", wt_path, "--force"],
            capture_output=True,
            text=True,
            cwd=project_path,
        )

        if result.returncode != 0:
            logger.warning(f"Git worktree remove failed: {result.stderr.strip()}")
            # Fallback: remove directory
            import shutil

            if Path(wt_path).exists():
                shutil.rmtree(wt_path, ignore_errors=True)

        # Clean up branch
        subprocess.run(
            ["git", "branch", "-D", branch],
            capture_output=True,
            text=True,
            cwd=project_path,
        )

        logger.info(f"Worktree removed | path={wt_path}")
        return True, None

    # ------------------------------------------------------------------
    # Claude Code CLI execution
    # ------------------------------------------------------------------

    def _build_command(self, prompt: str, source_app: str | None = None) -> tuple[str, str]:
        """Build the CC CLI command and return (command, prompt_for_stdin).

        Uses --print mode with stdin prompt delivery.
        """
        parts = [
            self.cc_binary,
            "--print",
            "--output-format", "json",
            "--dangerously-skip-permissions",
        ]

        if source_app:
            parts.extend(["--source-app", source_app])

        return " ".join(parts), prompt

    async def spawn(
        self,
        task_id: str,
        prompt: str,
        config: ProjectConfig,
        timeout: int | None = None,
    ) -> CCExecutionResult:
        """Spawn a Claude Code CLI session for a task.

        Args:
            task_id: Task UUID (used for worktree branch and tracking).
            prompt: Complete execution prompt.
            config: Project configuration.
            timeout: Override default timeout in seconds.

        Returns:
            CCExecutionResult with parsed output.
        """
        effective_timeout = timeout or self.default_timeout
        source_app = "leankit"

        # Determine working directory
        cwd = config.project_path
        if config.isolation == "git-worktree":
            wt_path, err = self.create_worktree(config.project_path, task_id)
            if err or not wt_path:
                return CCExecutionResult(
                    success=False,
                    stdout="",
                    stderr=err or "Failed to create worktree",
                    exit_code=-1,
                    duration_seconds=0,
                )
            cwd = wt_path

        command, stdin_prompt = self._build_command(prompt, source_app)

        logger.info(
            f"Spawning CC session | task_id={task_id} | "
            f"timeout={effective_timeout}s | cwd={cwd}"
        )

        start_time = time.time()

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            self._running[task_id] = process

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(input=stdin_prompt.encode()),
                    timeout=effective_timeout,
                )
            except TimeoutError:
                await self.kill(task_id)
                duration = time.time() - start_time
                logger.error(f"CC session timed out | task_id={task_id} | duration={duration:.1f}s")
                return CCExecutionResult(
                    success=False,
                    stdout="",
                    stderr=f"Timed out after {effective_timeout}s",
                    exit_code=-1,
                    duration_seconds=duration,
                    timed_out=True,
                )

            duration = time.time() - start_time
            stdout_text = stdout_bytes.decode() if stdout_bytes else ""
            stderr_text = stderr_bytes.decode() if stderr_bytes else ""
            exit_code = process.returncode or 0
            success = exit_code == 0

            parsed = self.parse_result(stdout_text)

            logger.info(
                f"CC session completed | task_id={task_id} | "
                f"exit_code={exit_code} | duration={duration:.1f}s | "
                f"result={parsed.get('result', 'unknown')}"
            )

            return CCExecutionResult(
                success=success,
                stdout=stdout_text,
                stderr=stderr_text,
                exit_code=exit_code,
                duration_seconds=duration,
                parsed=parsed,
            )

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"CC spawn error | task_id={task_id} | error={e}", exc_info=True)
            return CCExecutionResult(
                success=False,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration_seconds=duration,
            )
        finally:
            self._running.pop(task_id, None)

    async def kill(self, task_id: str) -> None:
        """Force-kill a running CC process."""
        process = self._running.pop(task_id, None)
        if process is None:
            return
        try:
            process.kill()
            await process.wait()
            logger.info(f"CC process killed | task_id={task_id}")
        except ProcessLookupError:
            pass

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_result(stdout: str) -> dict[str, Any]:
        """Parse structured output from Claude Code.

        Looks for the structured report block:
            RESULT: SUCCESS|FAILURE
            FILES_CHANGED: {n}
            TESTS_ADDED: {n}
            SUMMARY: {text}

        Falls back to extracting whatever is available from raw output.
        """
        parsed: dict[str, Any] = {}

        m = _RESULT_RE.search(stdout)
        if m:
            parsed["result"] = m.group(1).upper()

        m = _FILES_RE.search(stdout)
        if m:
            parsed["files_changed"] = int(m.group(1))

        m = _TESTS_RE.search(stdout)
        if m:
            parsed["tests_added"] = int(m.group(1))

        m = _SUMMARY_RE.search(stdout)
        if m:
            parsed["summary"] = m.group(1).strip()

        # Task assessment fields
        m = _ASSESS_RE.search(stdout)
        if m:
            parsed["task_assessment"] = m.group(1).lower()

        m = _EST_FILES_RE.search(stdout)
        if m:
            parsed["estimated_files"] = int(m.group(1))

        m = _EST_RISK_RE.search(stdout)
        if m:
            parsed["estimated_risk"] = m.group(1).lower()

        m = _ASSESS_REASON_RE.search(stdout)
        if m:
            parsed["assessment_reasoning"] = m.group(1).strip()

        # If no structured block, try to extract a one-line summary from the
        # last non-empty line of stdout.
        if "result" not in parsed and stdout.strip():
            lines = [ln.strip() for ln in stdout.strip().splitlines() if ln.strip()]
            if lines:
                parsed["raw_last_line"] = lines[-1][:500]

        return parsed
