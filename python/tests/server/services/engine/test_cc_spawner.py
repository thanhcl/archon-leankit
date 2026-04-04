"""Tests for CCSpawner — spawn, parse, timeout, worktree, model routing, streaming."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.cc_spawner import (
    CCSpawner,
    MODEL_DEFAULT,
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
    ProjectConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_process(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Create a mock asyncio.subprocess.Process with line-by-line stdout reading."""
    proc = AsyncMock()

    # Mock stdin
    proc.stdin = AsyncMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()

    # Mock stdout as async readline iterator
    # readline returns each line with \n, empty bytes b"" at EOF
    line_queue = [line.encode() + b"\n" for line in stdout.split("\n") if line] + [b""]

    proc.stdout = AsyncMock()
    proc.stdout.readline = AsyncMock(side_effect=line_queue)

    # Mock stderr
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=stderr.encode())

    proc.returncode = returncode
    proc.wait = AsyncMock()
    proc.kill = MagicMock()

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
        assert parsed == {"learnings": [], "code_patterns": []}

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

    def test_extracts_llm_metrics_from_json_result_event(self):
        import json
        result_event = json.dumps({
            "type": "result",
            "is_error": False,
            "total_cost_usd": 0.0450,
            "usage": {
                "input_tokens": 8000,
                "output_tokens": 4000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 3000,
            },
        })
        stdout = "RESULT: SUCCESS\nSUMMARY: Done\n" + result_event + "\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["llm_cost_usd"] == 0.0450
        assert parsed["llm_input_tokens"] == 8000
        assert parsed["llm_output_tokens"] == 4000
        # total = input + output + cache_creation + cache_read = 8000+4000+0+3000
        assert parsed["llm_total_tokens"] == 15000
        assert "llm_thinking_tokens" not in parsed

    def test_extracts_thinking_tokens_when_present(self):
        import json
        result_event = json.dumps({
            "type": "result",
            "is_error": False,
            "total_cost_usd": 0.10,
            "usage": {
                "input_tokens": 5000,
                "output_tokens": 2000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "thinking_input_tokens": 1500,
            },
        })
        stdout = "RESULT: SUCCESS\n" + result_event + "\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["llm_thinking_tokens"] == 1500
        assert parsed["llm_total_tokens"] == 7000  # 5000+2000+0+0

    def test_llm_metrics_absent_when_no_result_event(self):
        stdout = "RESULT: SUCCESS\nSUMMARY: Done\n"
        parsed = CCSpawner.parse_result(stdout)
        assert "llm_cost_usd" not in parsed
        assert "llm_input_tokens" not in parsed
        assert "llm_total_tokens" not in parsed

    def test_llm_metrics_only_uses_first_result_event(self):
        import json
        first = json.dumps({"type": "result", "total_cost_usd": 0.01, "usage": {"input_tokens": 100, "output_tokens": 50}})
        second = json.dumps({"type": "result", "total_cost_usd": 0.99, "usage": {"input_tokens": 9000, "output_tokens": 9000}})
        stdout = first + "\n" + second + "\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["llm_cost_usd"] == 0.01
        assert parsed["llm_input_tokens"] == 100


# ---------------------------------------------------------------------------
# Tests: parse_stream_line
# ---------------------------------------------------------------------------


class TestParseStreamLine:
    def test_assistant_message(self):
        import json
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Working on the task"}]},
        })
        result = CCSpawner.parse_stream_line(line)
        assert result is not None
        assert result["event"] == "assistant"
        assert result["message"] == "Working on the task"

    def test_assistant_string_content(self):
        import json
        line = json.dumps({
            "type": "assistant",
            "message": {"content": "Simple text", "text": "Simple text"},
        })
        result = CCSpawner.parse_stream_line(line)
        assert result is not None
        assert result["message"] == "Simple text"

    def test_tool_use_with_file_path(self):
        import json
        line = json.dumps({
            "type": "tool_use",
            "tool": {"name": "Read", "input": {"file_path": "/src/main.py"}},
        })
        result = CCSpawner.parse_stream_line(line)
        assert result is not None
        assert result["event"] == "tool_use"
        assert result["tool_name"] == "Read"
        assert "/src/main.py" in result["args_summary"]

    def test_tool_use_with_command(self):
        import json
        line = json.dumps({
            "type": "tool_use",
            "tool": {"name": "Bash", "input": {"command": "npm test"}},
        })
        result = CCSpawner.parse_stream_line(line)
        assert result is not None
        assert result["tool_name"] == "Bash"
        assert "npm test" in result["args_summary"]

    def test_tool_result(self):
        import json
        line = json.dumps({
            "type": "tool_result",
            "content": "File contents here...",
        })
        result = CCSpawner.parse_stream_line(line)
        assert result is not None
        assert result["event"] == "tool_result"
        assert "File contents" in result["output_summary"]

    def test_tool_result_truncated(self):
        import json
        line = json.dumps({
            "type": "tool_result",
            "content": "x" * 500,
        })
        result = CCSpawner.parse_stream_line(line)
        assert result is not None
        assert len(result["output_summary"]) <= 200

    def test_thinking(self):
        import json
        line = json.dumps({
            "type": "thinking",
            "thinking": "Let me analyze the code structure",
        })
        result = CCSpawner.parse_stream_line(line)
        assert result is not None
        assert result["event"] == "thinking"
        assert "analyze" in result["message"]

    def test_thinking_truncated(self):
        import json
        line = json.dumps({
            "type": "thinking",
            "thinking": "y" * 500,
        })
        result = CCSpawner.parse_stream_line(line)
        assert result is not None
        assert len(result["message"]) <= 300

    def test_unknown_type(self):
        import json
        line = json.dumps({"type": "system", "data": {}})
        result = CCSpawner.parse_stream_line(line)
        assert result is None

    def test_invalid_json(self):
        result = CCSpawner.parse_stream_line("not json at all")
        assert result is None

    def test_empty_line(self):
        result = CCSpawner.parse_stream_line("")
        assert result is None

    def test_non_dict_json(self):
        result = CCSpawner.parse_stream_line("[1, 2, 3]")
        assert result is None

    def test_assistant_empty_message(self):
        import json
        line = json.dumps({"type": "assistant", "message": {"content": []}})
        result = CCSpawner.parse_stream_line(line)
        assert result is None

    def test_tool_use_no_name(self):
        import json
        line = json.dumps({"type": "tool_use", "tool": {"input": {}}})
        result = CCSpawner.parse_stream_line(line)
        assert result is None

    def test_tool_result_content_blocks(self):
        import json
        line = json.dumps({
            "type": "tool_result",
            "content": [
                {"type": "text", "text": "First block"},
                {"type": "text", "text": "Second block"},
            ],
        })
        result = CCSpawner.parse_stream_line(line)
        assert result is not None
        assert "First block" in result["output_summary"]

    def test_result_event_parsed(self):
        import json
        line = json.dumps({
            "type": "result",
            "is_error": False,
            "total_cost_usd": 0.0450,
            "usage": {"input_tokens": 5000, "output_tokens": 2000},
        })
        result = CCSpawner.parse_stream_line(line)
        assert result is not None
        assert result["event"] == "result"
        assert result["is_error"] is False
        assert result["total_cost_usd"] == 0.0450
        assert result["usage"]["input_tokens"] == 5000

    def test_result_event_error(self):
        import json
        line = json.dumps({"type": "result", "is_error": True, "total_cost_usd": None, "usage": None})
        result = CCSpawner.parse_stream_line(line)
        assert result is not None
        assert result["event"] == "result"
        assert result["is_error"] is True


# ---------------------------------------------------------------------------
# Tests: spawn (with streaming)
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
        # Use high priority to select Opus directly (avoids fallback retry)
        task = {"complexity": "complex", "priority": "high"}

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            result = await spawner.spawn("task-2", "do something", config, task=task)

        assert result.success is False
        assert result.exit_code == 1
        assert "Tests failed" in result.parsed.get("summary", "")

    @pytest.mark.asyncio
    async def test_timeout(self):
        import asyncio as _asyncio

        proc = _mock_process()
        # Make wait() hang so asyncio.wait_for triggers timeout
        async def _hang():
            await _asyncio.sleep(9999)
        proc.wait = _hang
        # kill -> wait also needs to complete cleanly
        proc.kill = MagicMock()
        original_wait = proc.wait

        async def _kill_wait():
            pass
        # After kill, wait should return immediately
        killed = False

        async def _wait_or_hang():
            nonlocal killed
            if killed:
                return
            await _asyncio.sleep(9999)

        proc.wait = _wait_or_hang

        spawner = CCSpawner(default_timeout=1)
        config = ProjectConfig(project_path="/tmp/test-project")
        # Use Opus to avoid fallback retry
        task = {"complexity": "complex", "priority": "high"}

        # Patch spawner.kill to set killed flag
        original_kill = spawner.kill
        async def _mock_kill(task_id):
            nonlocal killed
            killed = True
            spawner._running.pop(task_id, None)

        with patch("asyncio.create_subprocess_shell", return_value=proc), \
             patch.object(spawner, "kill", side_effect=_mock_kill):
            result = await spawner.spawn("task-3", "do something", config, task=task)

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

    @pytest.mark.asyncio
    async def test_stream_callback_called(self):
        """Verify on_stream_event callback fires for JSON lines."""
        import json

        json_line = json.dumps({
            "type": "tool_use",
            "tool": {"name": "Read", "input": {"file_path": "/src/app.py"}},
        })
        proc = _mock_process(stdout=json_line + "\nRESULT: SUCCESS\n", returncode=0)

        received_events = []

        async def on_stream(task_id, event):
            received_events.append((task_id, event))

        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            result = await spawner.spawn("task-stream", "prompt", config, on_stream_event=on_stream)

        assert result.success is True
        assert len(received_events) >= 1
        tid, evt = received_events[0]
        assert tid == "task-stream"
        assert evt["event"] == "tool_use"
        assert evt["tool_name"] == "Read"

    @pytest.mark.asyncio
    async def test_stream_callback_error_non_fatal(self):
        """Stream callback errors should not crash the spawn."""
        import json

        json_line = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        })
        proc = _mock_process(stdout=json_line + "\nRESULT: SUCCESS\n", returncode=0)

        async def failing_callback(task_id, event):
            raise ValueError("callback broke")

        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            result = await spawner.spawn("task-err", "prompt", config, on_stream_event=failing_callback)

        # Should still succeed despite callback error
        assert result.success is True

    @pytest.mark.asyncio
    async def test_no_callback_still_works(self):
        """Spawn works fine without a stream callback."""
        proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)

        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            result = await spawner.spawn("task-nocb", "prompt", config, on_stream_event=None)

        assert result.success is True


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
        from src.server.services.engine.sandbox_provider import SandboxContext

        proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)

        spawner = CCSpawner()
        config = ProjectConfig(
            project_path="/repo",
            isolation="git-worktree",
        )

        mock_provider = MagicMock()
        mock_provider.acquire.return_value = SandboxContext(
            workspace_path="/repo/.leankit-worktrees/task-iso", provider_name="git-worktree"
        )

        with patch("src.server.services.engine.cc_spawner.get_provider_for_isolation", return_value=mock_provider), \
             patch("asyncio.create_subprocess_shell", return_value=proc) as mock_shell:
            result = await spawner.spawn("task-iso", "prompt", config)

        assert result.success is True
        # Verify cwd was set to worktree path
        call_kwargs = mock_shell.call_args[1]
        assert call_kwargs["cwd"] == "/repo/.leankit-worktrees/task-iso"

    @pytest.mark.asyncio
    async def test_spawn_worktree_failure(self):
        from src.server.services.engine.sandbox_provider import SandboxContext

        spawner = CCSpawner()
        config = ProjectConfig(
            project_path="/repo",
            isolation="git-worktree",
        )

        mock_provider = MagicMock()
        mock_provider.acquire.side_effect = RuntimeError("git error")

        with patch("src.server.services.engine.cc_spawner.get_provider_for_isolation", return_value=mock_provider):
            result = await spawner.spawn("task-bad", "prompt", config)

        assert result.success is False
        assert "git error" in result.stderr


# ---------------------------------------------------------------------------
# Tests: model routing (select_model)
# ---------------------------------------------------------------------------


class TestModelRouting:
    def test_default_no_task(self):
        assert CCSpawner.select_model() == MODEL_DEFAULT

    def test_default_empty_task(self):
        assert CCSpawner.select_model(task={}) == MODEL_DEFAULT

    def test_complex_task_uses_opus(self):
        task = {"complexity": "complex", "priority": "medium"}
        assert CCSpawner.select_model(task=task) == MODEL_OPUS

    def test_high_priority_uses_opus(self):
        task = {"complexity": "medium", "priority": "high"}
        assert CCSpawner.select_model(task=task) == MODEL_OPUS

    def test_critical_priority_uses_opus(self):
        task = {"complexity": "simple", "priority": "critical"}
        assert CCSpawner.select_model(task=task) == MODEL_OPUS

    def test_simple_low_uses_haiku(self):
        task = {"complexity": "simple", "priority": "low"}
        assert CCSpawner.select_model(task=task) == MODEL_HAIKU

    def test_medium_uses_sonnet(self):
        task = {"complexity": "medium", "priority": "medium"}
        assert CCSpawner.select_model(task=task) == MODEL_SONNET

    def test_simple_medium_priority_uses_sonnet(self):
        """Simple complexity but medium priority → Sonnet (not Haiku)."""
        task = {"complexity": "simple", "priority": "medium"}
        assert CCSpawner.select_model(task=task) == MODEL_SONNET

    def test_force_model_overrides(self):
        task = {"complexity": "complex", "priority": "high"}
        assert CCSpawner.select_model(task=task, force_model="custom-model") == "custom-model"

    def test_force_model_without_task(self):
        assert CCSpawner.select_model(force_model=MODEL_HAIKU) == MODEL_HAIKU

    def test_missing_complexity_defaults_medium(self):
        task = {"priority": "low"}
        assert CCSpawner.select_model(task=task) == MODEL_SONNET

    def test_missing_priority_defaults_medium(self):
        task = {"complexity": "simple"}
        assert CCSpawner.select_model(task=task) == MODEL_SONNET

    def test_case_insensitive(self):
        task = {"complexity": "Complex", "priority": "HIGH"}
        assert CCSpawner.select_model(task=task) == MODEL_OPUS


# ---------------------------------------------------------------------------
# Tests: stage-aware model routing (B-P3-05)
# ---------------------------------------------------------------------------


class TestStageAwareModelRouting:
    """Verify that review stages enforce minimum model tiers regardless of task metadata."""

    def test_code_review_always_sonnet_even_for_simple_low(self):
        """Root cause fix: simple/low task in code-review must NOT get haiku."""
        task = {"complexity": "simple", "priority": "low"}
        assert CCSpawner.select_model(task=task, stage="code-review") == MODEL_SONNET

    def test_code_review_returns_sonnet_for_medium_task(self):
        task = {"complexity": "medium", "priority": "medium"}
        assert CCSpawner.select_model(task=task, stage="code-review") == MODEL_SONNET

    def test_code_review_returns_sonnet_for_complex_task(self):
        """code-review caps at sonnet — opus is for architect-review only."""
        task = {"complexity": "complex", "priority": "critical"}
        assert CCSpawner.select_model(task=task, stage="code-review") == MODEL_SONNET

    def test_architect_review_always_opus(self):
        task = {"complexity": "simple", "priority": "low"}
        assert CCSpawner.select_model(task=task, stage="architect-review") == MODEL_OPUS

    def test_architect_review_opus_regardless_of_metadata(self):
        task = {"complexity": "medium", "priority": "medium"}
        assert CCSpawner.select_model(task=task, stage="architect-review") == MODEL_OPUS

    def test_execute_stage_preserves_existing_logic_opus(self):
        task = {"complexity": "complex", "priority": "high"}
        assert CCSpawner.select_model(task=task, stage="execute") == MODEL_OPUS

    def test_execute_stage_preserves_existing_logic_haiku(self):
        task = {"complexity": "simple", "priority": "low"}
        assert CCSpawner.select_model(task=task, stage="execute") == MODEL_HAIKU

    def test_execute_stage_preserves_existing_logic_sonnet(self):
        task = {"complexity": "medium", "priority": "medium"}
        assert CCSpawner.select_model(task=task, stage="execute") == MODEL_SONNET

    def test_no_stage_preserves_existing_logic(self):
        """Backward compatibility: no stage param works as before."""
        task = {"complexity": "simple", "priority": "low"}
        assert CCSpawner.select_model(task=task) == MODEL_HAIKU

    def test_force_model_overrides_stage(self):
        task = {"complexity": "simple", "priority": "low"}
        assert CCSpawner.select_model(task=task, stage="code-review", force_model="custom") == "custom"


class TestRetryModelEscalation:
    """Verify retry escalation ladder: codex/haiku → sonnet → opus."""

    def test_retry_1_escalates_haiku_to_sonnet(self):
        task = {"complexity": "simple", "priority": "low"}
        result = CCSpawner.select_model(task=task, stage="retry", retry_count=1, previous_model=MODEL_HAIKU)
        assert result == MODEL_SONNET

    def test_retry_2_escalates_sonnet_to_opus(self):
        task = {"complexity": "medium", "priority": "medium"}
        result = CCSpawner.select_model(task=task, stage="retry", retry_count=2, previous_model=MODEL_SONNET)
        assert result == MODEL_OPUS

    def test_retry_never_downgrades(self):
        task = {"complexity": "simple", "priority": "low"}
        result = CCSpawner.select_model(task=task, retry_count=1, previous_model=MODEL_SONNET)
        assert result >= MODEL_SONNET or result == MODEL_SONNET

    def test_retry_0_no_escalation(self):
        task = {"complexity": "simple", "priority": "low"}
        result = CCSpawner.select_model(task=task, retry_count=0, previous_model=MODEL_HAIKU)
        assert result == MODEL_HAIKU


# ---------------------------------------------------------------------------
# Tests: model in spawn command
# ---------------------------------------------------------------------------


class TestModelInCommand:
    def test_build_command_includes_model(self):
        spawner = CCSpawner()
        command, _ = spawner._build_command("prompt", model=MODEL_OPUS)
        assert f"--model {MODEL_OPUS}" in command

    def test_build_command_no_model(self):
        spawner = CCSpawner()
        command, _ = spawner._build_command("prompt")
        assert "--model" not in command

    @pytest.mark.asyncio
    async def test_spawn_uses_selected_model(self):
        proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")
        task = {"complexity": "complex", "priority": "high"}

        with patch("asyncio.create_subprocess_shell", return_value=proc) as mock_shell:
            result = await spawner.spawn("task-m1", "prompt", config, task=task)

        command = mock_shell.call_args[0][0]
        assert f"--model {MODEL_OPUS}" in command
        assert result.parsed["model_used"] == MODEL_OPUS

    @pytest.mark.asyncio
    async def test_spawn_with_force_model(self):
        proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj", force_model="my-custom-model")
        task = {"complexity": "simple", "priority": "low"}

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            result = await spawner.spawn("task-m2", "prompt", config, task=task)

        assert result.parsed["model_used"] == "my-custom-model"

    @pytest.mark.asyncio
    async def test_model_logged_in_parsed(self):
        proc = _mock_process(stdout="RESULT: SUCCESS\nFILES_CHANGED: 1\n", returncode=0)
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            result = await spawner.spawn("task-m3", "prompt", config)

        assert "model_used" in result.parsed
        assert result.parsed["model_used"] == MODEL_SONNET


# ---------------------------------------------------------------------------
# Tests: fallback retry with Opus
# ---------------------------------------------------------------------------


class TestFallbackRetry:
    @pytest.mark.asyncio
    async def test_fallback_to_opus_on_failure(self):
        """Non-Opus model fails → retry with Opus."""
        fail_proc = _mock_process(stdout="RESULT: FAILURE\n", returncode=1)
        success_proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)

        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")
        task = {"complexity": "simple", "priority": "low"}  # Would pick Haiku

        call_count = 0

        async def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return fail_proc if call_count == 1 else success_proc

        with patch("asyncio.create_subprocess_shell", side_effect=mock_create) as mock_shell:
            result = await spawner.spawn("task-fb", "prompt", config, task=task)

        assert call_count == 2
        assert result.success is True
        assert result.parsed["fallback_from"] == MODEL_HAIKU
        assert result.parsed["fallback_to"] == MODEL_OPUS

        # Verify first call used Haiku, second used Opus
        first_cmd = mock_shell.call_args_list[0][0][0]
        second_cmd = mock_shell.call_args_list[1][0][0]
        assert f"--model {MODEL_HAIKU}" in first_cmd
        assert f"--model {MODEL_OPUS}" in second_cmd

    @pytest.mark.asyncio
    async def test_no_fallback_when_opus_fails(self):
        """If Opus itself fails, no fallback retry."""
        fail_proc = _mock_process(stdout="RESULT: FAILURE\n", returncode=1)

        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")
        task = {"complexity": "complex", "priority": "high"}  # Picks Opus

        with patch("asyncio.create_subprocess_shell", return_value=fail_proc) as mock_shell:
            result = await spawner.spawn("task-nf", "prompt", config, task=task)

        # Should only be called once — no fallback since already Opus
        assert mock_shell.call_count == 1
        assert result.success is False
        assert "fallback_from" not in result.parsed

    @pytest.mark.asyncio
    async def test_no_fallback_on_success(self):
        """Successful spawn should not trigger fallback."""
        proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)

        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")
        task = {"complexity": "simple", "priority": "low"}

        with patch("asyncio.create_subprocess_shell", return_value=proc) as mock_shell:
            result = await spawner.spawn("task-ns", "prompt", config, task=task)

        assert mock_shell.call_count == 1
        assert result.success is True
        assert "fallback_from" not in result.parsed


# ---------------------------------------------------------------------------
# Tests: runner env allowlist filtering
# ---------------------------------------------------------------------------


class TestRunnerEnvAllowlist:
    @pytest.mark.asyncio
    async def test_allowlisted_token_profile_keys_injected(self):
        """Vars in the allowlist are injected into the subprocess env."""
        proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")
        token_profile = {"max_tokens": 8000}

        captured_env = {}

        async def capture_env(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return proc

        # LEANKIT_RUNNER_MAX_TOKENS is not in the default allowlist, so we use a custom one
        with patch("asyncio.create_subprocess_shell", side_effect=capture_env), \
             patch(
                 "src.server.services.engine.cc_spawner.get_runner_env_allowlist",
                 return_value=["LEANKIT_RUNNER_MAX_TOKENS"],
             ):
            result = await spawner.spawn("task-al1", "prompt", config, token_profile=token_profile)

        assert "LEANKIT_RUNNER_MAX_TOKENS" in captured_env
        assert captured_env["LEANKIT_RUNNER_MAX_TOKENS"] == "8000"
        assert result.parsed["injected_env_keys"] == ["LEANKIT_RUNNER_MAX_TOKENS"]

    @pytest.mark.asyncio
    async def test_non_allowlisted_token_profile_keys_blocked(self):
        """Vars not in the allowlist are blocked and not injected."""
        proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")
        token_profile = {"max_tokens": 8000, "temperature_pct": 50}

        captured_env = {}

        async def capture_env(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return proc

        # Only LEANKIT_RUNNER_MAX_TOKENS is allowed; LEANKIT_RUNNER_TEMPERATURE_PCT is not
        with patch("asyncio.create_subprocess_shell", side_effect=capture_env), \
             patch(
                 "src.server.services.engine.cc_spawner.get_runner_env_allowlist",
                 return_value=["LEANKIT_RUNNER_MAX_TOKENS"],
             ):
            result = await spawner.spawn("task-al2", "prompt", config, token_profile=token_profile)

        assert "LEANKIT_RUNNER_MAX_TOKENS" in captured_env
        assert "LEANKIT_RUNNER_TEMPERATURE_PCT" not in captured_env
        assert result.parsed["injected_env_keys"] == ["LEANKIT_RUNNER_MAX_TOKENS"]

    @pytest.mark.asyncio
    async def test_empty_allowlist_blocks_all_injections(self):
        """An empty allowlist prevents all token profile vars from being injected."""
        proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")
        token_profile = {"max_tokens": 8000, "temperature_pct": 50, "model_hint": "fast"}

        captured_env = {}

        async def capture_env(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return proc

        with patch("asyncio.create_subprocess_shell", side_effect=capture_env), \
             patch(
                 "src.server.services.engine.cc_spawner.get_runner_env_allowlist",
                 return_value=[],
             ):
            result = await spawner.spawn("task-al3", "prompt", config, token_profile=token_profile)

        assert "LEANKIT_RUNNER_MAX_TOKENS" not in captured_env
        assert "LEANKIT_RUNNER_TEMPERATURE_PCT" not in captured_env
        assert "LEANKIT_RUNNER_MODEL_HINT" not in captured_env
        assert result.parsed["injected_env_keys"] == []

    @pytest.mark.asyncio
    async def test_no_token_profile_means_empty_injected_keys(self):
        """When no token profile is provided, injected_env_keys is empty."""
        proc = _mock_process(stdout="RESULT: SUCCESS\n", returncode=0)
        spawner = CCSpawner()
        config = ProjectConfig(project_path="/tmp/proj")

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            result = await spawner.spawn("task-al4", "prompt", config, token_profile=None)

        assert result.parsed["injected_env_keys"] == []

    @pytest.mark.asyncio
    async def test_default_allowlist_contains_expected_vars(self):
        """Default allowlist includes the spec-required variable names."""
        from src.server.config.env_aliases import get_runner_env_allowlist
        allowlist = get_runner_env_allowlist()
        assert "MAX_THINKING_TOKENS" in allowlist
        assert "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE" in allowlist
        assert "CLAUDE_CODE_SUBAGENT_MODEL" in allowlist
