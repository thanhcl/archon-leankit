"""Tests for LearningProcessor — store, find, recurrence, auto-promote."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.learning_processor import (
    LearningProcessor,
    _keyword_overlap,
    _pattern_key,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides):
    base = {"id": "task-001", "project_id": "proj-001"}
    base.update(overrides)
    return base


def _make_learning(**overrides):
    base = {
        "type": "error",
        "description": "Missing null check on user input caused crash",
        "area": "backend",
        "suggested_rule": "Always validate nullable fields before accessing",
    }
    base.update(overrides)
    return base


def _make_existing_learning(**overrides):
    base = {
        "id": "learn-001",
        "project_id": "proj-001",
        "type": "error",
        "description": "Missing null check on user input caused crash",
        "area": "backend",
        "suggested_rule": "Always validate nullable fields before accessing",
        "recurrence_count": 1,
        "status": "pending",
        "related_tasks": ["task-old"],
    }
    base.update(overrides)
    return base


def _mock_client(select_data=None, insert_data=None):
    client = MagicMock()
    table = MagicMock()

    # select chain
    select = MagicMock()
    select.eq.return_value = select
    select.gte.return_value = select
    select.order.return_value = select
    execute_result = MagicMock()
    execute_result.data = select_data or []
    select.execute.return_value = execute_result
    table.select.return_value = select

    # insert chain
    insert_result = MagicMock()
    insert_result.data = insert_data or [{"id": "learn-new"}]
    insert_mock = MagicMock()
    insert_mock.execute.return_value = insert_result
    table.insert.return_value = insert_mock

    # update chain
    update = MagicMock()
    update.eq.return_value = update
    update.execute.return_value = MagicMock(data=[{}])
    table.update.return_value = update

    client.table.return_value = table
    return client


# ---------------------------------------------------------------------------
# Tests: utility functions
# ---------------------------------------------------------------------------


class TestUtilities:
    def test_pattern_key(self):
        key = _pattern_key({"type": "error", "area": "backend", "description": "Missing null check"})
        assert key.startswith("error:backend:")
        assert "missing" in key

    def test_keyword_overlap_identical(self):
        assert _keyword_overlap("null check crash", "null check crash") == 1.0

    def test_keyword_overlap_partial(self):
        score = _keyword_overlap("null check crash", "null check validation")
        assert 0.4 < score < 0.8  # 2 of 4 unique words

    def test_keyword_overlap_no_match(self):
        assert _keyword_overlap("frontend css layout", "backend database query") == 0.0

    def test_keyword_overlap_empty(self):
        assert _keyword_overlap("", "something") == 0.0


# ---------------------------------------------------------------------------
# Tests: store
# ---------------------------------------------------------------------------


class TestStore:
    @pytest.mark.asyncio
    async def test_store_new_learning(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)

        lid = await processor.store(_make_task(), _make_learning())

        assert lid == "learn-new"
        client.table.assert_called_with("archon_learnings")
        insert_call = client.table().insert.call_args[0][0]
        assert insert_call["type"] == "error"
        assert insert_call["area"] == "backend"
        assert insert_call["status"] == "pending"

    @pytest.mark.asyncio
    async def test_store_handles_error(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB down")
        processor = LearningProcessor(supabase_client=client)

        lid = await processor.store(_make_task(), _make_learning())
        assert lid is None


# ---------------------------------------------------------------------------
# Tests: find_similar
# ---------------------------------------------------------------------------


class TestFindSimilar:
    @pytest.mark.asyncio
    async def test_finds_similar_learning(self):
        existing = _make_existing_learning(description="Missing null check on user input caused crash")
        client = _mock_client(select_data=[existing])
        processor = LearningProcessor(supabase_client=client)

        result = await processor.find_similar("Missing null check on input caused crash", "proj-001")
        assert result is not None
        assert result["id"] == "learn-001"

    @pytest.mark.asyncio
    async def test_no_similar_found(self):
        client = _mock_client(select_data=[
            _make_existing_learning(description="frontend css layout broken"),
        ])
        processor = LearningProcessor(supabase_client=client)

        result = await processor.find_similar("backend database migration failed", "proj-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_db(self):
        client = _mock_client(select_data=[])
        processor = LearningProcessor(supabase_client=client)
        result = await processor.find_similar("anything", "proj-001")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: increment_recurrence
# ---------------------------------------------------------------------------


class TestIncrementRecurrence:
    @pytest.mark.asyncio
    async def test_increments_count(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)
        existing = _make_existing_learning(recurrence_count=2)

        await processor.increment_recurrence(existing, "task-new")

        update_call = client.table().update.call_args[0][0]
        assert update_call["recurrence_count"] == 3
        assert "task-new" in update_call["related_tasks"]


# ---------------------------------------------------------------------------
# Tests: auto_promote
# ---------------------------------------------------------------------------


class TestAutoPromote:
    @pytest.mark.asyncio
    async def test_promotes_with_suggested_rule(self):
        client = _mock_client()
        notifier = MagicMock()
        notifier.on_learning_promoted = AsyncMock()
        processor = LearningProcessor(supabase_client=client, notifier=notifier)

        learning = _make_existing_learning(recurrence_count=3, suggested_rule="Always validate inputs")
        await processor.auto_promote(learning)

        update_call = client.table().update.call_args[0][0]
        assert update_call["status"] == "promoted"
        assert update_call["promoted_to"] == "KB"
        notifier.on_learning_promoted.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_without_suggested_rule(self):
        client = _mock_client()
        notifier = MagicMock()
        notifier.on_learning_pattern_detected = AsyncMock()
        processor = LearningProcessor(supabase_client=client, notifier=notifier)

        learning = _make_existing_learning(recurrence_count=3, suggested_rule=None)
        await processor.auto_promote(learning)

        # Should not update status to promoted
        client.table().update.assert_not_called()
        notifier.on_learning_pattern_detected.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: process (integration)
# ---------------------------------------------------------------------------


class TestProcess:
    @pytest.mark.asyncio
    async def test_stores_new_learning(self):
        client = _mock_client(select_data=[])  # no similar found
        processor = LearningProcessor(supabase_client=client)

        ids = await processor.process(_make_task(), [_make_learning()])
        assert len(ids) == 1
        assert ids[0] == "learn-new"

    @pytest.mark.asyncio
    async def test_increments_existing_learning(self):
        existing = _make_existing_learning(recurrence_count=1)
        client = _mock_client(select_data=[existing])
        processor = LearningProcessor(supabase_client=client)

        ids = await processor.process(
            _make_task(),
            [_make_learning(description="Missing null check on user input caused crash")],
        )
        assert len(ids) == 1
        assert ids[0] == "learn-001"

    @pytest.mark.asyncio
    async def test_auto_promotes_at_threshold(self):
        existing = _make_existing_learning(
            recurrence_count=2, status="pending",
            suggested_rule="Always validate inputs",
        )
        client = _mock_client(select_data=[existing])
        notifier = MagicMock()
        notifier.on_learning_promoted = AsyncMock()
        processor = LearningProcessor(supabase_client=client, notifier=notifier)

        await processor.process(
            _make_task(),
            [_make_learning(description="Missing null check on user input caused crash")],
        )

        # Should have been promoted (count goes from 2 → 3)
        notifier.on_learning_promoted.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_invalid_learning(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)

        ids = await processor.process(_make_task(), [{"type": "invalid_type", "description": "test"}])
        assert len(ids) == 0

    @pytest.mark.asyncio
    async def test_skips_empty_description(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)

        ids = await processor.process(_make_task(), [{"type": "error", "description": ""}])
        assert len(ids) == 0

    @pytest.mark.asyncio
    async def test_multiple_learnings(self):
        client = _mock_client(select_data=[])
        processor = LearningProcessor(supabase_client=client)

        learnings = [
            _make_learning(description="Issue A"),
            _make_learning(description="Issue B", type="best_practice"),
        ]
        ids = await processor.process(_make_task(), learnings)
        assert len(ids) == 2


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_learning(self):
        assert LearningProcessor._validate(_make_learning()) is True

    def test_invalid_type(self):
        assert LearningProcessor._validate({"type": "bad", "description": "x"}) is False

    def test_invalid_area(self):
        assert LearningProcessor._validate({"type": "error", "description": "x", "area": "bad"}) is False

    def test_missing_description(self):
        assert LearningProcessor._validate({"type": "error", "description": ""}) is False

    def test_valid_without_area(self):
        assert LearningProcessor._validate({"type": "error", "description": "x"}) is True


# ---------------------------------------------------------------------------
# Tests: CCSpawner LEARNINGS parsing
# ---------------------------------------------------------------------------


class TestCCSpawnerLearningsParsing:
    def test_valid_learnings_parsed(self):
        from src.server.services.engine.cc_spawner import CCSpawner

        stdout = (
            'RESULT: SUCCESS\n'
            'LEARNINGS: [{"type":"error","description":"Missing null check","area":"backend"}]\n'
        )
        parsed = CCSpawner.parse_result(stdout)
        assert len(parsed["learnings"]) == 1
        assert parsed["learnings"][0]["type"] == "error"

    def test_empty_learnings(self):
        from src.server.services.engine.cc_spawner import CCSpawner

        stdout = "RESULT: SUCCESS\nLEARNINGS: []\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["learnings"] == []

    def test_missing_learnings_defaults_empty(self):
        from src.server.services.engine.cc_spawner import CCSpawner

        stdout = "RESULT: SUCCESS\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["learnings"] == []

    def test_malformed_learnings(self):
        from src.server.services.engine.cc_spawner import CCSpawner

        stdout = "RESULT: SUCCESS\nLEARNINGS: [not valid json]\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["learnings"] == []
