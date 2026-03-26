"""
Sandbox provider interface for execution environment isolation.

Defines the SandboxProvider protocol and concrete implementations:
- LocalDirectoryProvider: no-op default (shared checkout behaviour)
- GitWorktreeProvider: per-task git worktree isolation

Providers are acquired before runner.spawn() and released in a finally
block to guarantee cleanup regardless of execution outcome.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ...config.logfire_config import get_logger

logger = get_logger(__name__)


@dataclass
class SandboxContext:
    """Result of a successful provider.acquire() call."""

    workspace_path: str
    """Absolute path to the working directory for this task."""

    provider_name: str
    """Name of the provider that created this context (for logging)."""


@runtime_checkable
class SandboxProvider(Protocol):
    """Protocol for execution environment providers.

    Each provider manages the lifecycle of an isolated workspace for a
    single task execution:

    1. acquire(task_id, project_path) → SandboxContext
       Prepare and return the workspace path.  May create a worktree,
       a container, or simply return the project path unchanged.

    2. release(task_id, project_path)
       Clean up any resources created during acquire().  Called in a
       finally block to guarantee execution even on failure or exception.

    Implementing only these two methods is sufficient for a new provider
    to integrate with the engine without any further changes.
    """

    def acquire(self, task_id: str, project_path: str) -> SandboxContext:
        """Prepare a workspace for the given task.

        Args:
            task_id: Unique task identifier (used for path/branch naming).
            project_path: Root path of the project repository.

        Returns:
            SandboxContext with the effective workspace_path.

        Raises:
            RuntimeError: If workspace preparation fails and execution
                should not proceed.
        """
        ...

    def release(self, task_id: str, project_path: str) -> None:
        """Release resources acquired for the given task.

        Must be idempotent — safe to call even if acquire() was never
        called or raised an error for this task_id.

        Args:
            task_id: Same task identifier passed to acquire().
            project_path: Same project path passed to acquire().
        """
        ...


class LocalDirectoryProvider:
    """No-op sandbox provider — tasks run in the shared project checkout.

    This is the default provider corresponding to ``isolation="shared"``.
    acquire() returns the project_path unchanged; release() is a no-op.
    """

    provider_name = "local-directory"

    def acquire(self, task_id: str, project_path: str) -> SandboxContext:
        logger.debug(f"LocalDirectoryProvider: using shared checkout | task_id={task_id} | path={project_path}")
        return SandboxContext(workspace_path=project_path, provider_name=self.provider_name)

    def release(self, task_id: str, project_path: str) -> None:
        pass  # Nothing to clean up for shared checkout


class GitWorktreeProvider:
    """Per-task git worktree provider.

    Creates an isolated git worktree for each task so concurrent task
    executions do not interfere with each other's file modifications.

    Worktrees are stored at: ``{project_path}/{worktree_base}/{task_id}``
    with a corresponding branch named ``task/{task_id}``.

    This corresponds to ``isolation="git-worktree"``.
    """

    provider_name = "git-worktree"

    def __init__(self, worktree_base: str = ".leankit-worktrees") -> None:
        self.worktree_base = worktree_base

    def _worktree_path(self, project_path: str, task_id: str) -> str:
        return str(Path(project_path) / self.worktree_base / task_id)

    def acquire(self, task_id: str, project_path: str) -> SandboxContext:
        """Create a git worktree for isolated task execution.

        Returns:
            SandboxContext with the worktree path as workspace_path.

        Raises:
            RuntimeError: If worktree creation fails.
        """
        wt_path = self._worktree_path(project_path, task_id)
        branch = f"task/{task_id}"

        if Path(wt_path).exists():
            logger.info(f"GitWorktreeProvider: reusing existing worktree | task_id={task_id} | path={wt_path}")
            return SandboxContext(workspace_path=wt_path, provider_name=self.provider_name)

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
                err = f"Failed to create worktree for task {task_id}: {result.stderr.strip()}"
                logger.error(err)
                raise RuntimeError(err)

        logger.info(f"GitWorktreeProvider: worktree created | task_id={task_id} | path={wt_path} | branch={branch}")
        return SandboxContext(workspace_path=wt_path, provider_name=self.provider_name)

    def release(self, task_id: str, project_path: str) -> None:
        """Remove the git worktree and its associated branch."""
        wt_path = self._worktree_path(project_path, task_id)
        branch = f"task/{task_id}"

        result = subprocess.run(
            ["git", "worktree", "remove", wt_path, "--force"],
            capture_output=True,
            text=True,
            cwd=project_path,
        )

        if result.returncode != 0:
            logger.warning(f"GitWorktreeProvider: git worktree remove failed, falling back to rm | stderr={result.stderr.strip()}")
            if Path(wt_path).exists():
                shutil.rmtree(wt_path, ignore_errors=True)

        subprocess.run(
            ["git", "branch", "-D", branch],
            capture_output=True,
            text=True,
            cwd=project_path,
        )

        logger.info(f"GitWorktreeProvider: worktree released | task_id={task_id} | path={wt_path}")


def get_provider_for_isolation(
    isolation: str,
    worktree_base: str = ".leankit-worktrees",
) -> LocalDirectoryProvider | GitWorktreeProvider:
    """Return the appropriate SandboxProvider for the given isolation mode.

    Args:
        isolation: Isolation mode string from ProjectConfig.
        worktree_base: Subdirectory name for worktree storage.

    Returns:
        GitWorktreeProvider for ``"git-worktree"`` isolation,
        LocalDirectoryProvider for all other modes (``"shared"``, etc.).
    """
    if isolation == "git-worktree":
        return GitWorktreeProvider(worktree_base=worktree_base)
    return LocalDirectoryProvider()
