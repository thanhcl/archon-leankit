"""
Execution runner adapter boundary for LeanKit task engine.

This module decouples TaskEngine from any one concrete coding runtime.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from .cc_spawner import MODEL_SONNET, CCExecutionResult, CCSpawner, ProjectConfig
from .run_workspace import RunWorkspaceContext

DEFAULT_RUNNER_KEY = "claude-code-cli"
CODEX_DEFAULT_RUNNER_KEY = "codex-cli"


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def get_engine_default_runner_key() -> str:
    """Return the runtime default runner, respecting global kill switches."""
    disable_claude = _env_enabled("LEANKIT_ENGINE_DISABLE_CLAUDE_CODE")
    disable_codex = _env_enabled("LEANKIT_ENGINE_DISABLE_CODEX")

    if disable_claude and not disable_codex:
        return CODEX_DEFAULT_RUNNER_KEY
    return DEFAULT_RUNNER_KEY


@runtime_checkable
class ExecutionRunner(Protocol):
    """Protocol implemented by execution runtime adapters."""

    runner_key: str
    source_app: str
    review_model: str
    max_parallel: int
    running_count: int
    has_capacity: bool

    def select_model(
        self,
        task: dict[str, Any] | None = None,
        force_model: str | None = None,
    ) -> str: ...

    async def spawn(
        self,
        task_id: str,
        prompt: str,
        config: ProjectConfig,
        timeout: int | None = None,
        task: dict[str, Any] | None = None,
        runtime_metadata: dict[str, Any] | None = None,
        on_stream_event: Callable[[str, dict[str, Any]], Any] | None = None,
        token_profile: dict[str, Any] | None = None,
        model_fallback_chain: list[str] | None = None,
        workspace_context: RunWorkspaceContext | None = None,
        **kwargs: Any,
    ) -> CCExecutionResult: ...

    async def kill(self, task_id: str) -> bool: ...


class ClaudeCodeRunnerAdapter:
    """Runner adapter that wraps the current Claude Code spawner."""

    runner_key = DEFAULT_RUNNER_KEY
    source_app = DEFAULT_RUNNER_KEY
    review_model = MODEL_SONNET

    def __init__(self, spawner: CCSpawner):
        self._spawner = spawner

    @property
    def max_parallel(self) -> int:
        return self._spawner.max_parallel

    @property
    def running_count(self) -> int:
        return self._spawner.running_count

    @property
    def has_capacity(self) -> bool:
        return self._spawner.has_capacity

    def select_model(
        self,
        task: dict[str, Any] | None = None,
        force_model: str | None = None,
    ) -> str:
        return self._spawner.select_model(task=task, force_model=force_model)

    async def spawn(
        self,
        task_id: str,
        prompt: str,
        config: ProjectConfig,
        timeout: int | None = None,
        task: dict[str, Any] | None = None,
        runtime_metadata: dict[str, Any] | None = None,
        on_stream_event: Callable[[str, dict[str, Any]], Any] | None = None,
        token_profile: dict[str, Any] | None = None,
        model_fallback_chain: list[str] | None = None,
        workspace_context: RunWorkspaceContext | None = None,
        **kwargs: Any,
    ) -> CCExecutionResult:
        return await self._spawner.spawn(
            task_id=task_id,
            prompt=prompt,
            config=config,
            timeout=timeout,
            task=task,
            runtime_metadata=runtime_metadata,
            on_stream_event=on_stream_event,
            token_profile=token_profile,
            model_fallback_chain=model_fallback_chain,
            workspace_context=workspace_context,
            **kwargs,
        )

    def get_process(self, task_id: str) -> asyncio.subprocess.Process | None:
        return self._spawner.get_process(task_id)

    async def kill(self, task_id: str) -> bool:
        return await self._spawner.kill(task_id)
