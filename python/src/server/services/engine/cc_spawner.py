"""
Claude Code Spawner for LeanKit V3 Task Engine.

Spawns Claude Code CLI sessions in subprocess, optionally
inside git worktrees for isolation. Supports real-time
streaming of CC JSON output lines via callback.

Usage:
    spawner = CCSpawner()
    result = await spawner.spawn(task_id, prompt, project_config)
"""

import asyncio
import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Type alias for stream event callback: (task_id, parsed_event) -> None
StreamCallback = Callable[[str, dict[str, Any]], Any]


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
    force_model: str | None = None  # Override model selection (e.g. "claude-opus-4-6")


# Model routing constants
MODEL_OPUS = "claude-opus-4-6"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_DEFAULT = MODEL_SONNET


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

# Learnings pattern — captures JSON array
_LEARNINGS_RE = re.compile(r"LEARNINGS:\s*(\[.*?\])", re.IGNORECASE | re.DOTALL)

# Code patterns — captures JSON array
_CODE_PATTERNS_RE = re.compile(r"CODE_PATTERNS:\s*(\[.*?\])", re.IGNORECASE | re.DOTALL)

# Code review verdict and findings (from independent reviewer CC session)
_CODE_REVIEW_VERDICT_RE = re.compile(r"CODE_REVIEW_VERDICT:\s*(APPROVE|REQUEST_CHANGES)", re.IGNORECASE)
_CODE_REVIEW_FINDINGS_RE = re.compile(r"CODE_REVIEW_FINDINGS:\s*(\[.*?\])", re.IGNORECASE | re.DOTALL)


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
    # Model routing
    # ------------------------------------------------------------------

    @staticmethod
    def select_model(
        task: dict[str, Any] | None = None,
        force_model: str | None = None,
    ) -> str:
        """Select the appropriate Claude model based on task complexity and priority.

        Routing rules:
            1. force_model (project config override) → use as-is
            2. complexity=complex OR priority=high/critical → Opus
            3. complexity=simple AND priority=low → Haiku
            4. Default (medium complexity) → Sonnet
        """
        if force_model:
            return force_model

        if not task:
            return MODEL_DEFAULT

        complexity = (task.get("complexity") or "medium").lower()
        priority = (task.get("priority") or "medium").lower()

        if complexity == "complex" or priority in ("high", "critical"):
            return MODEL_OPUS

        if complexity == "simple" and priority == "low":
            return MODEL_HAIKU

        return MODEL_DEFAULT

    # ------------------------------------------------------------------
    # Claude Code CLI execution
    # ------------------------------------------------------------------

    def _build_command(self, prompt: str, model: str | None = None) -> tuple[str, str]:
        """Build the CC CLI command and return (command, prompt_for_stdin).

        Uses --print mode with stdin prompt delivery.
        """
        parts = [
            self.cc_binary,
            "--print",
            "--output-format", "json",
            "--dangerously-skip-permissions",
        ]

        if model:
            parts.extend(["--model", model])

        return " ".join(parts), prompt

    async def spawn(
        self,
        task_id: str,
        prompt: str,
        config: ProjectConfig,
        timeout: int | None = None,
        task: dict[str, Any] | None = None,
        on_stream_event: StreamCallback | None = None,
    ) -> CCExecutionResult:
        """Spawn a Claude Code CLI session for a task.

        Args:
            task_id: Task UUID (used for worktree branch and tracking).
            prompt: Complete execution prompt.
            config: Project configuration.
            timeout: Override default timeout in seconds.
            task: Task dict for model routing (complexity/priority fields).
            on_stream_event: Callback fired for each parsed JSON line from CC stdout.

        Returns:
            CCExecutionResult with parsed output.
        """
        selected_model = self.select_model(task=task, force_model=config.force_model)

        result = await self._spawn_with_model(
            task_id=task_id,
            prompt=prompt,
            config=config,
            model=selected_model,
            timeout=timeout,
            on_stream_event=on_stream_event,
        )

        # Fallback: if non-Opus model failed, retry with Opus
        if not result.success and selected_model != MODEL_OPUS:
            logger.warning(
                f"Model {selected_model} failed, retrying with {MODEL_OPUS} | task_id={task_id}"
            )
            fallback_result = await self._spawn_with_model(
                task_id=task_id,
                prompt=prompt,
                config=config,
                model=MODEL_OPUS,
                timeout=timeout,
                on_stream_event=on_stream_event,
            )
            fallback_result.parsed["fallback_from"] = selected_model
            fallback_result.parsed["fallback_to"] = MODEL_OPUS
            return fallback_result

        return result

    async def _spawn_with_model(
        self,
        task_id: str,
        prompt: str,
        config: ProjectConfig,
        model: str,
        timeout: int | None = None,
        on_stream_event: StreamCallback | None = None,
    ) -> CCExecutionResult:
        """Execute a CC spawn with a specific model.

        Reads stdout line-by-line for real-time streaming of CC JSON output.
        Each JSON line is parsed and forwarded via on_stream_event callback.
        """
        effective_timeout = timeout or self.default_timeout

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

        command, stdin_prompt = self._build_command(prompt, model=model)

        # Calculate estimated cost savings vs always using Opus
        cost_note = ""
        if model == MODEL_SONNET:
            cost_note = " (cost: ~3x cheaper than Opus)"
        elif model == MODEL_HAIKU:
            cost_note = " (cost: ~12x cheaper than Opus)"

        logger.info(
            f"Spawning CC session | task_id={task_id} | model={model}{cost_note} | "
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
                # Write prompt to stdin, then close stdin to signal EOF
                if process.stdin:
                    process.stdin.write(stdin_prompt.encode())
                    await process.stdin.drain()
                    process.stdin.close()

                # Read stdout line-by-line for real-time streaming
                stdout_lines: list[str] = []
                stdout_task = asyncio.create_task(
                    self._stream_stdout(process, task_id, stdout_lines, on_stream_event)
                )

                # Read stderr concurrently
                stderr_task = asyncio.create_task(self._read_stderr(process))

                # Wait for process to complete with timeout
                await asyncio.wait_for(process.wait(), timeout=effective_timeout)
                # Ensure stdout/stderr readers finish
                await stdout_task
                stderr_text = await stderr_task

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
            stdout_text = "\n".join(stdout_lines)
            exit_code = process.returncode or 0
            success = exit_code == 0

            parsed = self.parse_result(stdout_text)
            parsed["model_used"] = model

            logger.info(
                f"CC session completed | task_id={task_id} | model={model} | "
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

    async def _stream_stdout(
        self,
        process: asyncio.subprocess.Process,
        task_id: str,
        stdout_lines: list[str],
        on_stream_event: StreamCallback | None,
    ) -> None:
        """Read stdout line-by-line, parse JSON lines, and fire stream callbacks."""
        if not process.stdout:
            return

        while True:
            line_bytes = await process.stdout.readline()
            if not line_bytes:
                break

            line = line_bytes.decode().rstrip("\n")
            stdout_lines.append(line)

            if not on_stream_event or not line.strip():
                continue

            parsed_event = self.parse_stream_line(line)
            if parsed_event:
                try:
                    result = on_stream_event(task_id, parsed_event)
                    # Support async callbacks
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.debug(f"Stream callback error (non-fatal): {e}")

    @staticmethod
    async def _read_stderr(process: asyncio.subprocess.Process) -> str:
        """Read all stderr content."""
        if not process.stderr:
            return ""
        data = await process.stderr.read()
        return data.decode() if data else ""

    @staticmethod
    def parse_stream_line(line: str) -> dict[str, Any] | None:
        """Parse a single JSON line from CC --output-format json stdout.

        Extracts the event type and relevant fields:
        - type=assistant → message text
        - type=tool_use → tool name + args summary
        - type=tool_result → output summary (truncated 200 chars)
        - type=thinking → thinking text (truncated 300 chars)

        Returns None for unparseable or irrelevant lines.
        """
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None

        if not isinstance(data, dict):
            return None

        line_type = data.get("type", "")

        if line_type == "assistant":
            # Extract message content
            message = ""
            content = data.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        message = block.get("text", "")
                        break
            elif isinstance(content, str):
                message = content
            if not message:
                message = data.get("message", {}).get("text", "")
            return {"event": "assistant", "message": message[:500]} if message else None

        if line_type == "tool_use":
            tool_name = data.get("tool", {}).get("name", "") or data.get("name", "")
            tool_input = data.get("tool", {}).get("input", {}) or data.get("input", {})
            # Summarize args: file path if present, otherwise first key
            args_summary = ""
            if isinstance(tool_input, dict):
                for key in ("file_path", "path", "command", "pattern", "query"):
                    if key in tool_input:
                        args_summary = str(tool_input[key])[:150]
                        break
                if not args_summary and tool_input:
                    first_key = next(iter(tool_input))
                    args_summary = f"{first_key}={str(tool_input[first_key])[:100]}"
            return {
                "event": "tool_use",
                "tool_name": tool_name,
                "args_summary": args_summary,
            } if tool_name else None

        if line_type == "tool_result":
            output = data.get("content", "") or data.get("output", "")
            if isinstance(output, list):
                # Extract text from content blocks
                texts = [b.get("text", "") for b in output if isinstance(b, dict) and b.get("type") == "text"]
                output = "\n".join(texts)
            return {
                "event": "tool_result",
                "output_summary": str(output)[:200],
            }

        if line_type == "thinking":
            thinking = data.get("thinking", "") or data.get("text", "")
            return {
                "event": "thinking",
                "message": str(thinking)[:300],
            } if thinking else None

        return None

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

        # Learnings
        m = _LEARNINGS_RE.search(stdout)
        if m:
            try:
                learnings = json.loads(m.group(1))
                if isinstance(learnings, list):
                    parsed["learnings"] = learnings
            except json.JSONDecodeError:
                logger.warning("Failed to parse LEARNINGS JSON from CC output")
                parsed["learnings"] = []
        else:
            parsed["learnings"] = []

        # Code patterns
        m = _CODE_PATTERNS_RE.search(stdout)
        if m:
            try:
                code_patterns = json.loads(m.group(1))
                if isinstance(code_patterns, list):
                    parsed["code_patterns"] = code_patterns
            except json.JSONDecodeError:
                logger.warning("Failed to parse CODE_PATTERNS JSON from CC output")
                parsed["code_patterns"] = []
        else:
            parsed["code_patterns"] = []

        # Code review verdict and findings (from reviewer sessions)
        m = _CODE_REVIEW_VERDICT_RE.search(stdout)
        if m:
            parsed["code_review_verdict"] = m.group(1).upper()

        m = _CODE_REVIEW_FINDINGS_RE.search(stdout)
        if m:
            try:
                findings = json.loads(m.group(1))
                if isinstance(findings, list):
                    parsed["code_review_findings"] = findings
            except json.JSONDecodeError:
                logger.warning("Failed to parse CODE_REVIEW_FINDINGS JSON from CC output")
                parsed["code_review_findings"] = []
        elif "code_review_verdict" in parsed:
            parsed["code_review_findings"] = []

        # If no structured block, try to extract a one-line summary from the
        # last non-empty line of stdout.
        if "result" not in parsed and stdout.strip():
            lines = [ln.strip() for ln in stdout.strip().splitlines() if ln.strip()]
            if lines:
                parsed["raw_last_line"] = lines[-1][:500]

        return parsed
