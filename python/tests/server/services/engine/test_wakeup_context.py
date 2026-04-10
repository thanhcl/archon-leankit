"""Tests for Agent Wake-Up Context (MemPalace-inspired tiered loading).

Covers:
- _build_wakeup_context() assembly with all 4 sections
- Graceful handling of missing data sources
- Token budget enforcement with progressive truncation
- get_project_top_learnings() tier filtering and confidence decay
- Integration: wake-up context injection in prompt assembly
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_AGENT_DEFINITION = {
    "id": "agent-reviewer-001",
    "name": "CodeReviewer",
    "role": "code-review",
    "specialization": "TypeScript security review",
}

MOCK_PROJECT = {
    "id": "proj-test-001",
    "title": "LeanKit Platform",
    "tech_stack": "TypeScript, React, PostgreSQL",
}

MOCK_TASK = {
    "title": "Fix auth token refresh",
    "description": "Token refresh fails after 24h",
    "project_id": "proj-test-001",
    "priority": "high",
    "assignee": "CodeReviewer",
    "source_app": "LeanKit Platform",
}

MOCK_PROMOTED_LEARNINGS = [
    {
        "id": "learn-001",
        "description": "Always use parameterized queries for database access",
        "tier": "promoted",
        "confidence": 9,
        "recurrence_count": 7,
        "source": "observed",
        "created_at": datetime.now().isoformat(),
        "effective_confidence": 9,
    },
    {
        "id": "learn-002",
        "description": "Use ruff for linting, not flake8",
        "tier": "probation",
        "confidence": 7,
        "recurrence_count": 4,
        "source": "observed",
        "created_at": datetime.now().isoformat(),
        "effective_confidence": 7,
    },
]

MOCK_RECENT_RUNS = [
    {"task_id": "task-001", "result_summary": "Fixed CORS headers for API gateway", "finished_at": "2026-04-09T18:00:00Z"},
    {"task_id": "task-002", "result_summary": "Added rate limiting middleware", "finished_at": "2026-04-09T15:00:00Z"},
    {"task_id": "task-003", "result_summary": "Refactored auth service to use JWT", "finished_at": "2026-04-08T10:00:00Z"},
]


# ---------------------------------------------------------------------------
# _build_wakeup_context tests
# ---------------------------------------------------------------------------


class TestBuildWakeupContext:
    """Tests for PromptBuilder._build_wakeup_context()."""

    def _make_builder(self, learning_processor=None):
        from src.server.services.engine.prompt_builder import PromptBuilder

        return PromptBuilder(
            learning_processor=learning_processor,
            compress=True,
        )

    @pytest.mark.asyncio
    async def test_full_wakeup_context(self):
        """Wake-up context with all 4 sections present."""
        mock_lp = MagicMock()
        mock_lp.get_project_top_learnings.return_value = MOCK_PROMOTED_LEARNINGS

        builder = self._make_builder(learning_processor=mock_lp)

        with patch.object(builder, "_fetch_recent_execution_summaries", new_callable=AsyncMock) as mock_runs:
            mock_runs.return_value = MOCK_RECENT_RUNS
            text, tokens = await builder._build_wakeup_context(
                task=MOCK_TASK,
                project=MOCK_PROJECT,
                agent_definition=MOCK_AGENT_DEFINITION,
            )

        assert "## Agent Context" in text
        # Agent identity
        assert "CodeReviewer" in text
        assert "code-review" in text
        # Project context
        assert "LeanKit Platform" in text
        # Learnings
        assert "parameterized queries" in text
        assert "ruff" in text
        # Recent runs
        assert "CORS headers" in text
        assert tokens > 0

    @pytest.mark.asyncio
    async def test_wakeup_without_agent_definition(self):
        """Wake-up context gracefully omits identity when no agent_definition."""
        mock_lp = MagicMock()
        mock_lp.get_project_top_learnings.return_value = []

        builder = self._make_builder(learning_processor=mock_lp)

        with patch.object(builder, "_fetch_recent_execution_summaries", new_callable=AsyncMock) as mock_runs:
            mock_runs.return_value = []
            text, tokens = await builder._build_wakeup_context(
                task=MOCK_TASK,
                project=MOCK_PROJECT,
                agent_definition=None,
            )

        assert "## Agent Context" in text
        assert "CodeReviewer" not in text
        # Project context should still be present
        assert "LeanKit Platform" in text

    @pytest.mark.asyncio
    async def test_wakeup_without_learnings(self):
        """Wake-up context omits learnings section when none found."""
        mock_lp = MagicMock()
        mock_lp.get_project_top_learnings.return_value = []

        builder = self._make_builder(learning_processor=mock_lp)

        with patch.object(builder, "_fetch_recent_execution_summaries", new_callable=AsyncMock) as mock_runs:
            mock_runs.return_value = MOCK_RECENT_RUNS
            text, tokens = await builder._build_wakeup_context(
                task=MOCK_TASK,
                project=MOCK_PROJECT,
                agent_definition=MOCK_AGENT_DEFINITION,
            )

        assert "Key project patterns" not in text
        assert "Recent work" in text

    @pytest.mark.asyncio
    async def test_wakeup_without_recent_runs(self):
        """Wake-up context omits recent runs section when none found."""
        mock_lp = MagicMock()
        mock_lp.get_project_top_learnings.return_value = MOCK_PROMOTED_LEARNINGS

        builder = self._make_builder(learning_processor=mock_lp)

        with patch.object(builder, "_fetch_recent_execution_summaries", new_callable=AsyncMock) as mock_runs:
            mock_runs.return_value = []
            text, tokens = await builder._build_wakeup_context(
                task=MOCK_TASK,
                project=MOCK_PROJECT,
                agent_definition=MOCK_AGENT_DEFINITION,
            )

        assert "Recent work" not in text
        assert "Key project patterns" in text

    @pytest.mark.asyncio
    async def test_wakeup_empty_when_no_data(self):
        """Wake-up context returns empty when all data sources are empty."""
        builder = self._make_builder(learning_processor=None)

        with patch.object(builder, "_fetch_recent_execution_summaries", new_callable=AsyncMock) as mock_runs:
            mock_runs.return_value = []
            text, tokens = await builder._build_wakeup_context(
                task={"title": "test", "project_id": None},
                project=None,
                agent_definition=None,
            )

        assert text == ""
        assert tokens == 0

    @pytest.mark.asyncio
    async def test_wakeup_token_budget_enforcement(self):
        """Wake-up context respects token budget via progressive truncation."""
        mock_lp = MagicMock()
        mock_lp.get_project_top_learnings.return_value = MOCK_PROMOTED_LEARNINGS

        builder = self._make_builder(learning_processor=mock_lp)

        with patch.object(builder, "_fetch_recent_execution_summaries", new_callable=AsyncMock) as mock_runs:
            mock_runs.return_value = MOCK_RECENT_RUNS
            # Set very tight budget — should trigger truncation
            text, tokens = await builder._build_wakeup_context(
                task=MOCK_TASK,
                project=MOCK_PROJECT,
                agent_definition=MOCK_AGENT_DEFINITION,
                wakeup_budget=50,  # Very tight
            )

        # Should still produce something (identity + project at minimum)
        assert "## Agent Context" in text


# ---------------------------------------------------------------------------
# get_project_top_learnings tests
# ---------------------------------------------------------------------------


class TestGetProjectTopLearnings:
    """Tests for LearningProcessor.get_project_top_learnings()."""

    def _make_processor(self, mock_data=None):
        from src.server.services.engine.learning_processor import LearningProcessor

        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_in = MagicMock()
        mock_in2 = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.in_.return_value = mock_in
        mock_in.in_.return_value = mock_in2

        # Chain: .eq().order().limit().execute()
        mock_eq = MagicMock()
        mock_in2.eq.return_value = mock_eq
        mock_order = MagicMock()
        mock_eq.order.return_value = mock_order
        mock_limit = MagicMock()
        mock_order.limit.return_value = mock_limit

        mock_response = MagicMock()
        mock_response.data = mock_data or []
        mock_limit.execute.return_value = mock_response

        # Also handle the non-eq path (no project_id)
        mock_in2.order.return_value = mock_order

        processor = LearningProcessor(supabase_client=mock_client)
        return processor

    def test_returns_only_promoted_tiers(self):
        """Only probation/promoted learnings are returned by default."""
        data = [
            {
                "id": "l1",
                "description": "Use ruff",
                "tier": "promoted",
                "confidence": 9,
                "source": "user_stated",
                "recurrence_count": 5,
                "created_at": datetime.now().isoformat(),
            },
        ]
        processor = self._make_processor(mock_data=data)
        results = processor.get_project_top_learnings(project_id="proj-001", limit=5)
        assert len(results) == 1
        assert results[0]["tier"] == "promoted"

    def test_applies_confidence_decay(self):
        """Learnings older than 30 days have reduced effective confidence."""
        old_date = (datetime.now() - timedelta(days=90)).isoformat()
        data = [
            {
                "id": "l1",
                "description": "Old learning",
                "tier": "probation",
                "confidence": 5,
                "source": "observed",
                "recurrence_count": 3,
                "created_at": old_date,
            },
        ]
        processor = self._make_processor(mock_data=data)
        results = processor.get_project_top_learnings(project_id="proj-001")
        assert len(results) == 1
        # 90 days / 30 = 3 decay points: 5 - 3 = 2 (just at CONFIDENCE_FLOOR)
        assert results[0]["effective_confidence"] == 2

    def test_filters_below_confidence_floor(self):
        """Learnings decayed below CONFIDENCE_FLOOR are excluded."""
        very_old = (datetime.now() - timedelta(days=180)).isoformat()
        data = [
            {
                "id": "l1",
                "description": "Very old learning",
                "tier": "probation",
                "confidence": 5,
                "source": "observed",
                "recurrence_count": 10,
                "created_at": very_old,
            },
        ]
        processor = self._make_processor(mock_data=data)
        results = processor.get_project_top_learnings(project_id="proj-001")
        # 180 days / 30 = 6 decay: 5 - 6 = -1 → max(0, -1) = 0 < CONFIDENCE_FLOOR(2)
        assert len(results) == 0

    def test_respects_limit(self):
        """Returns at most `limit` results."""
        data = [
            {
                "id": f"l{i}",
                "description": f"Learning {i}",
                "tier": "promoted",
                "confidence": 8,
                "source": "user_stated",
                "recurrence_count": i,
                "created_at": datetime.now().isoformat(),
            }
            for i in range(10)
        ]
        processor = self._make_processor(mock_data=data)
        results = processor.get_project_top_learnings(project_id="proj-001", limit=3)
        assert len(results) == 3

    def test_sorted_by_confidence_then_recurrence(self):
        """Results sorted by effective_confidence DESC, recurrence DESC."""
        data = [
            {
                "id": "low",
                "description": "Low confidence",
                "tier": "probation",
                "confidence": 4,
                "source": "user_stated",
                "recurrence_count": 10,
                "created_at": datetime.now().isoformat(),
            },
            {
                "id": "high",
                "description": "High confidence",
                "tier": "promoted",
                "confidence": 9,
                "source": "user_stated",
                "recurrence_count": 2,
                "created_at": datetime.now().isoformat(),
            },
        ]
        processor = self._make_processor(mock_data=data)
        results = processor.get_project_top_learnings(project_id="proj-001")
        assert results[0]["id"] == "high"


# ---------------------------------------------------------------------------
# Integration: prompt assembly with wake-up context
# ---------------------------------------------------------------------------


class TestPromptAssemblyWithWakeup:
    """Integration tests for wake-up context in the final prompt."""

    @pytest.mark.asyncio
    async def test_wakeup_appears_before_task_header(self):
        """Wake-up context should appear before '# Task:' in the prompt."""
        from src.server.services.engine.prompt_builder import PromptBuilder

        mock_lp = MagicMock()
        mock_lp.get_relevant_learnings.return_value = []
        mock_lp.get_relevant_patterns.return_value = []
        mock_lp.get_project_top_learnings.return_value = MOCK_PROMOTED_LEARNINGS

        builder = PromptBuilder(learning_processor=mock_lp)

        with patch.object(builder, "_fetch_kb_context", new_callable=AsyncMock, return_value=[]), \
             patch.object(builder, "_fetch_codebase_intel", new_callable=AsyncMock, return_value=None), \
             patch.object(builder, "_fetch_recent_execution_summaries", new_callable=AsyncMock, return_value=MOCK_RECENT_RUNS):
            prompt, stats = await builder.build(
                task=MOCK_TASK,
                project=MOCK_PROJECT,
                agent_definition=MOCK_AGENT_DEFINITION,
            )

        # Wake-up context should be before the task header
        wakeup_pos = prompt.find("## Agent Context")
        task_pos = prompt.find("# Task:")
        assert wakeup_pos >= 0, "Wake-up context header not found in prompt"
        assert task_pos >= 0, "Task header not found in prompt"
        assert wakeup_pos < task_pos, "Wake-up context should appear before task header"

        # Separator should exist between wake-up and task
        separator_pos = prompt.find("---")
        assert wakeup_pos < separator_pos < task_pos

        # Stats should include wakeup_tokens
        assert "wakeup_tokens" in stats
        assert stats["wakeup_tokens"] > 0

    @pytest.mark.asyncio
    async def test_no_wakeup_when_no_data(self):
        """Prompt should not contain wake-up section when all data sources empty."""
        from src.server.services.engine.prompt_builder import PromptBuilder

        builder = PromptBuilder(learning_processor=None)

        with patch.object(builder, "_fetch_kb_context", new_callable=AsyncMock, return_value=[]), \
             patch.object(builder, "_fetch_codebase_intel", new_callable=AsyncMock, return_value=None), \
             patch.object(builder, "_fetch_recent_execution_summaries", new_callable=AsyncMock, return_value=[]):
            prompt, stats = await builder.build(
                task={"title": "test", "description": "test", "project_id": None},
                project=None,
                agent_definition=None,
            )

        assert "## Agent Context" not in prompt
        assert stats.get("wakeup_tokens", 0) == 0
