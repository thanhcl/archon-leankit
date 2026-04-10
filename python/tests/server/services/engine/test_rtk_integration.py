"""Tests for RTK integration — setup, analytics, tee, env injection, prompt hints."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.rtk_integration import (
    RTKSessionStats,
    RTKSetupResult,
    _parse_rtk_gain_output,
    cleanup_tee,
    collect_rtk_analytics,
    get_rtk_binary,
    get_rtk_spawn_env,
    get_tee_recovery_path,
    is_rtk_enabled,
    setup_rtk_for_workspace,
)


# ---------------------------------------------------------------------------
# Tests: is_rtk_enabled
# ---------------------------------------------------------------------------


class TestIsRTKEnabled:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("LEANKIT_RTK_ENABLED", raising=False)
        assert is_rtk_enabled() is False

    def test_enabled_true(self, monkeypatch):
        monkeypatch.setenv("LEANKIT_RTK_ENABLED", "true")
        assert is_rtk_enabled() is True

    def test_enabled_1(self, monkeypatch):
        monkeypatch.setenv("LEANKIT_RTK_ENABLED", "1")
        assert is_rtk_enabled() is True

    def test_enabled_yes(self, monkeypatch):
        monkeypatch.setenv("LEANKIT_RTK_ENABLED", "yes")
        assert is_rtk_enabled() is True

    def test_disabled_false(self, monkeypatch):
        monkeypatch.setenv("LEANKIT_RTK_ENABLED", "false")
        assert is_rtk_enabled() is False

    def test_disabled_empty(self, monkeypatch):
        monkeypatch.setenv("LEANKIT_RTK_ENABLED", "")
        assert is_rtk_enabled() is False


# ---------------------------------------------------------------------------
# Tests: get_rtk_binary
# ---------------------------------------------------------------------------


class TestGetRTKBinary:
    def test_explicit_path(self, monkeypatch, tmp_path):
        binary = tmp_path / "rtk"
        binary.touch()
        monkeypatch.setenv("LEANKIT_RTK_BINARY_PATH", str(binary))
        assert get_rtk_binary() == str(binary)

    def test_explicit_path_missing_file(self, monkeypatch):
        monkeypatch.setenv("LEANKIT_RTK_BINARY_PATH", "/nonexistent/rtk")
        assert get_rtk_binary() is None

    def test_fallback_to_which(self, monkeypatch):
        monkeypatch.delenv("LEANKIT_RTK_BINARY_PATH", raising=False)
        with patch("shutil.which", return_value="/usr/local/bin/rtk"):
            assert get_rtk_binary() == "/usr/local/bin/rtk"

    def test_not_found(self, monkeypatch):
        monkeypatch.delenv("LEANKIT_RTK_BINARY_PATH", raising=False)
        with patch("shutil.which", return_value=None):
            assert get_rtk_binary() is None


# ---------------------------------------------------------------------------
# Tests: RTKSessionStats
# ---------------------------------------------------------------------------


class TestRTKSessionStats:
    def test_to_dict_basic(self):
        stats = RTKSessionStats(
            total_commands=42,
            filtered_commands=38,
            tokens_before=118000,
            tokens_after=23900,
            savings_pct=79.746,
        )
        d = stats.to_dict()
        assert d["total_commands"] == 42
        assert d["tokens_before"] == 118000
        assert d["tokens_after"] == 23900
        assert d["savings_pct"] == 79.7
        assert "command_breakdown" not in d

    def test_to_dict_with_breakdown(self):
        stats = RTKSessionStats(
            total_commands=2,
            filtered_commands=2,
            tokens_before=1000,
            tokens_after=200,
            savings_pct=80.0,
            command_breakdown=[{"command": "git status", "savings_pct": 80}],
        )
        d = stats.to_dict()
        assert d["command_breakdown"] == [{"command": "git status", "savings_pct": 80}]


# ---------------------------------------------------------------------------
# Tests: _parse_rtk_gain_output
# ---------------------------------------------------------------------------


class TestParseRTKGainOutput:
    def test_summary_dict_format(self):
        data = {
            "total_commands": 10,
            "filtered_commands": 8,
            "tokens_before": 5000,
            "tokens_after": 1000,
            "savings_pct": 80.0,
        }
        stats = _parse_rtk_gain_output(data)
        assert stats.total_commands == 10
        assert stats.tokens_before == 5000
        assert stats.savings_pct == 80.0

    def test_list_format(self):
        data = [
            {"original_cmd": "git status", "input_tokens": 500, "output_tokens": 100},
            {"original_cmd": "git diff", "input_tokens": 1000, "output_tokens": 250},
        ]
        stats = _parse_rtk_gain_output(data)
        assert stats.total_commands == 2
        assert stats.tokens_before == 1500
        assert stats.tokens_after == 350
        assert stats.savings_pct == pytest.approx(76.67, abs=0.1)
        assert len(stats.command_breakdown) == 2
        assert stats.command_breakdown[0]["command"] == "git status"

    def test_empty_dict(self):
        stats = _parse_rtk_gain_output({})
        assert stats.total_commands == 0
        assert stats.tokens_before == 0

    def test_empty_list(self):
        stats = _parse_rtk_gain_output([])
        assert stats.total_commands == 0


# ---------------------------------------------------------------------------
# Tests: setup_rtk_for_workspace
# ---------------------------------------------------------------------------


class TestSetupRTKForWorkspace:
    @pytest.mark.asyncio
    async def test_disabled_returns_early(self, monkeypatch):
        monkeypatch.setenv("LEANKIT_RTK_ENABLED", "false")
        result = await setup_rtk_for_workspace("/tmp/test-workspace")
        assert result.enabled is False
        assert result.hook_installed is False

    @pytest.mark.asyncio
    async def test_binary_not_found(self, monkeypatch):
        monkeypatch.setenv("LEANKIT_RTK_ENABLED", "true")
        monkeypatch.delenv("LEANKIT_RTK_BINARY_PATH", raising=False)
        with patch("shutil.which", return_value=None):
            result = await setup_rtk_for_workspace("/tmp/test-workspace")
        assert result.enabled is False
        assert "not found" in (result.error or "")

    @pytest.mark.asyncio
    async def test_successful_setup(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LEANKIT_RTK_ENABLED", "true")
        workspace = str(tmp_path / "workspace")
        os.makedirs(workspace)

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_proc.returncode = 0

        with (
            patch("shutil.which", return_value="/usr/local/bin/rtk"),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            result = await setup_rtk_for_workspace(workspace)

        assert result.enabled is True
        assert result.hook_installed is True
        assert result.binary_path == "/usr/local/bin/rtk"
        assert result.tee_dir is not None
        assert os.path.isdir(result.tee_dir)

    @pytest.mark.asyncio
    async def test_init_failure_non_fatal(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LEANKIT_RTK_ENABLED", "true")
        workspace = str(tmp_path / "workspace")
        os.makedirs(workspace)

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_proc.returncode = 1

        with (
            patch("shutil.which", return_value="/usr/local/bin/rtk"),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            result = await setup_rtk_for_workspace(workspace)

        assert result.enabled is True
        assert result.hook_installed is False  # Failed but non-fatal


# ---------------------------------------------------------------------------
# Tests: collect_rtk_analytics
# ---------------------------------------------------------------------------


class TestCollectRTKAnalytics:
    @pytest.mark.asyncio
    async def test_successful_collection(self):
        gain_output = json.dumps({
            "total_commands": 20,
            "filtered_commands": 18,
            "tokens_before": 50000,
            "tokens_after": 10000,
            "savings_pct": 80.0,
        })

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(gain_output.encode(), b"")
        )
        mock_proc.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            stats = await collect_rtk_analytics("/usr/local/bin/rtk")

        assert stats is not None
        assert stats.total_commands == 20
        assert stats.savings_pct == 80.0

    @pytest.mark.asyncio
    async def test_no_binary_returns_none(self):
        stats = await collect_rtk_analytics(None)
        assert stats is None

    @pytest.mark.asyncio
    async def test_process_failure_returns_none(self):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_proc.returncode = 1

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            stats = await collect_rtk_analytics("/usr/local/bin/rtk")

        assert stats is None

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"not json", b""))
        mock_proc.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            stats = await collect_rtk_analytics("/usr/local/bin/rtk")

        assert stats is None


# ---------------------------------------------------------------------------
# Tests: tee recovery
# ---------------------------------------------------------------------------


class TestTeeRecovery:
    def test_tee_path_with_files(self, tmp_path):
        tee_dir = str(tmp_path / ".rtk-tee")
        os.makedirs(tee_dir)
        (tmp_path / ".rtk-tee" / "output.log").write_text("full output")
        assert get_tee_recovery_path(tee_dir) == tee_dir

    def test_tee_path_empty_dir(self, tmp_path):
        tee_dir = str(tmp_path / ".rtk-tee")
        os.makedirs(tee_dir)
        assert get_tee_recovery_path(tee_dir) is None

    def test_tee_path_nonexistent(self):
        assert get_tee_recovery_path("/nonexistent") is None

    def test_tee_path_none(self):
        assert get_tee_recovery_path(None) is None

    def test_cleanup_success_run(self, tmp_path):
        tee_dir = str(tmp_path / ".rtk-tee")
        os.makedirs(tee_dir)
        (tmp_path / ".rtk-tee" / "output.log").write_text("data")

        cleanup_tee(tee_dir, keep_on_failure=True, run_success=True)
        assert not os.path.exists(tee_dir)

    def test_cleanup_failed_run_kept(self, tmp_path):
        tee_dir = str(tmp_path / ".rtk-tee")
        os.makedirs(tee_dir)
        (tmp_path / ".rtk-tee" / "output.log").write_text("data")

        cleanup_tee(tee_dir, keep_on_failure=True, run_success=False)
        assert os.path.exists(tee_dir)  # Retained for debugging


# ---------------------------------------------------------------------------
# Tests: RTK spawn environment
# ---------------------------------------------------------------------------


class TestRTKSpawnEnv:
    def test_basic_env(self, monkeypatch):
        monkeypatch.delenv("LEANKIT_RTK_CONFIG_PATH", raising=False)
        env = get_rtk_spawn_env()
        assert env["RTK_TRACKING"] == "true"
        assert "RTK_CONFIG_PATH" not in env

    def test_with_config_path(self, monkeypatch):
        monkeypatch.setenv("LEANKIT_RTK_CONFIG_PATH", "/etc/rtk/config.toml")
        env = get_rtk_spawn_env()
        assert env["RTK_CONFIG_PATH"] == "/etc/rtk/config.toml"
        assert env["RTK_TRACKING"] == "true"


# ---------------------------------------------------------------------------
# Tests: Context efficiency hints in PromptBuilder
# ---------------------------------------------------------------------------


class TestContextEfficiencyHints:
    """Test the _context_efficiency_hints method on PromptBuilder."""

    def _make_task(self, **overrides):
        base = {
            "id": "task-001",
            "title": "Test task",
            "description": "Test description",
            "retry_count": 0,
            "allowed_paths": [],
            "task_type": "implementation",
        }
        base.update(overrides)
        return base

    def test_no_hints_for_simple_task(self):
        from src.server.services.engine.prompt_builder import PromptBuilder

        task = self._make_task(task_type="other", description="setup config")
        result = PromptBuilder._context_efficiency_hints(task)
        # May or may not return hints depending on keywords
        # For a generic task with no special conditions, should be None or minimal
        # Actually "other" task_type and no retry, no large scope → no hints
        assert result is None

    def test_retry_hint(self):
        from src.server.services.engine.prompt_builder import PromptBuilder

        task = self._make_task(retry_count=2)
        result = PromptBuilder._context_efficiency_hints(task)
        assert result is not None
        assert "Retry efficiency" in result
        assert "error messages" in result

    def test_large_scope_hint(self):
        from src.server.services.engine.prompt_builder import PromptBuilder

        task = self._make_task(allowed_paths=[f"src/file{i}.ts" for i in range(25)])
        result = PromptBuilder._context_efficiency_hints(task)
        assert result is not None
        assert "Large scope" in result

    def test_test_execution_hint(self):
        from src.server.services.engine.prompt_builder import PromptBuilder

        task = self._make_task(task_type="bug", description="fix login test failure")
        result = PromptBuilder._context_efficiency_hints(task)
        assert result is not None
        assert "Test output" in result

    def test_research_task_hint(self):
        from src.server.services.engine.prompt_builder import PromptBuilder

        task = self._make_task(task_type="research")
        result = PromptBuilder._context_efficiency_hints(task)
        assert result is not None
        assert "Exploration efficiency" in result

    def test_combined_hints(self):
        from src.server.services.engine.prompt_builder import PromptBuilder

        task = self._make_task(
            retry_count=1,
            allowed_paths=[f"path{i}" for i in range(30)],
            task_type="bug",
        )
        result = PromptBuilder._context_efficiency_hints(task)
        assert result is not None
        assert "Retry efficiency" in result
        assert "Large scope" in result
        assert "Test output" in result


# ---------------------------------------------------------------------------
# Tests: Lifecycle hooks RTK metadata
# ---------------------------------------------------------------------------


class TestLifecycleHooksRTK:
    def test_capture_run_stop_with_rtk_stats(self):
        from src.server.services.engine.lifecycle_hooks import LifecycleHooks

        runner_result = MagicMock()
        runner_result.success = True
        runner_result.exit_code = 0
        runner_result.duration_seconds = 120.5
        runner_result.timed_out = False
        runner_result.parsed = {
            "rtk_session_stats": {
                "total_commands": 20,
                "tokens_before": 50000,
                "tokens_after": 10000,
                "savings_pct": 80.0,
            }
        }

        context = LifecycleHooks.capture_run_stop(runner_result=runner_result)
        assert "rtk_session_stats" in context
        assert context["rtk_session_stats"]["savings_pct"] == 80.0

    def test_capture_run_stop_with_rtk_tee(self):
        from src.server.services.engine.lifecycle_hooks import LifecycleHooks

        runner_result = MagicMock()
        runner_result.success = False
        runner_result.exit_code = 1
        runner_result.duration_seconds = 60.0
        runner_result.timed_out = False
        runner_result.parsed = {
            "rtk_tee_path": "/workspace/.rtk-tee",
        }

        context = LifecycleHooks.capture_run_stop(runner_result=runner_result)
        assert context["rtk_tee_path"] == "/workspace/.rtk-tee"

    def test_capture_run_stop_without_rtk(self):
        from src.server.services.engine.lifecycle_hooks import LifecycleHooks

        runner_result = MagicMock()
        runner_result.success = True
        runner_result.exit_code = 0
        runner_result.duration_seconds = 30.0
        runner_result.timed_out = False
        runner_result.parsed = {"model_used": "sonnet"}

        context = LifecycleHooks.capture_run_stop(runner_result=runner_result)
        assert "rtk_session_stats" not in context
        assert "rtk_tee_path" not in context
