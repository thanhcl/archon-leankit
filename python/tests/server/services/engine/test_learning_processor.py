"""Tests for LearningProcessor — store, find, recurrence, auto-promote."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.learning_processor import (
    LearningProcessor,
    _code_pattern_key,
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


# ---------------------------------------------------------------------------
# Helpers: code patterns
# ---------------------------------------------------------------------------


def _make_pattern(**overrides):
    base = {
        "pattern_name": "Null-safe Repository Access",
        "category": "error-handling",
        "language": "java",
        "code_example": "Optional.ofNullable(repo.findById(id)).orElseThrow()",
        "context": "When fetching entities that may not exist",
        "anti_pattern": "Calling .get() without null check",
        "source_files": ["UserRepo.java"],
    }
    base.update(overrides)
    return base


def _make_existing_pattern(**overrides):
    base = {
        "id": "pat-001",
        "project_id": "proj-001",
        "pattern_name": "Null-safe Repository Access",
        "pattern_key": "error-handling.java.null-safe-repository-access",
        "category": "error-handling",
        "language": "java",
        "code_example": "Optional.ofNullable(repo.findById(id)).orElseThrow()",
        "context": "When fetching entities that may not exist",
        "anti_pattern": "Calling .get() without null check",
        "source_task_ids": ["task-old"],
        "source_files": ["UserRepo.java"],
        "usage_count": 1,
        "confidence": 0.7,
        "status": "pending",
    }
    base.update(overrides)
    return base


def _mock_pattern_client(select_data=None, insert_data=None):
    """Mock client that handles separate table routing for patterns vs learnings."""
    client = MagicMock()

    def make_table_mock(s_data, i_data):
        table = MagicMock()
        select = MagicMock()
        select.eq.return_value = select
        select.gte.return_value = select
        select.in_.return_value = select
        select.order.return_value = select
        select.limit.return_value = select
        execute_result = MagicMock()
        execute_result.data = s_data or []
        select.execute.return_value = execute_result
        table.select.return_value = select

        insert_result = MagicMock()
        insert_result.data = i_data or [{"id": "pat-new"}]
        insert_mock = MagicMock()
        insert_mock.execute.return_value = insert_result
        table.insert.return_value = insert_mock

        update = MagicMock()
        update.eq.return_value = update
        update.execute.return_value = MagicMock(data=[{}])
        table.update.return_value = update

        return table

    patterns_table = make_table_mock(select_data, insert_data)
    learnings_table = make_table_mock([], [{"id": "learn-new"}])

    def table_router(name):
        if name == "archon_code_patterns":
            return patterns_table
        return learnings_table

    client.table.side_effect = table_router
    return client, patterns_table


# ---------------------------------------------------------------------------
# Tests: _code_pattern_key
# ---------------------------------------------------------------------------


class TestCodePatternKey:
    def test_generates_key(self):
        key = _code_pattern_key({
            "pattern_name": "Null-safe Repository Access",
            "category": "error-handling",
            "language": "java",
        })
        assert key == "error-handling.java.null-safe-repository-access"

    def test_defaults_language_to_java(self):
        key = _code_pattern_key({"pattern_name": "Test Pattern", "category": "testing"})
        assert key.startswith("testing.java.")

    def test_normalizes_special_chars(self):
        key = _code_pattern_key({
            "pattern_name": "Try/Catch & Finally!!!",
            "category": "error-handling",
            "language": "java",
        })
        assert "/" not in key
        assert "&" not in key
        assert "!" not in key


# ---------------------------------------------------------------------------
# Tests: _validate_pattern
# ---------------------------------------------------------------------------


class TestValidatePattern:
    def test_valid_pattern(self):
        assert LearningProcessor._validate_pattern(_make_pattern()) is True

    def test_missing_name(self):
        assert LearningProcessor._validate_pattern(_make_pattern(pattern_name="")) is False

    def test_invalid_category(self):
        assert LearningProcessor._validate_pattern(_make_pattern(category="invalid")) is False

    def test_missing_code_example(self):
        assert LearningProcessor._validate_pattern(_make_pattern(code_example="")) is False

    def test_missing_context(self):
        assert LearningProcessor._validate_pattern(_make_pattern(context="")) is False


# ---------------------------------------------------------------------------
# Tests: _create_pattern
# ---------------------------------------------------------------------------


class TestCreatePattern:
    @pytest.mark.asyncio
    async def test_creates_new_pattern(self):
        client, pt = _mock_pattern_client()
        processor = LearningProcessor(supabase_client=client)

        pid = await processor._create_pattern(_make_task(), _make_pattern())

        assert pid == "pat-new"
        insert_call = pt.insert.call_args[0][0]
        assert insert_call["pattern_name"] == "Null-safe Repository Access"
        assert insert_call["category"] == "error-handling"
        assert insert_call["status"] == "pending"
        assert insert_call["confidence"] == 0.7

    @pytest.mark.asyncio
    async def test_create_handles_error(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB down")
        processor = LearningProcessor(supabase_client=client)

        pid = await processor._create_pattern(_make_task(), _make_pattern())
        assert pid is None


# ---------------------------------------------------------------------------
# Tests: _increment_pattern_usage
# ---------------------------------------------------------------------------


class TestIncrementPatternUsage:
    @pytest.mark.asyncio
    async def test_increments_usage(self):
        client, pt = _mock_pattern_client()
        processor = LearningProcessor(supabase_client=client)
        existing = _make_existing_pattern(usage_count=2, confidence=0.75)

        await processor._increment_pattern_usage(existing, _make_task())

        update_call = pt.update.call_args[0][0]
        assert update_call["usage_count"] == 3
        assert update_call["confidence"] == 0.80
        assert "task-001" in update_call["source_task_ids"]

    @pytest.mark.asyncio
    async def test_confidence_caps_at_1(self):
        client, pt = _mock_pattern_client()
        processor = LearningProcessor(supabase_client=client)
        existing = _make_existing_pattern(confidence=0.98)

        await processor._increment_pattern_usage(existing, _make_task())

        update_call = pt.update.call_args[0][0]
        assert update_call["confidence"] == 1.0


# ---------------------------------------------------------------------------
# Tests: _process_code_patterns (integration)
# ---------------------------------------------------------------------------


class TestProcessCodePatterns:
    @pytest.mark.asyncio
    async def test_creates_new_pattern_when_no_match(self):
        client, pt = _mock_pattern_client(select_data=[])
        processor = LearningProcessor(supabase_client=client)

        await processor._process_code_patterns(_make_task(), [_make_pattern()])

        pt.insert.assert_called_once()

    @pytest.mark.asyncio
    async def test_increments_existing_pattern(self):
        existing = _make_existing_pattern()
        client, pt = _mock_pattern_client(select_data=[existing])
        processor = LearningProcessor(supabase_client=client)

        await processor._process_code_patterns(_make_task(), [_make_pattern()])

        pt.update.assert_called()
        update_call = pt.update.call_args[0][0]
        assert update_call["usage_count"] == 2

    @pytest.mark.asyncio
    async def test_auto_promotes_at_threshold(self):
        existing = _make_existing_pattern(
            usage_count=2, confidence=0.88, status="active",
        )
        client, pt = _mock_pattern_client(select_data=[existing])
        processor = LearningProcessor(supabase_client=client)

        await processor._process_code_patterns(_make_task(), [_make_pattern()])

        # usage goes 2→3, confidence goes 0.88→0.93 (>=0.9), so promote is triggered
        # The promote call updates status to "promoted"
        # We check that update was called multiple times (increment + promote)
        assert pt.update.call_count >= 2

    @pytest.mark.asyncio
    async def test_skips_invalid_pattern(self):
        client, pt = _mock_pattern_client()
        processor = LearningProcessor(supabase_client=client)

        await processor._process_code_patterns(
            _make_task(), [{"pattern_name": "", "category": "bad"}],
        )

        pt.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_includes_code_patterns(self):
        """Verify process() calls _process_code_patterns for CODE_PATTERNS input."""
        client, pt = _mock_pattern_client(select_data=[])
        processor = LearningProcessor(supabase_client=client)

        ids = await processor.process(
            _make_task(), [], code_patterns=[_make_pattern()],
        )

        # Learnings empty, but patterns should still be processed
        assert ids == []
        pt.insert.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _build_pattern_kb_content
# ---------------------------------------------------------------------------


class TestBuildPatternKBContent:
    def test_renders_markdown(self):
        pattern = _make_existing_pattern()
        content = LearningProcessor._build_pattern_kb_content(pattern)
        assert "## Expert Code Pattern: Null-safe Repository Access" in content
        assert "error-handling" in content
        assert "Optional.ofNullable" in content
        assert "Anti-Pattern" in content


# ---------------------------------------------------------------------------
# Tests: list/stats/validate/deprecate helpers
# ---------------------------------------------------------------------------


class TestPatternQueryHelpers:
    def test_list_code_patterns(self):
        client, _ = _mock_pattern_client(select_data=[_make_existing_pattern()])
        processor = LearningProcessor(supabase_client=client)

        ok, result = processor.list_code_patterns()
        assert ok is True
        assert result["count"] == 1

    def test_get_code_pattern(self):
        client, _ = _mock_pattern_client(select_data=[_make_existing_pattern()])
        processor = LearningProcessor(supabase_client=client)

        ok, result = processor.get_code_pattern("pat-001")
        assert ok is True
        assert result["pattern"]["id"] == "pat-001"

    def test_get_code_pattern_not_found(self):
        client, _ = _mock_pattern_client(select_data=[])
        processor = LearningProcessor(supabase_client=client)

        ok, result = processor.get_code_pattern("nonexistent")
        assert ok is False

    @pytest.mark.asyncio
    async def test_validate_pattern(self):
        client, pt = _mock_pattern_client()
        processor = LearningProcessor(supabase_client=client)

        ok, msg = await processor.validate_pattern("pat-001")
        assert ok is True
        update_call = pt.update.call_args[0][0]
        assert update_call["expert_validated"] is True
        assert update_call["status"] == "active"

    @pytest.mark.asyncio
    async def test_deprecate_pattern(self):
        client, pt = _mock_pattern_client()
        processor = LearningProcessor(supabase_client=client)

        ok, msg = await processor.deprecate_pattern("pat-001")
        assert ok is True
        update_call = pt.update.call_args[0][0]
        assert update_call["status"] == "deprecated"

    def test_get_pattern_stats(self):
        patterns = [
            _make_existing_pattern(
                id="p1", category="security", status="promoted",
                expert_validated=True, usage_count=5,
            ),
            _make_existing_pattern(
                id="p2", category="testing", status="active",
                expert_validated=False, usage_count=2,
            ),
        ]
        client, _ = _mock_pattern_client(select_data=patterns)
        processor = LearningProcessor(supabase_client=client)

        ok, result = processor.get_pattern_stats()
        assert ok is True
        assert result["total"] == 2
        assert result["promoted"] == 1
        assert result["validated"] == 1
        assert "security" in result["by_category"]

    def test_get_relevant_patterns(self):
        patterns = [_make_existing_pattern(status="active", confidence=0.85)]
        client, _ = _mock_pattern_client(select_data=patterns)
        processor = LearningProcessor(supabase_client=client)

        result = processor.get_relevant_patterns("proj-001")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests: CCSpawner CODE_PATTERNS parsing
# ---------------------------------------------------------------------------


class TestCCSpawnerCodePatternsParsing:
    def test_valid_code_patterns_parsed(self):
        from src.server.services.engine.cc_spawner import CCSpawner

        stdout = (
            'RESULT: SUCCESS\n'
            'CODE_PATTERNS: [{"pattern_name":"Null Safe","category":"error-handling",'
            '"code_example":"Optional.of(x)","context":"entity lookup"}]\n'
        )
        parsed = CCSpawner.parse_result(stdout)
        assert len(parsed["code_patterns"]) == 1
        assert parsed["code_patterns"][0]["pattern_name"] == "Null Safe"

    def test_empty_code_patterns(self):
        from src.server.services.engine.cc_spawner import CCSpawner

        stdout = "RESULT: SUCCESS\nCODE_PATTERNS: []\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["code_patterns"] == []

    def test_missing_code_patterns_defaults_empty(self):
        from src.server.services.engine.cc_spawner import CCSpawner

        stdout = "RESULT: SUCCESS\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["code_patterns"] == []

    def test_malformed_code_patterns(self):
        from src.server.services.engine.cc_spawner import CCSpawner

        stdout = "RESULT: SUCCESS\nCODE_PATTERNS: [bad json]\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["code_patterns"] == []

    def test_both_learnings_and_patterns_parsed(self):
        from src.server.services.engine.cc_spawner import CCSpawner

        stdout = (
            'RESULT: SUCCESS\n'
            'LEARNINGS: [{"type":"error","description":"test","area":"backend"}]\n'
            'CODE_PATTERNS: [{"pattern_name":"Test","category":"testing",'
            '"code_example":"assert x","context":"unit tests"}]\n'
        )
        parsed = CCSpawner.parse_result(stdout)
        assert len(parsed["learnings"]) == 1
        assert len(parsed["code_patterns"]) == 1


# ---------------------------------------------------------------------------
# Tests: get_relevant_learnings
# ---------------------------------------------------------------------------


class TestGetRelevantLearnings:
    def _mock_client_for_learnings(self, data=None):
        """Create a mock client that supports the in_() chain for learnings queries."""
        client = MagicMock()
        table = MagicMock()

        select = MagicMock()
        select.in_.return_value = select
        select.eq.return_value = select
        select.order.return_value = select
        select.limit.return_value = select
        execute_result = MagicMock()
        execute_result.data = data or []
        select.execute.return_value = execute_result
        table.select.return_value = select

        client.table.return_value = table
        return client

    def test_returns_learnings_for_project(self):
        learnings_data = [
            _make_existing_learning(description="Always validate user input", recurrence_count=3),
            _make_existing_learning(id="learn-002", description="Check null before access", recurrence_count=2),
        ]
        client = self._mock_client_for_learnings(data=learnings_data)
        processor = LearningProcessor(supabase_client=client)
        result = processor.get_relevant_learnings(project_id="proj-001", limit=10)
        assert len(result) == 2

    def test_returns_empty_when_no_learnings(self):
        client = self._mock_client_for_learnings(data=[])
        processor = LearningProcessor(supabase_client=client)
        result = processor.get_relevant_learnings(project_id="proj-001")
        assert result == []

    def test_filters_by_keyword_overlap(self):
        learnings_data = [
            _make_existing_learning(description="Missing null check on user input caused crash", recurrence_count=2),
            _make_existing_learning(id="learn-002", description="API rate limit exceeded on deploy", recurrence_count=2),
            _make_existing_learning(id="learn-003", description="Input validation missing for login", recurrence_count=1),
        ]
        client = self._mock_client_for_learnings(data=learnings_data)
        processor = LearningProcessor(supabase_client=client)
        result = processor.get_relevant_learnings(
            project_id="proj-001",
            task_keywords=["input", "validation", "login"],
            limit=2,
        )
        assert len(result) == 2
        # Items with keyword overlap should be prioritized
        descriptions = [r["description"] for r in result]
        assert any("input" in d.lower() for d in descriptions)

    def test_respects_limit(self):
        learnings_data = [_make_existing_learning(id=f"learn-{i}") for i in range(20)]
        client = self._mock_client_for_learnings(data=learnings_data)
        processor = LearningProcessor(supabase_client=client)
        result = processor.get_relevant_learnings(project_id="proj-001", limit=5)
        assert len(result) == 5

    def test_graceful_on_db_error(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB connection failed")
        processor = LearningProcessor(supabase_client=client)
        result = processor.get_relevant_learnings(project_id="proj-001")
        assert result == []

    def test_works_without_keywords(self):
        learnings_data = [
            _make_existing_learning(recurrence_count=5),
            _make_existing_learning(id="learn-002", recurrence_count=2),
        ]
        client = self._mock_client_for_learnings(data=learnings_data)
        processor = LearningProcessor(supabase_client=client)
        result = processor.get_relevant_learnings(project_id="proj-001", task_keywords=None)
        assert len(result) == 2

    def test_ignores_short_keywords(self):
        """Keywords with <= 2 chars are filtered out."""
        learnings_data = [_make_existing_learning()]
        client = self._mock_client_for_learnings(data=learnings_data)
        processor = LearningProcessor(supabase_client=client)
        result = processor.get_relevant_learnings(
            project_id="proj-001",
            task_keywords=["a", "to", "fix"],  # only "fix" should be used
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests: execution_run_id tracking
# ---------------------------------------------------------------------------


class TestExecutionRunTracking:
    @pytest.mark.asyncio
    async def test_store_includes_run_id(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)

        await processor.store(_make_task(), _make_learning(), execution_run_id="run-001")

        insert_call = client.table().insert.call_args[0][0]
        assert insert_call["source_run_ids"] == ["run-001"]

    @pytest.mark.asyncio
    async def test_store_empty_run_ids_without_run(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)

        await processor.store(_make_task(), _make_learning(), execution_run_id=None)

        insert_call = client.table().insert.call_args[0][0]
        assert insert_call["source_run_ids"] == []

    @pytest.mark.asyncio
    async def test_increment_recurrence_appends_run_id(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)
        existing = _make_existing_learning(recurrence_count=1, source_run_ids=["run-old"])

        await processor.increment_recurrence(existing, "task-new", execution_run_id="run-002")

        update_call = client.table().update.call_args[0][0]
        assert "run-old" in update_call["source_run_ids"]
        assert "run-002" in update_call["source_run_ids"]

    @pytest.mark.asyncio
    async def test_increment_recurrence_dedupes_run_ids(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)
        existing = _make_existing_learning(recurrence_count=1, source_run_ids=["run-001"])

        await processor.increment_recurrence(existing, "task-new", execution_run_id="run-001")

        update_call = client.table().update.call_args[0][0]
        assert update_call["source_run_ids"].count("run-001") == 1

    @pytest.mark.asyncio
    async def test_process_passes_run_id_to_store(self):
        client = _mock_client(select_data=[])  # no similar
        processor = LearningProcessor(supabase_client=client)

        await processor.process(
            _make_task(), [_make_learning()], execution_run_id="run-abc"
        )

        insert_call = client.table().insert.call_args[0][0]
        assert insert_call["source_run_ids"] == ["run-abc"]

    @pytest.mark.asyncio
    async def test_process_passes_run_id_to_increment(self):
        existing = _make_existing_learning(recurrence_count=1, source_run_ids=[])
        client = _mock_client(select_data=[existing])
        processor = LearningProcessor(supabase_client=client)

        await processor.process(
            _make_task(),
            [_make_learning(description="Missing null check on user input caused crash")],
            execution_run_id="run-xyz",
        )

        update_call = client.table().update.call_args_list[0][0][0]
        assert "run-xyz" in update_call["source_run_ids"]


# ---------------------------------------------------------------------------
# Tests: promotion log
# ---------------------------------------------------------------------------


class TestPromotionLog:
    def _mock_full_client(self, select_data=None):
        """Mock that routes both TABLE and PROMOTION_LOG_TABLE separately."""
        client = MagicMock()

        def make_table_mock(s_data):
            table = MagicMock()
            select = MagicMock()
            select.eq.return_value = select
            select.in_.return_value = select
            select.order.return_value = select
            select.limit.return_value = select
            execute_result = MagicMock()
            execute_result.data = s_data or []
            select.execute.return_value = execute_result
            table.select.return_value = select

            insert_result = MagicMock()
            insert_result.data = [{"id": "log-new"}]
            insert_mock = MagicMock()
            insert_mock.execute.return_value = insert_result
            table.insert.return_value = insert_mock

            update = MagicMock()
            update.eq.return_value = update
            update.execute.return_value = MagicMock(data=[{}])
            table.update.return_value = update

            return table

        learnings_table = make_table_mock(select_data)
        suggestions_table = make_table_mock([])
        log_table = make_table_mock([])

        def router(name):
            if name == "archon_promotion_log":
                return log_table
            if name == "archon_rule_suggestions":
                return suggestions_table
            return learnings_table

        client.table.side_effect = router
        return client, log_table

    @pytest.mark.asyncio
    async def test_auto_promote_writes_log(self):
        client, log_table = self._mock_full_client()
        processor = LearningProcessor(supabase_client=client)

        learning = _make_existing_learning(
            recurrence_count=3,
            suggested_rule="Always validate inputs",
            source_run_ids=["run-1", "run-2"],
            related_tasks=["task-a", "task-b"],
        )
        await processor.auto_promote(learning)

        log_table.insert.assert_called_once()
        log_call = log_table.insert.call_args[0][0]
        assert log_call["learning_id"] == "learn-001"
        assert log_call["promotion_type"] == "auto"
        assert log_call["promoted_to"] == "KB"
        assert log_call["recurrence_count"] == 3
        assert log_call["source_run_ids"] == ["run-1", "run-2"]
        assert log_call["source_task_ids"] == ["task-a", "task-b"]
        assert 0 < log_call["confidence"] <= 0.95

    @pytest.mark.asyncio
    async def test_log_promotion_handles_db_error(self):
        """Promotion log failure should not break the promote flow."""
        client, log_table = self._mock_full_client()
        log_table.insert.side_effect = Exception("DB error")
        processor = LearningProcessor(supabase_client=client)

        learning = _make_existing_learning(
            recurrence_count=3, suggested_rule="Always validate inputs"
        )
        # Should not raise
        await processor.auto_promote(learning)

    @pytest.mark.asyncio
    async def test_manual_promote_writes_log(self):
        learning_row = _make_existing_learning(
            recurrence_count=2, source_run_ids=["run-x"]
        )
        client, log_table = self._mock_full_client(select_data=[learning_row])
        processor = LearningProcessor(supabase_client=client)

        ok, msg = await processor.manual_promote("learn-001")

        assert ok is True
        log_table.insert.assert_called_once()
        log_call = log_table.insert.call_args[0][0]
        assert log_call["promotion_type"] == "manual"
        assert log_call["promoted_to"] == "KB_manual"

    @pytest.mark.asyncio
    async def test_log_confidence_formula(self):
        """Confidence = min(0.95, 0.5 + (recurrence-1) * 0.1)."""
        client, log_table = self._mock_full_client()
        processor = LearningProcessor(supabase_client=client)

        # recurrence=1 → 0.5 + 0*0.1 = 0.5
        await processor._log_promotion(
            _make_existing_learning(recurrence_count=1),
            promotion_type="auto", promoted_to="KB",
        )
        call = log_table.insert.call_args[0][0]
        assert call["confidence"] == 0.5

        log_table.insert.reset_mock()

        # recurrence=6 → 0.5 + 5*0.1 = 1.0 → capped at 0.95
        await processor._log_promotion(
            _make_existing_learning(recurrence_count=6),
            promotion_type="auto", promoted_to="KB",
        )
        call = log_table.insert.call_args[0][0]
        assert call["confidence"] == 0.95
