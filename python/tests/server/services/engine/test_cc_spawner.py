"""Tests for CCSpawner — spawn, parse, timeout, worktree."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.cc_spawner import (
    CCSpawner,
    ProjectConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_process(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Create a mock asyncio.subprocess.Process."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode())
    )
    proc.returncode = returncode
    proc.kill = AsyncMock()
    proc.wait = AsyncMock()
    return proc


# ---------------------------------------------------------------------------
# Tests: parse_result
# ---------------------------------------------------------------------------


class TestParseResult:
    def test_full_structured_output(self):
        stdout = (
            "Some preamble...\n"
            "RESULT: SUCCESS\n"
            "FILES_CHANGED: 3\n"
            "TESTS_ADDED: 2\n"
            "SUMMARY: Implemented login endpoint with JWT\n"
        )
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["result"] == "SUCCESS"
        assert parsed["files_changed"] == 3
        assert parsed["tests_added"] == 2
        assert parsed["summary"] == "Implemented login endpoint with JWT"

    def test_failure_result(self):
        stdout = "RESULT: FAILURE\nSUMMARY: Build failed\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["result"] == "FAILURE"
        assert parsed["summary"] == "Build failed"

    def test_partial_output(self):
        stdout = "RESULT: SUCCESS\nSome other text\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["result"] == "SUCCESS"
        assert "files_changed" not in parsed

    def test_no_structured_output(self):
        stdout = "Did some work\nAll done!\n"
        parsed = CCSpawner.parse_result(stdout)
        assert "result" not in parsed
        assert parsed["raw_last_line"] == "All done!"

    def test_empty_output(self):
        parsed = CCSpawner.parse_result("")
        assert parsed == {"learnings": []}

    def test_task_assessment_parsed(self):
        stdout = (
            "TASK_ASSESSMENT: complex\n"
            "ESTIMATED_FILES: 12\n"
            "ESTIMATED_RISK: high\n"
            "ASSESSMENT_REASONING: Touches auth middleware and 3 service layers\n"
            "RESULT: SUCCESS\n"
        )
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["task_assessment"] == "complex"
        assert parsed["estimated_files"] == 12
        assert parsed["estimated_risk"] == "high"
        assert "auth middleware" in parsed["assessment_reasoning"]
        assert parsed["result"] == "SUCCESS"

    def test_task_assessment_simple_low_risk(self):
        stdout = "TASK_ASSESSMENT: simple\nESTIMATED_FILES: 2\nESTIMATED_RISK: low\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["task_assessment"] == "simple"
        assert parsed["estimated_risk"] == "low"

    def test_assessment_missing_is_ok(self):
        stdout = "RESULT: SUCCESS\nSUMMARY: Done\n"
        parsed = CCSpawner.parse_result(stdout)
        assert "task_assessment" not in parsed
        assert "estimated_risk" not in parsed

    def test_case_insensitive(self):
        stdout = "result: success\nfiles_changed: 5\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["result"] == "SUCCESS"
        assert parsed["files_changed"] == 5


# ---------------------------------------------------------------------------
# Tests: spawn
# ---------------------------------------------------------------------------


class TestSpawn:
    @pytest.mark.asyncio
    async def test_successful_spawn(self):
        proc = _mock_process(
            stdout="RESULT: SUCCESS\nFILES_CHANGED: 2\nSUMMARY: Done\n",
            returncode=0,
        )

        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/test-project")

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            result = await spawner.spawn("task-1", "do something", config)

        assert result.success is True
        assert result.exit_code == 0
        assert result.parsed["result"] == "SUCCESS"
        assert result.parsed["files_changed"] == 2
        assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_failed_spawn(self):
        proc = _mock_process(
            stdout="RESULT: FAILURE\nSUMMARY: Tests failed\n",
            stderr="error: test failure",
            returncode=1,
        )

        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/test-project")

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            result = await spawner.spawn("task-2", "do something", config)

        assert result.success is False
        assert result.exit_code == 1
        assert "Tests failed" in result.parsed.get("summary", "")

    @pytest.mark.asyncio
    async def test_timeout(self):
        proc = _mock_process()
        # Make communicate hang until killed
        proc.communicate = AsyncMock(side_effect=TimeoutError)

        spawner = CCSpawner(default_timeout=1)
        config = ProjectConfig(project_path="/tmp/test-project")

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            result = await spawner.spawn("task-3", "do something", config)

        assert result.success is False
        assert result.timed_out is True
        assert "Timed out" in result.stderr

    @pytest.mark.asyncio
    async def test_spawn_exception(self):
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/test-project")

        with patch(
            "asyncio.create_subprocess_shell",
            side_effect=OSError("command not found"),
        ):
            result = await spawner.spawn("task-4", "prompt", config)

        assert result.success is False
        assert "command not found" in result.stderr

    @pytest.mark.asyncio
    async def test_build_command_includes_flags(self):
        proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)

        spawner = CCSpawner(cc_binary="/usr/local/bin/claude")
        config = ProjectConfig(project_path="/tmp/proj")

        with patch("asyncio.create_subprocess_shell", return_value=proc) as mock_shell:
            await spawner.spawn("task-5", "hello", config)

        call_args = mock_shell.call_args
        command = call_args[0][0]
        assert "/usr/local/bin/claude" in command
        assert "--print" in command
        assert "--output-format" in command
        assert "json" in command
        assert "--dangerously-skip-permissions" in command

    @pytest.mark.asyncio
    async def test_capacity_tracking(self):
        spawner = CCSpawner(max_parallel=2)
        assert spawner.has_capacity is True
        assert spawner.running_count == 0

        # Simulate adding a running process
        spawner._running["task-a"] = MagicMock()
        assert spawner.running_count == 1
        assert spawner.has_capacity is True

        spawner._running["task-b"] = MagicMock()
        assert spawner.running_count == 2
        assert spawner.has_capacity is False

        spawner._running.pop("task-a")
        assert spawner.has_capacity is True


# ---------------------------------------------------------------------------
# Tests: kill
# ---------------------------------------------------------------------------


class TestKill:
    @pytest.mark.asyncio
    async def test_kill_running_process(self):
        proc = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        spawner = CCSpawner()
        spawner._running["task-k"] = proc

        await spawner.kill("task-k")

        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()
        assert "task-k" not in spawner._running

    @pytest.mark.asyncio
    async def test_kill_nonexistent(self):
        spawner = CCSpawner()
        await spawner.kill("not-found")  # Should not raise


# ---------------------------------------------------------------------------
# Tests: worktree
# ---------------------------------------------------------------------------


class TestWorktree:
    def test_worktree_path(self):
        spawner = CCSpawner()
        path = spawner._worktree_path("/repo", "task-123")
        assert path == "/repo/.leankit-worktrees/task-123"

    @patch("subprocess.run")
    def test_create_worktree_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        spawner = CCSpawner()
        with patch("pathlib.Path.exists", return_value=False), \
             patch("pathlib.Path.mkdir"):
            path, err = spawner.create_worktree("/repo", "task-w1")

        assert err is None
        assert "task-w1" in path

        # Verify git command
        args = mock_run.call_args[0][0]
        assert "worktree" in args
        assert "add" in args

    @patch("subprocess.run")
    def test_create_worktree_already_exists(self, mock_run):
        spawner = CCSpawner()
        with patch("pathlib.Path.exists", return_value=True):
            path, err = spawner.create_worktree("/repo", "task-w2")

        assert err is None
        assert path is not None
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_remove_worktree(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        spawner = CCSpawner()
        ok, err = spawner.remove_worktree("/repo", "task-w3")

        assert ok is True
        assert err is None

    @pytest.mark.asyncio
    async def test_spawn_with_worktree_isolation(self):
        proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)

        spawner = CCSpawner()
        config = ProjectConfig(
            project_path="/repo",
            isolation="git-worktree",
        )

        with patch.object(spawner, "create_worktree", return_value=("/repo/.leankit-worktrees/task-iso", None)), \
             patch("asyncio.create_subprocess_shell", return_value=proc) as mock_shell:
            result = await spawner.spawn("task-iso", "prompt", config)

        assert result.success is True
        # Verify cwd was set to worktree path
        call_kwargs = mock_shell.call_args[1]
        assert call_kwargs["cwd"] == "/repo/.leankit-worktrees/task-iso"

    @pytest.mark.asyncio
    async def test_spawn_worktree_failure(self):
        spawner = CCSpawner()
        config = ProjectConfig(
            project_path="/repo",
            isolation="git-worktree",
        )

        with patch.object(spawner, "create_worktree", return_value=(None, "git error")):
            result = await spawner.spawn("task-bad", "prompt", config)

        assert result.success is False
        assert "git error" in result.stderr
