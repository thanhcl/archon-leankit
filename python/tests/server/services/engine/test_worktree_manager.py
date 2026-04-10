"""Tests for WorktreeManager — enhanced git worktree lifecycle."""

import pytest

from src.server.services.engine.worktree_manager import (
    MergeCheckResult,
    WorktreeInfo,
    WorktreeManager,
    slugify_branch,
)


class TestSlugifyBranch:
    """Test branch name slugification."""

    def test_simple_name(self) -> None:
        assert slugify_branch("main") == "main"

    def test_slash_to_dash(self) -> None:
        assert slugify_branch("feature/auth") == "feature-auth"

    def test_spaces_and_specials(self) -> None:
        assert slugify_branch("fix/bug #123") == "fix-bug-123"

    def test_uppercase_to_lower(self) -> None:
        assert slugify_branch("TASK-42_implement") == "task-42-implement"

    def test_multiple_specials(self) -> None:
        assert slugify_branch("feature/auth/login--test") == "feature-auth-login-test"

    def test_empty_string(self) -> None:
        assert slugify_branch("") == "unnamed"

    def test_only_specials(self) -> None:
        assert slugify_branch("///") == "unnamed"


class TestWorktreeInfo:
    """Test WorktreeInfo dataclass."""

    def test_branch_slug(self) -> None:
        info = WorktreeInfo(path="/tmp/wt", branch="task/abc-123")
        assert info.branch_slug == "task-abc-123"

    def test_to_dict(self) -> None:
        info = WorktreeInfo(
            path="/tmp/wt",
            branch="task/abc",
            head_sha="abc123",
            is_bare=False,
            is_detached=False,
            prunable=True,
        )
        d = info.to_dict()
        assert d["path"] == "/tmp/wt"
        assert d["branch"] == "task/abc"
        assert d["prunable"] is True


class TestMergeCheckResult:
    """Test MergeCheckResult."""

    def test_merged(self) -> None:
        r = MergeCheckResult(merged=True, method="ancestry")
        assert r.merged is True

    def test_not_merged(self) -> None:
        r = MergeCheckResult(merged=False)
        assert r.merged is False


class TestWorktreeManagerInit:
    """Test WorktreeManager initialization (no git repo needed)."""

    def test_init(self) -> None:
        mgr = WorktreeManager(project_path="/tmp/test-repo")
        assert mgr.project_path == "/tmp/test-repo"
        assert mgr.worktree_base == ".leankit-worktrees"

    def test_custom_base(self) -> None:
        mgr = WorktreeManager(project_path="/tmp", worktree_base=".custom-wt")
        assert mgr.worktree_base == ".custom-wt"


class TestIsWorktreePath:
    """Test worktree path detection."""

    def test_non_git_path(self, tmp_path) -> None:
        mgr = WorktreeManager(project_path=str(tmp_path))
        assert mgr.is_worktree_path(str(tmp_path)) is False

    def test_git_directory(self, tmp_path) -> None:
        # Regular repo has .git as directory
        (tmp_path / ".git").mkdir()
        mgr = WorktreeManager(project_path=str(tmp_path))
        assert mgr.is_worktree_path(str(tmp_path)) is False

    def test_git_file_worktree(self, tmp_path) -> None:
        # Worktree has .git as file
        (tmp_path / ".git").write_text("gitdir: /some/repo/.git/worktrees/branch")
        mgr = WorktreeManager(project_path=str(tmp_path))
        assert mgr.is_worktree_path(str(tmp_path)) is True


class TestGetStatus:
    """Test status reporting."""

    def test_status_structure(self, tmp_path) -> None:
        # Use a real temp directory so subprocess.run doesn't fail with FileNotFoundError
        mgr = WorktreeManager(project_path=str(tmp_path))
        status = mgr.get_status()
        assert "total_worktrees" in status
        assert "task_worktrees" in status
        assert "prunable" in status
        assert "detached" in status
        assert "worktrees" in status
