"""Codex CLI runner adapter for LeanKit task execution."""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from typing import Any

from ...config.logfire_config import get_logger
from .cc_spawner import CCExecutionResult, CCSpawner, ProjectConfig
from .codex_hook_publisher import CodexHookPublisher
from .run_workspace import RunWorkspaceContext

logger = get_logger(__name__)

CODEX_RUNNER_KEY = "codex-cli"
CODEX_DEFAULT_MODEL = "codex-default"


class CodexRunnerAdapter:
    """Runner adapter that executes work through `codex exec --json`."""

    runner_key = CODEX_RUNNER_KEY
    source_app = CODEX_RUNNER_KEY
    review_model = CODEX_DEFAULT_MODEL

    def __init__(
        self,
        codex_binary: str = "codex",
        default_timeout: int = 600,
        max_parallel: int = 5,
        worktree_base: str = ".leankit-worktrees",
        hook_publisher: CodexHookPublisher | None = None,
    ):
        self.codex_binary = codex_binary
        self.default_timeout = default_timeout
        self.max_parallel = max_parallel
        self._running: dict[str, asyncio.subprocess.Process] = {}
        self._cc_helper = CCSpawner(
            cc_binary="claude",
            default_timeout=default_timeout,
            max_parallel=max_parallel,
            worktree_base=worktree_base,
        )
        self._hook_publisher = hook_publisher or CodexHookPublisher()

    @property
    def running_count(self) -> int:
        return len(self._running)

    @property
    def has_capacity(self) -> bool:
        return self.running_count < self.max_parallel

    def get_process(self, task_id: str) -> asyncio.subprocess.Process | None:
        """Expose the live subprocess handle for watchdog liveness checks."""
        return self._running.get(task_id)

    def select_model(
        self,
        task: dict[str, Any] | None = None,
        force_model: str | None = None,
    ) -> str:
        """Return explicit model override when provided, otherwise a stable default label."""
        if force_model:
            return force_model
        return CODEX_DEFAULT_MODEL

    def _build_command(self, cwd: str, model: str | None) -> list[str]:
        command = [
            self.codex_binary,
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--cd",
            cwd,
            "-",
        ]
        if model and model != CODEX_DEFAULT_MODEL:
            command[2:2] = ["-m", model]
        return command

    async def spawn(
        self,
        task_id: str,
        prompt: str,
        config: ProjectConfig,
        timeout: int | None = None,
        task: dict[str, Any] | None = None,
        runtime_metadata: dict[str, Any] | None = None,
        on_stream_event: Any = None,
        token_profile: dict[str, Any] | None = None,
        model_fallback_chain: list[str] | None = None,
        workspace_context: RunWorkspaceContext | None = None,
        **kwargs: Any,
    ) -> CCExecutionResult:
        effective_timeout = timeout or self.default_timeout
        selected_model = self.select_model(task=task, force_model=config.force_model)

        cwd = config.project_path
        if config.isolation == "git-worktree":
            wt_path, err = self._cc_helper.create_worktree(config.project_path, task_id)
            if err or not wt_path:
                return CCExecutionResult(
                    success=False,
                    stdout="",
                    stderr=err or "Failed to create worktree",
                    exit_code=-1,
                    duration_seconds=0,
                )
            cwd = wt_path

        command = self._build_command(cwd, selected_model)
        logger.info(
            f"Spawning Codex session | task_id={task_id} | model={selected_model} | "
            f"timeout={effective_timeout}s | cwd={cwd}"
        )
        start_time = time.time()

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._running[task_id] = process

            try:
                if process.stdin:
                    process.stdin.write(prompt.encode())
                    await process.stdin.drain()
                    process.stdin.close()

                transcript_lines: list[str] = []
                stdout_task = asyncio.create_task(
                    self._stream_stdout(
                        process=process,
                        task_id=task_id,
                        transcript_lines=transcript_lines,
                        runtime_metadata=runtime_metadata or {},
                        model=selected_model,
                        on_stream_event=on_stream_event,
                    )
                )
                stderr_task = asyncio.create_task(self._read_stderr(process))

                await asyncio.wait_for(process.wait(), timeout=effective_timeout)
                await stdout_task
                stderr_text = await stderr_task
            except TimeoutError:
                await self.kill(task_id)
                duration = time.time() - start_time
                return CCExecutionResult(
                    success=False,
                    stdout="",
                    stderr=f"Timed out after {effective_timeout}s",
                    exit_code=-1,
                    duration_seconds=duration,
                    timed_out=True,
                )

            duration = time.time() - start_time
            stdout_text = "\n".join(line for line in transcript_lines if line.strip())
            exit_code = process.returncode or 0
            parsed = CCSpawner.parse_result(stdout_text)
            parsed["model_used"] = selected_model
            parsed["runner_key"] = self.runner_key

            return CCExecutionResult(
                success=exit_code == 0,
                stdout=stdout_text,
                stderr=stderr_text,
                exit_code=exit_code,
                duration_seconds=duration,
                parsed=parsed,
            )
        except FileNotFoundError:
            duration = time.time() - start_time
            return CCExecutionResult(
                success=False,
                stdout="",
                stderr=f"Codex binary not found: {self.codex_binary}",
                exit_code=-1,
                duration_seconds=duration,
            )
        except Exception as exc:
            duration = time.time() - start_time
            logger.error(f"Codex spawn error | task_id={task_id} | error={exc}", exc_info=True)
            return CCExecutionResult(
                success=False,
                stdout="",
                stderr=str(exc),
                exit_code=-1,
                duration_seconds=duration,
            )
        finally:
            self._running.pop(task_id, None)
            if config.isolation == "git-worktree":
                self._cc_helper.remove_worktree(config.project_path, task_id)

    async def _stream_stdout(
        self,
        *,
        process: asyncio.subprocess.Process,
        task_id: str,
        transcript_lines: list[str],
        runtime_metadata: dict[str, Any],
        model: str,
        on_stream_event: Any,
    ) -> None:
        if not process.stdout:
            return
        buffer = bytearray()
        chunk_size = 65536
        max_line_buffer = 4 * 1024 * 1024

        while True:
            chunk = await process.stdout.read(chunk_size)
            if not chunk:
                if buffer:
                    await self._consume_stream_line(
                        bytes(buffer).decode(errors="replace"),
                        task_id=task_id,
                        transcript_lines=transcript_lines,
                        runtime_metadata=runtime_metadata,
                        model=model,
                        on_stream_event=on_stream_event,
                    )
                break

            buffer.extend(chunk)
            while True:
                newline_index = buffer.find(b"\n")
                if newline_index == -1:
                    break

                line_bytes = bytes(buffer[:newline_index])
                del buffer[: newline_index + 1]
                await self._consume_stream_line(
                    line_bytes.decode(errors="replace"),
                    task_id=task_id,
                    transcript_lines=transcript_lines,
                    runtime_metadata=runtime_metadata,
                    model=model,
                    on_stream_event=on_stream_event,
                )

            if len(buffer) > max_line_buffer:
                logger.warning(
                    "Codex stdout line exceeded %s bytes without newline | task_id=%s | truncating for liveness",
                    max_line_buffer,
                    task_id,
                )
                await self._consume_stream_line(
                    bytes(buffer).decode(errors="replace"),
                    task_id=task_id,
                    transcript_lines=transcript_lines,
                    runtime_metadata=runtime_metadata,
                    model=model,
                    on_stream_event=on_stream_event,
                )
                buffer.clear()

    async def _consume_stream_line(
        self,
        line: str,
        *,
        task_id: str,
        transcript_lines: list[str],
        runtime_metadata: dict[str, Any],
        model: str,
        on_stream_event: Any,
    ) -> None:
        normalized = line.rstrip("\n")
        if not normalized.strip():
            return

        parsed_event = self.parse_stream_line(normalized)
        if parsed_event:
            message = parsed_event.get("message")
            if isinstance(message, str) and message.strip():
                transcript_lines.append(message)

            self._publish_hook_event(
                task_id=task_id,
                stream_event=parsed_event,
                raw_line=normalized,
                runtime_metadata=runtime_metadata,
                model=model,
            )

            if on_stream_event:
                result = on_stream_event(task_id, parsed_event)
                if asyncio.iscoroutine(result):
                    await result
            return

        transcript_lines.append(normalized)

    def _publish_hook_event(
        self,
        *,
        task_id: str,
        stream_event: dict[str, Any],
        raw_line: str,
        runtime_metadata: dict[str, Any],
        model: str,
    ) -> None:
        source_app = str(runtime_metadata.get("source_app") or "unknown")
        run_id = str(runtime_metadata.get("session_id") or task_id)
        task_ref = runtime_metadata.get("task_id") or task_id
        execution_run_id = runtime_metadata.get("execution_run_id")
        agent_id = runtime_metadata.get("agent_id") or task_id

        hook_payload = dict(stream_event)
        hook_payload["raw_jsonl"] = raw_line
        if "stage" in runtime_metadata:
            hook_payload["stage"] = runtime_metadata["stage"]

        self._hook_publisher.publish(
            source_app=source_app,
            run_id=run_id,
            event=str(stream_event.get("event") or "codex_output"),
            task_id=str(task_ref) if task_ref else None,
            execution_run_id=str(execution_run_id) if execution_run_id else None,
            agent_id=str(agent_id) if agent_id else None,
            model=model,
            data=hook_payload,
        )

    @staticmethod
    async def _read_stderr(process: asyncio.subprocess.Process) -> str:
        if not process.stderr:
            return ""
        data = await process.stderr.read()
        return data.decode() if data else ""

    @staticmethod
    def _extract_text(value: Any) -> list[str]:
        texts: list[str] = []
        if isinstance(value, str):
            if value.strip():
                texts.append(value.strip())
            return texts
        if isinstance(value, dict):
            for key in ("message", "content", "text", "output", "summary", "delta"):
                if key in value:
                    texts.extend(CodexRunnerAdapter._extract_text(value[key]))
            return texts
        if isinstance(value, list):
            for item in value:
                texts.extend(CodexRunnerAdapter._extract_text(item))
        return texts

    @staticmethod
    def parse_stream_line(line: str) -> dict[str, Any] | None:
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None

        if not isinstance(data, dict):
            return None

        event_name = data.get("type") or data.get("event") or "codex_output"
        texts = CodexRunnerAdapter._extract_text(data)
        message = "\n".join(texts).strip()
        parsed: dict[str, Any] = {
            "event": str(event_name),
            "message": message[:1000] if message else "",
            "raw": data,
        }

        command = data.get("command")
        if isinstance(command, str):
            parsed["tool_name"] = "command"
            parsed["args_summary"] = command[:300]
        elif isinstance(command, list):
            rendered = " ".join(shlex.quote(str(part)) for part in command[:20])
            parsed["tool_name"] = "command"
            parsed["args_summary"] = rendered[:300]

        return parsed

    async def kill(self, task_id: str, grace_seconds: int = 10) -> bool:
        """Gracefully terminate a running Codex process.

        Sends SIGTERM first, waits up to grace_seconds for clean exit,
        then falls back to SIGKILL. Adopted from CCS signal-forwarder pattern.
        """
        process = self._running.pop(task_id, None)
        if process is None:
            return False
        try:
            process.terminate()  # SIGTERM
            try:
                await asyncio.wait_for(process.wait(), timeout=grace_seconds)
                logger.info(f"Codex process terminated gracefully | task_id={task_id}")
            except TimeoutError:
                process.kill()  # SIGKILL after grace period
                await process.wait()
                logger.info(f"Codex process killed after {grace_seconds}s grace | task_id={task_id}")
            return True
        except ProcessLookupError:
            return False
