"""Tests for the SandboxProvider interface and concrete implementations (C-P4-03).

Covers:
- SandboxProvider protocol structural typing
- LocalDirectoryProvider: no-op acquire/release
- GitWorktreeProvider: acquire creates worktree, release removes it
- GitWorktreeProvider: reuses existing worktree path
- GitWorktreeProvider: raises RuntimeError on git failure
- get_provider_for_isolation: returns correct provider type
- CCSpawner.spawn: uses provider for workspace acquisition
- CCSpawner.spawn: releases provider even when _spawn_with_model raises
- CCSpawner backward-compatible create_worktree / remove_worktree API
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.server.services.engine.sandbox_provider import (
    GitWorktreeProvider,
    LocalDirectoryProvider,
    SandboxContext,
    SandboxProvider,
    get_provider_for_isolation,
)
from src.server.services.engine.cc_spawner import CCExecutionResult, CCSpawner, ProjectConfig


# ---------------------------------------------------------------------------
# Protocol structural check
# ---------------------------------------------------------------------------


class TestSandboxProviderProtocol:
    def test_local_directory_implements_protocol(self) -> None:
        provider = LocalDirectoryProvider()
        assert isinstance(provider, SandboxProvider)

    def test_git_worktree_implements_protocol(self) -> None:
        provider = GitWorktreeProvider()
        assert isinstance(provider, SandboxProvider)

    def test_custom_provider_implements_protocol(self) -> None:
        """A minimal class with acquire/release satisfies the protocol."""

        class MinimalProvider:
            def acquire(self, task_id: str, project_path: str) -> SandboxContext:
                return SandboxContext(workspace_path=project_path, provider_name="minimal")

            def release(self, task_id: str, project_path: str) -> None:
                pass

        assert isinstance(MinimalProvider(), SandboxProvider)


# ---------------------------------------------------------------------------
# LocalDirectoryProvider
# ---------------------------------------------------------------------------


class TestLocalDirectoryProvider:
    def test_acquire_returns_project_path(self) -> None:
        provider = LocalDirectoryProvider()
        ctx = provider.acquire("task-01", "/repo")
        assert ctx.workspace_path == "/repo"
        assert ctx.provider_name == "local-directory"

    def test_acquire_is_idempotent(self) -> None:
        provider = LocalDirectoryProvider()
        ctx1 = provider.acquire("task-01", "/repo")
        ctx2 = provider.acquire("task-01", "/repo")
        assert ctx1.workspace_path == ctx2.workspace_path

    def test_release_is_noop(self) -> None:
        provider = LocalDirectoryProvider()
        # Must not raise regardless of whether acquire was called
        provider.release("task-01", "/repo")
        provider.release("task-never-acquired", "/repo")


# ---------------------------------------------------------------------------
# GitWorktreeProvider
# ---------------------------------------------------------------------------


class TestGitWorktreeProvider:
    def test_acquire_creates_worktree(self, tmp_path) -> None:
        provider = GitWorktreeProvider(worktree_base=".wt")
        task_id = "task-wt-01"

        mock_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))

        with patch("subprocess.run", mock_run):
            ctx = provider.acquire(task_id, str(tmp_path))

        assert ctx.workspace_path == str(tmp_path / ".wt" / task_id)
        assert ctx.provider_name == "git-worktree"
        # git worktree add should have been called
        call_args_list = mock_run.call_args_list
        assert any("worktree" in str(call) for call in call_args_list)

    def test_acquire_reuses_existing_path(self, tmp_path) -> None:
        provider = GitWorktreeProvider(worktree_base=".wt")
        task_id = "task-existing"
        wt_path = tmp_path / ".wt" / task_id
        wt_path.mkdir(parents=True)

        mock_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))

        with patch("subprocess.run", mock_run):
            ctx = provider.acquire(task_id, str(tmp_path))

        assert ctx.workspace_path == str(wt_path)
        # No git commands needed when path already exists
        mock_run.assert_not_called()

    def test_acquire_retries_without_b_when_branch_exists(self, tmp_path) -> None:
        provider = GitWorktreeProvider(worktree_base=".wt")
        task_id = "task-branch-exists"

        # First call: fails with "already exists", second call: succeeds
        first_result = MagicMock(returncode=128, stderr="fatal: branch 'task/task-branch-exists' already exists")
        second_result = MagicMock(returncode=0, stderr="")
        mock_run = MagicMock(side_effect=[first_result, second_result])

        with patch("subprocess.run", mock_run):
            ctx = provider.acquire(task_id, str(tmp_path))

        assert ctx.workspace_path.endswith(task_id)
        assert mock_run.call_count == 2

    def test_acquire_raises_on_git_failure(self, tmp_path) -> None:
        provider = GitWorktreeProvider(worktree_base=".wt")
        task_id = "task-fail"

        fail_result = MagicMock(returncode=128, stderr="error: not a git repo")
        mock_run = MagicMock(return_value=fail_result)

        with patch("subprocess.run", mock_run):
            with pytest.raises(RuntimeError, match="Failed to create worktree"):
                provider.acquire(task_id, str(tmp_path))

    def test_release_calls_git_worktree_remove(self, tmp_path) -> None:
        provider = GitWorktreeProvider(worktree_base=".wt")
        task_id = "task-release"

        mock_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))

        with patch("subprocess.run", mock_run):
            provider.release(task_id, str(tmp_path))

        assert mock_run.call_count >= 1
        # First call should be worktree remove
        first_call_cmd = mock_run.call_args_list[0][0][0]
        assert "worktree" in first_call_cmd and "remove" in first_call_cmd

    def test_release_falls_back_to_rmtree_on_git_failure(self, tmp_path) -> None:
        provider = GitWorktreeProvider(worktree_base=".wt")
        task_id = "task-rmtree"
        wt_path = tmp_path / ".wt" / task_id
        wt_path.mkdir(parents=True)

        fail_result = MagicMock(returncode=1, stderr="error: not a valid worktree")
        ok_result = MagicMock(returncode=0, stderr="")
        mock_run = MagicMock(side_effect=[fail_result, ok_result])

        with patch("subprocess.run", mock_run):
            provider.release(task_id, str(tmp_path))

        # Directory should be removed via shutil.rmtree fallback
        assert not wt_path.exists()

    def test_release_is_safe_when_nothing_to_clean(self, tmp_path) -> None:
        provider = GitWorktreeProvider(worktree_base=".wt")
        task_id = "task-no-worktree"

        mock_run = MagicMock(return_value=MagicMock(returncode=1, stderr=""))

        with patch("subprocess.run", mock_run):
            provider.release(task_id, str(tmp_path))  # Must not raise


# ---------------------------------------------------------------------------
# get_provider_for_isolation factory
# ---------------------------------------------------------------------------


class TestGetProviderForIsolation:
    def test_git_worktree_mode_returns_git_provider(self) -> None:
        provider = get_provider_for_isolation("git-worktree")
        assert isinstance(provider, GitWorktreeProvider)

    def test_shared_mode_returns_local_provider(self) -> None:
        provider = get_provider_for_isolation("shared")
        assert isinstance(provider, LocalDirectoryProvider)

    def test_on_conflict_worktree_returns_local_provider(self) -> None:
        # on-conflict-worktree is resolved to git-worktree at the override level,
        # so when it reaches the provider the isolation field is already "git-worktree".
        # This mode itself should fall back to local (shared checkout).
        provider = get_provider_for_isolation("on-conflict-worktree")
        assert isinstance(provider, LocalDirectoryProvider)

    def test_custom_worktree_base_forwarded(self) -> None:
        provider = get_provider_for_isolation("git-worktree", worktree_base=".custom-wt")
        assert isinstance(provider, GitWorktreeProvider)
        assert provider.worktree_base == ".custom-wt"


# ---------------------------------------------------------------------------
# CCSpawner.spawn uses provider (provider lifecycle)
# ---------------------------------------------------------------------------


class TestCCSpawnerUsesProvider:
    @pytest.mark.asyncio
    async def test_spawn_acquires_and_releases_local_provider(self) -> None:
        """For shared isolation, provider.acquire/release called without git ops."""
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/repo", isolation="shared")

        ok_result = CCExecutionResult(success=True, stdout="", stderr="", exit_code=0, duration_seconds=1.0)

        with (
            patch.object(spawner, "_spawn_with_model", return_value=ok_result) as mock_spawn,
            patch("src.server.services.engine.cc_spawner.get_provider_for_isolation") as mock_factory,
        ):
            mock_provider = MagicMock()
            mock_provider.acquire.return_value = SandboxContext(workspace_path="/repo", provider_name="local-directory")
            mock_factory.return_value = mock_provider

            await spawner.spawn(task_id="t-01", prompt="do it", config=config)

        mock_provider.acquire.assert_called_once_with("t-01", "/repo")
        mock_provider.release.assert_called_once_with("t-01", "/repo")
        # _spawn_with_model should receive the resolved cwd
        _, call_kwargs = mock_spawn.call_args
        assert call_kwargs["cwd"] == "/repo"

    @pytest.mark.asyncio
    async def test_spawn_releases_provider_on_exception(self) -> None:
        """Provider.release must be called even when _spawn_with_model raises."""
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/repo", isolation="git-worktree")

        with (
            patch("src.server.services.engine.cc_spawner.get_provider_for_isolation") as mock_factory,
        ):
            mock_provider = MagicMock()
            mock_provider.acquire.return_value = SandboxContext(workspace_path="/repo/.wt/t-02", provider_name="git-worktree")
            mock_factory.return_value = mock_provider

            async def _raise(*a, **kw):
                raise RuntimeError("cc crashed")

            with patch.object(spawner, "_spawn_with_model", side_effect=_raise):
                with pytest.raises(RuntimeError, match="cc crashed"):
                    await spawner.spawn(task_id="t-02", prompt="do it", config=config)

        mock_provider.release.assert_called_once_with("t-02", "/repo")

    @pytest.mark.asyncio
    async def test_spawn_returns_failure_when_acquire_raises(self) -> None:
        """If provider.acquire raises RuntimeError, spawn returns CCExecutionResult(success=False)."""
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/repo", isolation="git-worktree")

        with patch("src.server.services.engine.cc_spawner.get_provider_for_isolation") as mock_factory:
            mock_provider = MagicMock()
            mock_provider.acquire.side_effect = RuntimeError("git not available")
            mock_factory.return_value = mock_provider

            result = await spawner.spawn(task_id="t-03", prompt="do it", config=config)

        assert not result.success
        assert "git not available" in result.stderr

    @pytest.mark.asyncio
    async def test_spawn_passes_worktree_path_as_cwd(self) -> None:
        """When git-worktree isolation, the worktree path is passed as cwd to _spawn_with_model."""
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/repo", isolation="git-worktree")
        worktree_path = "/repo/.leankit-worktrees/t-04"

        ok_result = CCExecutionResult(success=True, stdout="", stderr="", exit_code=0, duration_seconds=1.0)

        with (
            patch.object(spawner, "_spawn_with_model", return_value=ok_result) as mock_spawn,
            patch("src.server.services.engine.cc_spawner.get_provider_for_isolation") as mock_factory,
        ):
            mock_provider = MagicMock()
            mock_provider.acquire.return_value = SandboxContext(workspace_path=worktree_path, provider_name="git-worktree")
            mock_factory.return_value = mock_provider

            await spawner.spawn(task_id="t-04", prompt="do it", config=config)

        _, call_kwargs = mock_spawn.call_args
        assert call_kwargs["cwd"] == worktree_path


# ---------------------------------------------------------------------------
# CCSpawner backward-compatible API (create_worktree / remove_worktree)
# ---------------------------------------------------------------------------


class TestCCSpawnerBackwardCompatAPI:
    def test_create_worktree_returns_path_on_success(self) -> None:
        spawner = CCSpawner()

        with patch.object(spawner._worktree_provider, "acquire") as mock_acquire:
            mock_acquire.return_value = SandboxContext(workspace_path="/repo/.wt/t-05", provider_name="git-worktree")
            path, err = spawner.create_worktree("/repo", "t-05")

        assert path == "/repo/.wt/t-05"
        assert err is None

    def test_create_worktree_returns_error_on_failure(self) -> None:
        spawner = CCSpawner()

        with patch.object(spawner._worktree_provider, "acquire") as mock_acquire:
            mock_acquire.side_effect = RuntimeError("git error")
            path, err = spawner.create_worktree("/repo", "t-06")

        assert path is None
        assert "git error" in (err or "")

    def test_remove_worktree_delegates_to_provider(self) -> None:
        spawner = CCSpawner()

        with patch.object(spawner._worktree_provider, "release") as mock_release:
            success, err = spawner.remove_worktree("/repo", "t-07")

        assert success is True
        assert err is None
        mock_release.assert_called_once_with("t-07", "/repo")
