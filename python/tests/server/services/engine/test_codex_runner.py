"""Tests for CodexRunnerAdapter streaming and process tracking."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.codex_runner import CodexRunnerAdapter


class TestCodexRunnerAdapter:
    @pytest.mark.asyncio
    async def test_get_process_returns_tracked_process(self):
        runner = CodexRunnerAdapter()
        process = MagicMock()
        runner._running["task-001"] = process

        assert runner.get_process("task-001") is process
        assert runner.get_process("missing-task") is None

    @pytest.mark.asyncio
    async def test_stream_stdout_handles_oversized_json_lines(self):
        runner = CodexRunnerAdapter()
        runner._hook_publisher = MagicMock()
        reader = asyncio.StreamReader(limit=64)
        long_message = "x" * 200_000
        payload = json.dumps({"type": "assistant", "message": long_message}) + "\n"
        reader.feed_data(payload.encode())
        reader.feed_eof()
        process = SimpleNamespace(stdout=reader)
        transcript_lines: list[str] = []
        on_stream_event = AsyncMock()

        await asyncio.wait_for(
            runner._stream_stdout(
                process=process,
                task_id="task-001",
                transcript_lines=transcript_lines,
                runtime_metadata={"source_app": "codex-cli"},
                model="codex-default",
                on_stream_event=on_stream_event,
            ),
            timeout=1,
        )

        assert transcript_lines == [long_message]
        on_stream_event.assert_awaited()
        runner._hook_publisher.publish.assert_called_once()
