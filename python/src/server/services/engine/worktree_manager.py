"""
Worktree Manager — Enhanced git worktree lifecycle management.

Adopted from upstream Archon v0.3.2 worktree isolation patterns.
Extends the existing GitWorktreeProvider with:
- Branch slugification (feature/auth → feature-auth)
- Canonical repo path resolution (worktree → parent repo)
- Merge detection (ancestry check, patch equivalence)
- Auto-cleanup of stale/merged worktrees
- Listing and status reporting

Usage:
    manager = WorktreeManager(project_path="/path/to/repo")
    worktrees = manager.list_worktrees()
    manager.cleanup_merged()
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

DEFAULT_WORKTREE_BASE = ".leankit-worktrees"


def slugify_branch(name: str) -> str:
    """Convert a branch name to a filesystem-safe slug.

    Examples:
        feature/auth → feature-auth
        fix/bug #123 → fix-bug-123
        TASK-42_implement → task-42-implement
    """
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "unnamed"


@dataclass
class WorktreeInfo:
    """Information about a git worktree."""

    path: str
    branch: str
    head_sha: str = ""
    is_bare: bool = False
    is_detached: bool = False
    prunable: bool = False

    @property
    def branch_slug(self) -> str:
        return slugify_branch(self.branch)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "is_bare": self.is_bare,
            "is_detached": self.is_detached,
            "prunable": self.prunable,
        }


@dataclass
class MergeCheckResult:
    """Result of checking if a branch has been merged."""

    merged: bool = False
    method: str = ""  # "ancestry", "cherry", "pr_state"
    details: str = ""


class WorktreeManager:
    """Enhanced worktree lifecycle management.

    Provides discovery, status, merge detection, and cleanup
    for git worktrees created by the task engine.
    """

    def __init__(
        self,
        project_path: str,
        worktree_base: str = DEFAULT_WORKTREE_BASE,
    ) -> None:
        self.project_path = project_path
        self.worktree_base = worktree_base

    def _run_git(self, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
        """Run a git command and return the result."""
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd or self.project_path,
        )

    # ── Discovery ──

    def list_worktrees(self) -> list[WorktreeInfo]:
        """List all git worktrees in the repository."""
        result = self._run_git("worktree", "list", "--porcelain")
        if result.returncode != 0:
            logger.warning(f"git worktree list failed: {result.stderr.strip()}")
            return []

        worktrees: list[WorktreeInfo] = []
        current: dict[str, str] = {}

        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                if current:
                    worktrees.append(self._parse_worktree_entry(current))
                    current = {}
                continue

            if line.startswith("worktree "):
                current["path"] = line[9:]
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
            elif line.startswith("branch "):
                current["branch"] = line[7:]
            elif line == "bare":
                current["bare"] = "true"
            elif line == "detached":
                current["detached"] = "true"
            elif line == "prunable":
                current["prunable"] = "true"

        if current:
            worktrees.append(self._parse_worktree_entry(current))

        return worktrees

    def _parse_worktree_entry(self, entry: dict[str, str]) -> WorktreeInfo:
        branch = entry.get("branch", "")
        if branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/"):]
        return WorktreeInfo(
            path=entry.get("path", ""),
            branch=branch,
            head_sha=entry.get("head", ""),
            is_bare=entry.get("bare") == "true",
            is_detached=entry.get("detached") == "true",
            prunable=entry.get("prunable") == "true",
        )

    def find_by_branch(self, branch_name: str) -> WorktreeInfo | None:
        """Find a worktree by branch name (supports fuzzy slug matching)."""
        target_slug = slugify_branch(branch_name)
        for wt in self.list_worktrees():
            if wt.branch == branch_name:
                return wt
            if slugify_branch(wt.branch) == target_slug:
                return wt
        return None

    def find_by_task_id(self, task_id: str) -> WorktreeInfo | None:
        """Find a worktree created for a specific task."""
        return self.find_by_branch(f"task/{task_id}")

    # ── Path resolution ──

    def is_worktree_path(self, path: str) -> bool:
        """Check if a path is inside a git worktree (not the main repo)."""
        git_path = Path(path) / ".git"
        if git_path.is_file():
            # Worktree .git is a FILE containing: gitdir: ...
            content = git_path.read_text(encoding="utf-8").strip()
            return content.startswith("gitdir:")
        return False

    def get_canonical_repo_path(self, path: str) -> str:
        """Resolve a worktree path to its parent repository path.

        If the path is already the main repo, returns it unchanged.
        """
        result = self._run_git("rev-parse", "--git-common-dir", cwd=path)
        if result.returncode != 0:
            return path

        common_git_dir = result.stdout.strip()
        if common_git_dir.endswith("/.git"):
            return common_git_dir[:-5]
        if common_git_dir.endswith(".git"):
            return str(Path(common_git_dir).parent)

        return path

    # ── Merge detection ──

    def check_merged(self, branch: str, target_branch: str = "main") -> MergeCheckResult:
        """Check if a branch has been merged into the target branch.

        Uses multiple detection methods:
        1. Ancestry check: is the branch an ancestor of target?
        2. Cherry check: are there unmerged commits?
        """
        # Method 1: Ancestry check (fast-forward merge or rebase)
        result = self._run_git("merge-base", "--is-ancestor", branch, target_branch)
        if result.returncode == 0:
            return MergeCheckResult(
                merged=True,
                method="ancestry",
                details=f"Branch '{branch}' is ancestor of '{target_branch}'",
            )

        # Method 2: Cherry check (squash merge detection)
        result = self._run_git("cherry", target_branch, branch)
        if result.returncode == 0:
            unmerged_lines = [l for l in result.stdout.strip().split("\n") if l.startswith("+")]
            if not unmerged_lines:
                return MergeCheckResult(
                    merged=True,
                    method="cherry",
                    details=f"All commits from '{branch}' are in '{target_branch}' (squash-merged)",
                )

        return MergeCheckResult(
            merged=False,
            details=f"Branch '{branch}' has unmerged commits",
        )

    # ── Cleanup ──

    def cleanup_merged(
        self,
        target_branch: str = "main",
        dry_run: bool = False,
    ) -> list[dict[str, str]]:
        """Remove worktrees whose branches have been merged.

        Args:
            target_branch: Branch to check merge status against.
            dry_run: If True, only report what would be cleaned up.

        Returns:
            List of cleaned up worktrees with path, branch, and method.
        """
        cleaned: list[dict[str, str]] = []
        worktrees = self.list_worktrees()

        for wt in worktrees:
            if wt.is_bare or not wt.branch:
                continue
            if not wt.branch.startswith("task/"):
                continue

            merge_result = self.check_merged(wt.branch, target_branch)
            if merge_result.merged:
                entry = {
                    "path": wt.path,
                    "branch": wt.branch,
                    "method": merge_result.method,
                }

                if dry_run:
                    logger.info(f"Worktree cleanup (dry-run): would remove {wt.path} ({wt.branch})")
                else:
                    self._remove_worktree(wt.path, wt.branch)
                    logger.info(f"Worktree cleaned up: {wt.path} ({wt.branch}) — {merge_result.method}")

                cleaned.append(entry)

        if cleaned:
            logger.info(f"Worktree cleanup: {len(cleaned)} {'would be ' if dry_run else ''}removed")
        return cleaned

    def cleanup_prunable(self) -> int:
        """Prune worktrees marked as prunable by git."""
        result = self._run_git("worktree", "prune")
        if result.returncode != 0:
            logger.warning(f"git worktree prune failed: {result.stderr.strip()}")
            return 0

        # Count pruned (git doesn't report count, re-check)
        logger.info("Worktree prune completed")
        return 0  # git prune doesn't report count

    def _remove_worktree(self, wt_path: str, branch: str) -> None:
        """Remove a worktree and its branch."""
        result = self._run_git("worktree", "remove", wt_path, "--force")
        if result.returncode != 0:
            logger.warning(f"git worktree remove failed: {result.stderr.strip()}")
            import shutil
            if Path(wt_path).exists():
                shutil.rmtree(wt_path, ignore_errors=True)

        self._run_git("branch", "-D", branch)

    # ── Status reporting ──

    def get_status(self) -> dict[str, Any]:
        """Get an overview of worktree status for monitoring."""
        worktrees = self.list_worktrees()
        task_worktrees = [wt for wt in worktrees if wt.branch.startswith("task/")]
        return {
            "total_worktrees": len(worktrees),
            "task_worktrees": len(task_worktrees),
            "prunable": sum(1 for wt in worktrees if wt.prunable),
            "detached": sum(1 for wt in worktrees if wt.is_detached),
            "worktrees": [wt.to_dict() for wt in task_worktrees],
        }
