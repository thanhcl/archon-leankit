"""
Run-local workspace manager for execution runs.

Each execution run gets a dedicated directory keyed by execution_run_id:
    {base_dir}/logs/{run_id}/stdout.log
    {base_dir}/logs/{run_id}/stderr.log
    {base_dir}/artifacts/{run_id}/

After execution, the workspace is scanned for artifacts (test reports,
coverage files, generated outputs) and an artifact manifest is produced
for storage in execution_run.metadata.

Workspace retention is controlled by LEANKIT_RUN_WORKSPACE_RETENTION_DAYS
(default: 7 days). Cleanup runs lazily on workspace creation.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Environment variable that controls the base directory for run workspaces.
# Defaults to a sibling of the project path resolved at engine init time.
_ENV_BASE_DIR = "LEANKIT_RUN_WORKSPACE_BASE"
_ENV_RETENTION_DAYS = "LEANKIT_RUN_WORKSPACE_RETENTION_DAYS"

_DEFAULT_RETENTION_DAYS = 7

# Artifact patterns to scan after execution.
# Glob patterns relative to the artifact directory root (project workspace).
_ARTIFACT_PATTERNS: list[str] = [
    # Test reports
    "**/*.xml",
    "**/test-results/**/*.xml",
    "**/coverage/**",
    "**/coverage-report/**",
    # Coverage files
    "**/.coverage",
    "**/coverage.json",
    "**/lcov.info",
    # Generated outputs common in CI
    "**/*.log",
    "**/dist/**",
    "**/build/**",
]

# Maximum number of artifact paths to record in the manifest.
_MAX_ARTIFACT_PATHS = 100


@dataclass
class RunWorkspaceContext:
    """Paths and state for a single execution run workspace."""

    run_id: str
    log_dir: Path
    artifact_dir: Path
    stdout_path: Path
    stderr_path: Path

    # Filled in after scan_artifacts() is called
    artifact_manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def workspace_root(self) -> Path:
        """Parent of log_dir — the run-scoped root under logs/."""
        return self.log_dir


class RunWorkspaceManager:
    """Manages per-run workspace directories for execution logs and artifacts.

    Directory layout under base_dir:
        logs/{run_id}/stdout.log
        logs/{run_id}/stderr.log
        artifacts/{run_id}/          (scan target after execution)

    Usage:
        manager = RunWorkspaceManager(base_dir="/tmp/leankit-runs")
        ctx = manager.create(run_id)
        # write logs via ctx.stdout_path / ctx.stderr_path
        manifest = manager.scan_artifacts(ctx, project_path="/path/to/project")
        # manifest is stored in execution_run.metadata["artifact_manifest"]
    """

    def __init__(self, base_dir: str | None = None) -> None:
        resolved = base_dir or os.environ.get(_ENV_BASE_DIR)
        if resolved:
            self._base = Path(resolved)
        else:
            # Default: sibling directory to the server package, inside a temp area
            self._base = Path("/tmp/leankit-run-workspaces")

        self._retention_days = int(os.environ.get(_ENV_RETENTION_DAYS, _DEFAULT_RETENTION_DAYS))

    @property
    def base_dir(self) -> Path:
        return self._base

    # ------------------------------------------------------------------
    # Workspace lifecycle
    # ------------------------------------------------------------------

    def create(self, run_id: str) -> RunWorkspaceContext:
        """Create workspace directories for the given execution run ID.

        Directories created:
            {base}/logs/{run_id}/
            {base}/artifacts/{run_id}/

        Args:
            run_id: Execution run UUID used as the directory key.

        Returns:
            RunWorkspaceContext with all resolved paths.
        """
        log_dir = self._base / "logs" / run_id
        artifact_dir = self._base / "artifacts" / run_id

        log_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        ctx = RunWorkspaceContext(
            run_id=run_id,
            log_dir=log_dir,
            artifact_dir=artifact_dir,
            stdout_path=log_dir / "stdout.log",
            stderr_path=log_dir / "stderr.log",
        )
        logger.debug(f"RunWorkspace created | run_id={run_id} | log_dir={log_dir}")
        return ctx

    def write_logs(self, ctx: RunWorkspaceContext, stdout: str, stderr: str) -> None:
        """Write stdout and stderr content to the workspace log files.

        Args:
            ctx: Workspace context returned by create().
            stdout: Full stdout string from the execution.
            stderr: Full stderr string from the execution.
        """
        try:
            ctx.stdout_path.write_text(stdout, encoding="utf-8")
        except OSError as exc:
            logger.warning(f"RunWorkspace: failed to write stdout.log | run_id={ctx.run_id} | error={exc}")

        if stderr:
            try:
                ctx.stderr_path.write_text(stderr, encoding="utf-8")
            except OSError as exc:
                logger.warning(f"RunWorkspace: failed to write stderr.log | run_id={ctx.run_id} | error={exc}")

    def scan_artifacts(
        self,
        ctx: RunWorkspaceContext,
        project_path: str | None = None,
    ) -> dict[str, Any]:
        """Scan for artifacts generated during execution and build a manifest.

        Scans the artifact_dir (and optionally the project_path) for files
        matching known artifact patterns (test reports, coverage, dist outputs).

        Args:
            ctx: Workspace context for the run.
            project_path: Optional project working directory to scan for
                task-generated files (e.g. dist/, build/, coverage/).

        Returns:
            Artifact manifest dict suitable for storage in execution_run.metadata.
            Keys: log_paths, artifact_paths, artifact_count, workspace_base.
        """
        artifact_paths: list[str] = []

        # Always include log files that exist
        log_paths: dict[str, str] = {}
        if ctx.stdout_path.exists():
            log_paths["stdout"] = str(ctx.stdout_path)
        if ctx.stderr_path.exists():
            log_paths["stderr"] = str(ctx.stderr_path)

        # Scan project path for common artifact patterns
        if project_path:
            scan_root = Path(project_path)
            artifact_paths.extend(
                self._scan_directory(scan_root, _ARTIFACT_PATTERNS)
            )

        # Deduplicate and cap
        artifact_paths = list(dict.fromkeys(artifact_paths))[:_MAX_ARTIFACT_PATHS]

        manifest: dict[str, Any] = {
            "workspace_base": str(ctx.log_dir),
            "log_paths": log_paths,
            "artifact_paths": artifact_paths,
            "artifact_count": len(artifact_paths),
        }
        ctx.artifact_manifest = manifest
        logger.debug(
            f"RunWorkspace artifact scan complete | run_id={ctx.run_id} | "
            f"artifacts={len(artifact_paths)} | logs={list(log_paths.keys())}"
        )
        return manifest

    # ------------------------------------------------------------------
    # Retention / cleanup
    # ------------------------------------------------------------------

    def cleanup_expired(self) -> int:
        """Remove workspace directories older than the retention period.

        Returns:
            Number of run directories removed.
        """
        cutoff = time.time() - self._retention_days * 86400
        removed = 0

        for subdir_name in ("logs", "artifacts"):
            subdir = self._base / subdir_name
            if not subdir.exists():
                continue
            for run_dir in subdir.iterdir():
                if not run_dir.is_dir():
                    continue
                try:
                    mtime = run_dir.stat().st_mtime
                    if mtime < cutoff:
                        shutil.rmtree(run_dir, ignore_errors=True)
                        removed += 1
                        logger.debug(f"RunWorkspace: expired directory removed | path={run_dir}")
                except OSError as exc:
                    logger.warning(f"RunWorkspace: cleanup error | path={run_dir} | error={exc}")

        if removed:
            logger.info(f"RunWorkspace: cleanup complete | removed={removed} | retention_days={self._retention_days}")
        return removed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _scan_directory(root: Path, patterns: list[str]) -> list[str]:
        """Scan a directory tree using glob patterns.

        Skips hidden directories (.git, .leankit-worktrees, node_modules)
        and only returns files (not directories).
        """
        found: list[str] = []
        _SKIP_DIRS = {".git", ".leankit-worktrees", "node_modules", "__pycache__", ".venv", "venv"}

        for pattern in patterns:
            try:
                for match in root.glob(pattern):
                    # Skip if any path component is a known ignore directory
                    if any(part in _SKIP_DIRS for part in match.parts):
                        continue
                    if match.is_file():
                        found.append(str(match))
                        if len(found) >= _MAX_ARTIFACT_PATHS:
                            return found
            except OSError:
                pass

        return found
