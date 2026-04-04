"""Tests for Claude Code internals adoption gaps.

Covers: sub-agent cost rollup, guidance pack budget, recovery events,
guidance pack versioning, token breakdown logging.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.coordinator_service import CoordinatorService
from src.server.services.engine.prompt_builder import PromptBuilder


# ---------------------------------------------------------------------------
# Sub-agent cost rollup
# ---------------------------------------------------------------------------

class TestSubAgentCostRollup:

    @pytest.mark.asyncio
    async def test_cost_rollup_in_children_summary(self):
        task_service = MagicMock()
        run_service = MagicMock()
        service = CoordinatorService(task_service, execution_run_service=run_service)

        task_service.get_subtasks.return_value = (True, {"tasks": [
            {"id": "c1", "status": "done"},
            {"id": "c2", "status": "done"},
        ]})
        run_service.list_runs.side_effect = [
            (True, {"runs": [{"cost_usd": 0.5}]}),
            (True, {"runs": [{"cost_usd": 0.3}, {"cost_usd": 0.2}]}),
        ]

        should, details = await service.evaluate_parent_status("parent1")
        assert should is True
        assert details["summary"]["total_cost_usd"] == 1.0

    @pytest.mark.asyncio
    async def test_cost_rollup_with_null_costs(self):
        task_service = MagicMock()
        run_service = MagicMock()
        service = CoordinatorService(task_service, execution_run_service=run_service)

        task_service.get_subtasks.return_value = (True, {"tasks": [
            {"id": "c1", "status": "done"},
        ]})
        run_service.list_runs.return_value = (True, {"runs": [
            {"cost_usd": None},
            {"cost_usd": 0.5},
        ]})

        should, details = await service.evaluate_parent_status("parent1")
        assert details["summary"]["total_cost_usd"] == 0.5

    @pytest.mark.asyncio
    async def test_cost_rollup_without_run_service(self):
        task_service = MagicMock()
        service = CoordinatorService(task_service)  # no run_service

        task_service.get_subtasks.return_value = (True, {"tasks": [
            {"id": "c1", "status": "done"},
        ]})

        should, details = await service.evaluate_parent_status("parent1")
        assert details["summary"]["total_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# Token breakdown + guidance pack hash
# ---------------------------------------------------------------------------

class TestTokenBreakdown:

    def test_injection_stats_has_breakdown(self):
        builder = PromptBuilder.__new__(PromptBuilder)
        builder.compress = False
        builder.max_kb_chunks = 3
        builder.default_build_command = "test"

        stats = builder._compute_injection_stats(
            "full prompt text here for testing",
            kb_chunks=[],
            code_patterns=[],
            learnings=[],
        )

        assert "prompt_token_breakdown" in stats
        assert stats["prompt_token_breakdown"]["total_prompt_tokens"] > 0
        assert stats["prompt_token_breakdown"]["guidance_pack_tokens"] == 0
        assert stats["prompt_token_breakdown"]["task_context_tokens"] > 0

    def _make_builder(self):
        builder = PromptBuilder.__new__(PromptBuilder)
        builder.compress = False
        builder.max_kb_chunks = 3
        builder.max_chunk_length = 200
        builder.default_build_command = "test"
        return builder

    def test_guidance_pack_hash_present(self):
        builder = self._make_builder()

        stats = builder._compute_injection_stats(
            "prompt",
            kb_chunks=[{"content": "test chunk", "similarity_score": 0.9}],
            code_patterns=[],
            learnings=[],
        )

        assert "guidance_pack_hash" in stats
        assert len(stats["guidance_pack_hash"]) == 12  # md5[:12]

    def test_guidance_pack_hash_empty_when_no_content(self):
        builder = self._make_builder()

        stats = builder._compute_injection_stats(
            "prompt",
            kb_chunks=[],
            code_patterns=[],
            learnings=[],
        )

        assert stats["guidance_pack_hash"] == ""

    def test_same_content_produces_same_hash(self):
        builder = self._make_builder()

        chunks = [{"content": "consistent content", "similarity_score": 0.9}]
        stats1 = builder._compute_injection_stats("p1", chunks, [], [])
        stats2 = builder._compute_injection_stats("p2", chunks, [], [])

        assert stats1["guidance_pack_hash"] == stats2["guidance_pack_hash"]
