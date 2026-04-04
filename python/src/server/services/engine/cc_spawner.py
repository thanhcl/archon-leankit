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
import os as _os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config.env_aliases import get_runner_env_allowlist, get_runner_env_stripped_prefixes
from ...config.logfire_config import get_logger
from .run_workspace import RunWorkspaceContext
from .sandbox_provider import GitWorktreeProvider, SandboxContext, get_provider_for_isolation

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

# Stderr signals that indicate the runner binary itself failed to start,
# rather than the task failing during execution.
_RUNNER_ERROR_SIGNALS = (
    "binary not found",
    "command not found",
    "no such file or directory",
    "exec format error",
    "permission denied: ",
)


def is_runner_level_failure(result: CCExecutionResult) -> bool:
    """Return True if the failure is at the runner binary level, not task-level.

    Runner-level failures include: binary not found, permission denied on the
    executable, and OS-level process creation errors. These are distinguished
    from task-level failures (where the runner ran but the task itself failed)
    by a combination of exit_code, short duration, and stderr content.
    """
    if result.success:
        return False
    if result.exit_code != -1:
        return False
    # Very short duration indicates the runner never started executing the task
    if result.duration_seconds >= 5.0:
        return False
    stderr_lower = result.stderr.lower()
    return any(signal in stderr_lower for signal in _RUNNER_ERROR_SIGNALS)


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
_LEARNINGS_RE = re.compile(r"LEARNINGS:\s*(\[.*\])", re.IGNORECASE)

# Code patterns — captures JSON array
_CODE_PATTERNS_RE = re.compile(r"CODE_PATTERNS:\s*(\[.*\])", re.IGNORECASE)

# Code review verdict and findings (from independent reviewer CC session)
_CODE_REVIEW_VERDICT_RE = re.compile(r"CODE_REVIEW_VERDICT:\s*(APPROVE|REQUEST_CHANGES)", re.IGNORECASE)
_CODE_REVIEW_FINDINGS_RE = re.compile(r"CODE_REVIEW_FINDINGS:\s*(\[.*\])", re.IGNORECASE)


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
        self._worktree_provider = GitWorktreeProvider(worktree_base=worktree_base)

    @property
    def running_count(self) -> int:
        return len(self._running)

    @property
    def has_capacity(self) -> bool:
        return self.running_count < self.max_parallel

    # ------------------------------------------------------------------
    # Git worktree management (delegates to GitWorktreeProvider)
    # ------------------------------------------------------------------

    def _worktree_path(self, project_path: str, task_id: str) -> str:
        return self._worktree_provider._worktree_path(project_path, task_id)

    def create_worktree(self, project_path: str, task_id: str) -> tuple[str | None, str | None]:
        """Create a git worktree for isolated task execution.

        Returns:
            Tuple of (worktree_path, error_message).
        """
        try:
            ctx = self._worktree_provider.acquire(task_id, project_path)
            return ctx.workspace_path, None
        except RuntimeError as exc:
            return None, str(exc)

    def remove_worktree(self, project_path: str, task_id: str) -> tuple[bool, str | None]:
        """Remove a git worktree after task completion.

        Returns:
            Tuple of (success, error_message).
        """
        self._worktree_provider.release(task_id, project_path)
        return True, None

    # ------------------------------------------------------------------
    # Model routing
    # ------------------------------------------------------------------

    # Stages where review-grade models are enforced regardless of task metadata
    _REVIEW_STAGES = frozenset({"code-review", "architect-review"})

    @staticmethod
    def select_model(
        task: dict[str, Any] | None = None,
        force_model: str | None = None,
        stage: str | None = None,
        retry_count: int = 0,
        previous_model: str | None = None,
    ) -> str:
        """Select the appropriate Claude model based on stage, task metadata, and retry state.

        Routing rules (evaluated in order):
            1. force_model (project config override) → use as-is
            2. stage=architect-review → always Opus (design reasoning)
            3. stage=code-review → always >= Sonnet (quality gate)
            4. stage=retry → model >= previous attempt (escalation ladder)
            5. complexity=complex OR priority=high/critical → Opus
            6. complexity=simple AND priority=low → Haiku (execute only)
            7. Default → Sonnet

        The stage parameter ensures review stages never receive a model weaker
        than Sonnet, preventing the false-approve rework cycle where a weak
        reviewer approves bad code that the owner later rejects.
        """
        if force_model:
            return force_model

        # Stage-aware routing: review stages get strong models unconditionally
        if stage == "architect-review":
            return MODEL_OPUS
        if stage == "code-review":
            return MODEL_SONNET

        # Retry escalation: never downgrade on retry
        if stage == "retry" or retry_count > 0:
            base = CCSpawner._select_by_task_metadata(task)
            if previous_model:
                return CCSpawner._escalate_model(previous_model, base, retry_count)
            return base

        return CCSpawner._select_by_task_metadata(task)

    @staticmethod
    def _select_by_task_metadata(task: dict[str, Any] | None = None) -> str:
        """Select model based on task complexity and priority (execute stage)."""
        if not task:
            return MODEL_DEFAULT

        complexity = (task.get("complexity") or "medium").lower()
        priority = (task.get("priority") or "medium").lower()

        if complexity == "complex" or priority in ("high", "critical"):
            return MODEL_OPUS

        if complexity == "simple" and priority == "low":
            return MODEL_HAIKU

        return MODEL_DEFAULT

    @staticmethod
    def _escalate_model(previous_model: str, base_model: str, retry_count: int) -> str:
        """Ensure retry model is >= previous attempt. Escalation ladder:
        haiku/codex → sonnet (retry 1), sonnet → opus (retry 2+).
        """
        _MODEL_TIER = {MODEL_HAIKU: 0, MODEL_SONNET: 1, MODEL_DEFAULT: 1, MODEL_OPUS: 2}
        prev_tier = _MODEL_TIER.get(previous_model, 1)
        base_tier = _MODEL_TIER.get(base_model, 1)

        # Retry must be >= previous model
        effective_tier = max(prev_tier, base_tier)

        # Escalate on retry: bump up one tier from previous
        if retry_count >= 2:
            effective_tier = max(effective_tier, 2)  # → Opus
        elif retry_count >= 1:
            effective_tier = max(effective_tier, 1)  # → at least Sonnet

        if effective_tier >= 2:
            return MODEL_OPUS
        if effective_tier >= 1:
            return MODEL_SONNET
        return MODEL_HAIKU

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
        runtime_metadata: dict[str, Any] | None = None,
        on_stream_event: StreamCallback | None = None,
        token_profile: dict[str, Any] | None = None,
        model_fallback_chain: list[str] | None = None,
        workspace_context: RunWorkspaceContext | None = None,
        stage: str | None = None,
        retry_count: int = 0,
        previous_model: str | None = None,
        manage_sandbox: bool = True,
        sandbox_ctx: SandboxContext | None = None,
    ) -> CCExecutionResult:
        """Spawn a Claude Code CLI session for a task.

        Args:
            task_id: Task UUID (used for worktree branch and tracking).
            prompt: Complete execution prompt.
            config: Project configuration.
            timeout: Override default timeout in seconds.
            task: Task dict for model routing (complexity/priority fields).
            runtime_metadata: Reserved for runner adapters that publish native stream telemetry.
            on_stream_event: Callback fired for each parsed JSON line from CC stdout.
            token_profile: Optional profile dict with max_tokens/temperature_pct/model_hint
                to inject as env vars into the subprocess.
            model_fallback_chain: Ordered list of model names to try after the selected
                model fails. When None, falls back to Opus once if a cheaper model was
                selected (preserving legacy behaviour). Set to [] to disable fallback.
            workspace_context: Optional run workspace context for persisting stdout/stderr
                to log files keyed by execution_run_id.
            stage: Execution stage (execute, code-review, architect-review, retry).
                When provided, review stages enforce minimum model tiers.
            retry_count: Number of previous failed attempts for this task.
            previous_model: Model used in the previous attempt (for retry escalation).
            manage_sandbox: When True (default), spawn() acquires and releases
                the sandbox provider internally (legacy behaviour).  When False,
                the caller owns sandbox lifecycle — ``sandbox_ctx`` must be
                provided and the caller is responsible for calling
                ``provider.release()`` after validation and merge.
            sandbox_ctx: Pre-acquired SandboxContext when ``manage_sandbox=False``.

        Returns:
            CCExecutionResult with parsed output.
        """
        selected_model = self.select_model(
            task=task,
            force_model=config.force_model,
            stage=stage,
            retry_count=retry_count,
            previous_model=previous_model,
        )

        if manage_sandbox:
            provider = get_provider_for_isolation(config.isolation, worktree_base=self.worktree_base)
            try:
                sandbox_ctx = provider.acquire(task_id, config.project_path)
            except RuntimeError as exc:
                return CCExecutionResult(
                    success=False,
                    stdout="",
                    stderr=str(exc),
                    exit_code=-1,
                    duration_seconds=0,
                )
        else:
            provider = None
            if sandbox_ctx is None:
                return CCExecutionResult(
                    success=False,
                    stdout="",
                    stderr="manage_sandbox=False but no sandbox_ctx provided",
                    exit_code=-1,
                    duration_seconds=0,
                )

        try:
            result = await self._spawn_with_model(
                task_id=task_id,
                prompt=prompt,
                config=config,
                cwd=sandbox_ctx.workspace_path,
                model=selected_model,
                timeout=timeout,
                on_stream_event=on_stream_event,
                token_profile=token_profile,
                workspace_context=workspace_context,
            )

            if not result.success:
                # Build the effective fallback list: explicit chain or legacy single-Opus retry
                chain: list[str] = []
                if model_fallback_chain is not None:
                    chain = model_fallback_chain
                elif selected_model != MODEL_OPUS:
                    chain = [MODEL_OPUS]

                attempted = {selected_model}
                last_result = result
                for fallback_model in chain:
                    if fallback_model in attempted:
                        continue
                    attempted.add(fallback_model)
                    logger.warning(
                        f"Model {selected_model} failed, trying fallback {fallback_model} | task_id={task_id}"
                    )
                    last_result = await self._spawn_with_model(
                        task_id=task_id,
                        prompt=prompt,
                        config=config,
                        cwd=sandbox_ctx.workspace_path,
                        model=fallback_model,
                        timeout=timeout,
                        on_stream_event=on_stream_event,
                        token_profile=token_profile,
                        workspace_context=workspace_context,
                    )
                    last_result.parsed["fallback_from"] = selected_model
                    last_result.parsed["fallback_to"] = fallback_model
                    if model_fallback_chain is not None:
                        last_result.parsed["model_fallback_chain"] = model_fallback_chain
                    if last_result.success:
                        return last_result
                return last_result

            return result
        finally:
            # Only release sandbox here if spawn() owns the lifecycle (legacy mode).
            # When manage_sandbox=False, the pipeline owns release timing.
            if manage_sandbox and provider is not None:
                provider.release(task_id, config.project_path)

    async def _spawn_with_model(
        self,
        task_id: str,
        prompt: str,
        config: ProjectConfig,
        cwd: str,
        model: str,
        timeout: int | None = None,
        on_stream_event: StreamCallback | None = None,
        token_profile: dict[str, Any] | None = None,
        workspace_context: RunWorkspaceContext | None = None,
    ) -> CCExecutionResult:
        """Execute a CC spawn with a specific model.

        Reads stdout line-by-line for real-time streaming of CC JSON output.
        Each JSON line is parsed and forwarded via on_stream_event callback.

        Args:
            cwd: Working directory resolved by the sandbox provider.
            workspace_context: Optional run workspace context. When provided,
                stdout and stderr are written to log files in the workspace
                directory after the process completes.
        """
        effective_timeout = timeout or self.default_timeout

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

        # Build subprocess environment with:
        #  1. Inherited env stripped of ANTHROPIC_*/OPENAI_* prefixes (CCS adoption)
        #  2. Explicit re-injection of the intended API key
        #  3. Allowlist-filtered token profile vars
        #  4. Per-spawn CLAUDE_CONFIG_DIR isolation (CCS adoption)
        allowlist = set(get_runner_env_allowlist())
        stripped_prefixes = get_runner_env_stripped_prefixes()

        base_env = dict(_os.environ)

        # Strip inherited keys that match stripped prefixes to prevent engine
        # credentials from leaking into runner sessions.
        stripped_keys: list[str] = []
        for key in list(base_env.keys()):
            if any(key.startswith(prefix) for prefix in stripped_prefixes):
                stripped_keys.append(key)
                del base_env[key]

        # Re-inject the API key explicitly so runners can authenticate.
        # This makes the injection path explicit rather than implicitly inherited.
        api_key = _os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            base_env["ANTHROPIC_API_KEY"] = api_key

        if stripped_keys:
            logger.debug(
                f"Runner env stripped | task_id={task_id} | stripped={sorted(stripped_keys)}"
            )

        spawn_env = base_env
        injected_env_keys: list[str] = []

        if token_profile:
            candidate_injections: dict[str, str] = {}
            if "max_tokens" in token_profile:
                candidate_injections["LEANKIT_RUNNER_MAX_TOKENS"] = str(token_profile["max_tokens"])
            if "temperature_pct" in token_profile:
                candidate_injections["LEANKIT_RUNNER_TEMPERATURE_PCT"] = str(token_profile["temperature_pct"])
            if "model_hint" in token_profile:
                candidate_injections["LEANKIT_RUNNER_MODEL_HINT"] = str(token_profile["model_hint"])

            for key, value in candidate_injections.items():
                if key in allowlist:
                    spawn_env[key] = value
                    injected_env_keys.append(key)
                else:
                    logger.warning(
                        f"Runner env var blocked by allowlist | key={key} | task_id={task_id} | "
                        f"allowlist={sorted(allowlist)}"
                    )

        # Per-spawn CLAUDE_CONFIG_DIR isolation: create a temporary config
        # directory so concurrent CC processes don't share session state.
        # Adopted from CCS instance-manager pattern.
        #
        # IMPORTANT: The temp config dir must inherit auth credentials from
        # the real config dir (~/.claude or CLAUDE_CONFIG_DIR).  Without this,
        # CC CLI sees an empty config dir and reports "Not logged in".
        # We symlink the auth-related files rather than copying secrets.
        config_dir: Path | None = None
        try:
            config_dir = Path(tempfile.mkdtemp(prefix=f"leankit-cc-{task_id[:8]}-"))

            # Inherit auth credentials from the real config directory
            real_config_dir = Path(_os.environ.get("CLAUDE_CONFIG_DIR", "")) or Path.home() / ".claude"
            if real_config_dir.is_dir():
                # Symlink auth-related files so CC CLI can authenticate
                auth_files = [
                    ".credentials.json", "credentials.json",
                    ".oauth_token", "oauth_token",
                    "settings.json", "settings.local.json",
                    "statsig", "statsig_metadata",
                ]
                for auth_file in auth_files:
                    src = real_config_dir / auth_file
                    if src.exists():
                        dst = config_dir / auth_file
                        try:
                            dst.symlink_to(src)
                        except OSError:
                            pass  # Non-fatal: some files may not be symlinkable

            spawn_env["CLAUDE_CONFIG_DIR"] = str(config_dir)
            logger.debug(f"Per-spawn config dir created | task_id={task_id} | config_dir={config_dir}")
        except OSError as exc:
            logger.warning(f"Failed to create per-spawn config dir, using inherited | task_id={task_id} | error={exc}")

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                env=spawn_env,
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
                # Drain stdout with a bounded timeout — the pipe may not receive EOF
                # immediately if child processes of the shell still hold it open
                try:
                    await asyncio.wait_for(stdout_task, timeout=30)
                except TimeoutError:
                    stdout_task.cancel()
                    try:
                        await stdout_task
                    except asyncio.CancelledError:
                        pass
                stderr_text = await stderr_task

            except TimeoutError:
                await self.kill(task_id)
                # Drain remaining stdout after process kill; reader exits on EOF
                try:
                    await asyncio.wait_for(stdout_task, timeout=5)
                except (TimeoutError, asyncio.CancelledError, Exception):
                    stdout_task.cancel()
                partial_stdout = "\n".join(stdout_lines)
                duration = time.time() - start_time
                logger.error(
                    f"CC session timed out | task_id={task_id} | duration={duration:.1f}s | "
                    f"partial_lines={len(stdout_lines)}"
                )
                return CCExecutionResult(
                    success=False,
                    stdout=partial_stdout,
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
            parsed["injected_env_keys"] = injected_env_keys

            logger.info(
                f"CC session completed | task_id={task_id} | model={model} | "
                f"exit_code={exit_code} | duration={duration:.1f}s | "
                f"result={parsed.get('result', 'unknown')}"
            )

            if workspace_context is not None:
                from .run_workspace import RunWorkspaceManager
                RunWorkspaceManager().write_logs(workspace_context, stdout_text, stderr_text)

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
            # Cleanup per-spawn config directory
            if config_dir is not None:
                try:
                    shutil.rmtree(config_dir, ignore_errors=True)
                    logger.debug(f"Per-spawn config dir cleaned up | task_id={task_id}")
                except OSError:
                    pass

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

        if line_type == "result":
            return {
                "event": "result",
                "is_error": data.get("is_error", False),
                "total_cost_usd": data.get("total_cost_usd"),
                "usage": data.get("usage"),
            }

        return None

    def get_process(self, task_id: str) -> "asyncio.subprocess.Process | None":
        """Return the subprocess handle for a running task, or None if not tracked."""
        return self._running.get(task_id)

    async def kill(self, task_id: str, grace_seconds: int = 10) -> bool:
        """Gracefully terminate a running CC process.

        Sends SIGTERM first, waits up to grace_seconds for clean exit,
        then falls back to SIGKILL. Adopted from CCS signal-forwarder pattern
        — see docs/design/what-we-adopt-from-ccs.md.

        Returns True if a process was killed.
        """
        process = self._running.pop(task_id, None)
        if process is None:
            return False
        try:
            process.terminate()  # SIGTERM — allows CC to save partial state
            try:
                await asyncio.wait_for(process.wait(), timeout=grace_seconds)
                logger.info(f"CC process terminated gracefully | task_id={task_id}")
            except TimeoutError:
                process.kill()  # SIGKILL — force after grace period
                await process.wait()
                logger.info(f"CC process killed after {grace_seconds}s grace | task_id={task_id}")
            return True
        except ProcessLookupError:
            return False

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

        # Extract LLM token usage and cost from CC JSON result event.
        # The result event appears as a JSON line with type="result" in the stdout stream.
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("type") != "result":
                continue
            if data.get("total_cost_usd") is not None:
                parsed["llm_cost_usd"] = float(data["total_cost_usd"])
            usage = data.get("usage")
            if isinstance(usage, dict):
                input_tok = usage.get("input_tokens", 0) or 0
                output_tok = usage.get("output_tokens", 0) or 0
                cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
                cache_read = usage.get("cache_read_input_tokens", 0) or 0
                thinking_tok = usage.get("thinking_input_tokens", 0) or 0
                total_tok = input_tok + output_tok + cache_creation + cache_read
                parsed["llm_input_tokens"] = input_tok
                parsed["llm_output_tokens"] = output_tok
                parsed["llm_total_tokens"] = total_tok
                if thinking_tok:
                    parsed["llm_thinking_tokens"] = thinking_tok
            break

        # If no structured block, try to extract a one-line summary from the
        # last non-empty line of stdout.
        if "result" not in parsed and stdout.strip():
            lines = [ln.strip() for ln in stdout.strip().splitlines() if ln.strip()]
            if lines:
                parsed["raw_last_line"] = lines[-1][:500]

        return parsed
