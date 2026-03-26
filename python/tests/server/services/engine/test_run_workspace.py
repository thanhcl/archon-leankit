"""Tests for RunWorkspaceManager."""

import shutil
import tempfile
import time
from pathlib import Path

import pytest

from src.server.services.engine.run_workspace import (
    RunWorkspaceContext,
    RunWorkspaceManager,
    _MAX_ARTIFACT_PATHS,
)


@pytest.fixture()
def tmp_base(tmp_path: Path) -> Path:
    return tmp_path / "run-workspaces"


@pytest.fixture()
def manager(tmp_base: Path) -> RunWorkspaceManager:
    return RunWorkspaceManager(base_dir=str(tmp_base))


# ------------------------------------------------------------------
# create()
# ------------------------------------------------------------------


class TestCreate:
    def test_creates_log_and_artifact_dirs(self, manager: RunWorkspaceManager) -> None:
        run_id = "run-abc123"
        ctx = manager.create(run_id)

        assert ctx.log_dir.exists()
        assert ctx.artifact_dir.exists()
        assert ctx.log_dir.name == run_id
        assert ctx.artifact_dir.name == run_id

    def test_stdout_and_stderr_paths_are_inside_log_dir(self, manager: RunWorkspaceManager) -> None:
        ctx = manager.create("run-001")
        assert ctx.stdout_path == ctx.log_dir / "stdout.log"
        assert ctx.stderr_path == ctx.log_dir / "stderr.log"

    def test_create_is_idempotent(self, manager: RunWorkspaceManager) -> None:
        run_id = "run-idempotent"
        ctx1 = manager.create(run_id)
        ctx2 = manager.create(run_id)
        assert ctx1.log_dir == ctx2.log_dir
        assert ctx1.artifact_dir == ctx2.artifact_dir

    def test_different_run_ids_get_separate_dirs(self, manager: RunWorkspaceManager) -> None:
        ctx_a = manager.create("run-aaa")
        ctx_b = manager.create("run-bbb")
        assert ctx_a.log_dir != ctx_b.log_dir
        assert ctx_a.artifact_dir != ctx_b.artifact_dir


# ------------------------------------------------------------------
# write_logs()
# ------------------------------------------------------------------


class TestWriteLogs:
    def test_writes_stdout_to_file(self, manager: RunWorkspaceManager) -> None:
        ctx = manager.create("run-wlog")
        manager.write_logs(ctx, stdout="hello stdout", stderr="")
        assert ctx.stdout_path.read_text() == "hello stdout"

    def test_writes_stderr_to_file(self, manager: RunWorkspaceManager) -> None:
        ctx = manager.create("run-wlog-stderr")
        manager.write_logs(ctx, stdout="", stderr="error occurred")
        assert ctx.stderr_path.read_text() == "error occurred"

    def test_empty_stderr_does_not_create_file(self, manager: RunWorkspaceManager) -> None:
        ctx = manager.create("run-no-stderr")
        manager.write_logs(ctx, stdout="some output", stderr="")
        assert not ctx.stderr_path.exists()

    def test_overwrites_existing_log_file(self, manager: RunWorkspaceManager) -> None:
        ctx = manager.create("run-overwrite")
        manager.write_logs(ctx, stdout="first run", stderr="")
        manager.write_logs(ctx, stdout="second run", stderr="")
        assert ctx.stdout_path.read_text() == "second run"

    def test_handles_write_error_gracefully(self, manager: RunWorkspaceManager) -> None:
        ctx = manager.create("run-err")
        # Remove the log directory to trigger write error
        shutil.rmtree(ctx.log_dir)
        # Should not raise — errors are logged as warnings
        manager.write_logs(ctx, stdout="data", stderr="err")


# ------------------------------------------------------------------
# scan_artifacts()
# ------------------------------------------------------------------


class TestScanArtifacts:
    def test_returns_manifest_with_log_paths_when_logs_exist(self, manager: RunWorkspaceManager) -> None:
        ctx = manager.create("run-scan")
        manager.write_logs(ctx, stdout="output", stderr="error")

        manifest = manager.scan_artifacts(ctx)

        assert "log_paths" in manifest
        assert "stdout" in manifest["log_paths"]
        assert "stderr" in manifest["log_paths"]
        assert manifest["log_paths"]["stdout"] == str(ctx.stdout_path)
        assert manifest["log_paths"]["stderr"] == str(ctx.stderr_path)

    def test_log_paths_empty_when_no_logs_written(self, manager: RunWorkspaceManager) -> None:
        ctx = manager.create("run-no-logs")
        manifest = manager.scan_artifacts(ctx)
        assert manifest["log_paths"] == {}

    def test_contains_workspace_base_path(self, manager: RunWorkspaceManager) -> None:
        ctx = manager.create("run-base")
        manifest = manager.scan_artifacts(ctx)
        assert manifest["workspace_base"] == str(ctx.log_dir)

    def test_scans_project_path_for_xml_artifacts(
        self, manager: RunWorkspaceManager, tmp_path: Path
    ) -> None:
        ctx = manager.create("run-xml")
        project = tmp_path / "project"
        project.mkdir()
        report = project / "test-results.xml"
        report.write_text("<xml/>")

        manifest = manager.scan_artifacts(ctx, project_path=str(project))

        assert str(report) in manifest["artifact_paths"]
        assert manifest["artifact_count"] >= 1

    def test_skips_git_directory(self, manager: RunWorkspaceManager, tmp_path: Path) -> None:
        ctx = manager.create("run-git-skip")
        project = tmp_path / "project"
        project.mkdir()
        git_dir = project / ".git"
        git_dir.mkdir()
        (git_dir / "COMMIT_EDITMSG").write_text("some commit")
        real_file = project / "coverage.json"
        real_file.write_text("{}")

        manifest = manager.scan_artifacts(ctx, project_path=str(project))

        paths = manifest["artifact_paths"]
        assert not any(".git" in p for p in paths)
        assert str(real_file) in paths

    def test_skips_node_modules(self, manager: RunWorkspaceManager, tmp_path: Path) -> None:
        ctx = manager.create("run-node-skip")
        project = tmp_path / "project"
        project.mkdir()
        nm = project / "node_modules" / "some-pkg"
        nm.mkdir(parents=True)
        nm_file = nm / "test.xml"
        nm_file.write_text("<x/>")
        real = project / "test.xml"
        real.write_text("<x/>")

        manifest = manager.scan_artifacts(ctx, project_path=str(project))

        paths = manifest["artifact_paths"]
        # Check the node_modules file specifically is not included
        assert str(nm_file) not in paths
        assert str(real) in paths

    def test_artifact_count_capped_at_max(self, manager: RunWorkspaceManager, tmp_path: Path) -> None:
        ctx = manager.create("run-cap")
        project = tmp_path / "many"
        project.mkdir()
        for i in range(_MAX_ARTIFACT_PATHS + 10):
            (project / f"file-{i}.xml").write_text("<x/>")

        manifest = manager.scan_artifacts(ctx, project_path=str(project))

        assert manifest["artifact_count"] <= _MAX_ARTIFACT_PATHS
        assert len(manifest["artifact_paths"]) <= _MAX_ARTIFACT_PATHS

    def test_stores_manifest_in_ctx(self, manager: RunWorkspaceManager) -> None:
        ctx = manager.create("run-ctx-manifest")
        manifest = manager.scan_artifacts(ctx)
        assert ctx.artifact_manifest is manifest


# ------------------------------------------------------------------
# cleanup_expired()
# ------------------------------------------------------------------


class TestCleanupExpired:
    def test_removes_old_directories(self, tmp_base: Path) -> None:
        manager = RunWorkspaceManager(base_dir=str(tmp_base))
        manager._retention_days = 0  # Everything older than 0 days = expired

        ctx = manager.create("run-old")
        # Backdate the directory modification time to 1 day ago
        old_time = time.time() - 86401
        import os
        os.utime(ctx.log_dir, (old_time, old_time))
        os.utime(ctx.artifact_dir, (old_time, old_time))

        removed = manager.cleanup_expired()
        assert removed >= 1
        assert not ctx.log_dir.exists()

    def test_keeps_recent_directories(self, manager: RunWorkspaceManager) -> None:
        ctx = manager.create("run-recent")
        # Default 7-day retention — newly created dir should survive
        removed = manager.cleanup_expired()
        assert removed == 0
        assert ctx.log_dir.exists()

    def test_returns_zero_when_base_not_exists(self, tmp_path: Path) -> None:
        manager = RunWorkspaceManager(base_dir=str(tmp_path / "nonexistent"))
        removed = manager.cleanup_expired()
        assert removed == 0


# ------------------------------------------------------------------
# Integration: write_logs + scan_artifacts
# ------------------------------------------------------------------


class TestIntegration:
    def test_full_workflow(self, manager: RunWorkspaceManager, tmp_path: Path) -> None:
        run_id = "run-full"
        ctx = manager.create(run_id)

        # Simulate execution output
        stdout = '{"type": "result", "result": "SUCCESS"}\nSUMMARY: All done'
        stderr = "warning: deprecated"
        manager.write_logs(ctx, stdout=stdout, stderr=stderr)

        # Verify logs on disk
        assert ctx.stdout_path.read_text() == stdout
        assert ctx.stderr_path.read_text() == stderr

        # Create fake project artifacts
        project = tmp_path / "project"
        project.mkdir()
        (project / "coverage.json").write_text('{"coverage": 87}')

        manifest = manager.scan_artifacts(ctx, project_path=str(project))

        assert manifest["log_paths"]["stdout"] == str(ctx.stdout_path)
        assert manifest["log_paths"]["stderr"] == str(ctx.stderr_path)
        assert any("coverage.json" in p for p in manifest["artifact_paths"])
        assert manifest["workspace_base"] == str(ctx.log_dir)
