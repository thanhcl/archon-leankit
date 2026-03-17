"""Tests for Code Pattern Library — create, find, increment, promote, validate, stats."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.cc_spawner import CCSpawner
from src.server.services.engine.learning_processor import (
    PATTERN_CONFIDENCE_INCREMENT,
    PATTERN_PROMOTE_CONFIDENCE,
    PATTERN_PROMOTE_USAGE,
    PATTERNS_TABLE,
    LearningProcessor,
    _code_pattern_key,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides):
    base = {"id": "task-001", "project_id": "proj-001"}
    base.update(overrides)
    return base


def _make_code_pattern(**overrides):
    base = {
        "pattern_name": "PKCS11 Session Management",
        "category": "security",
        "code_example": "try (Session s = provider.openSession()) { ... }",
        "context": "Any PKCS11 crypto operation",
        "anti_pattern": "Never keep sessions open across request boundaries",
        "source_files": ["CryptoService.java"],
    }
    base.update(overrides)
    return base


def _make_existing_pattern(**overrides):
    base = {
        "id": "pat-001",
        "project_id": "proj-001",
        "pattern_name": "PKCS11 Session Management",
        "pattern_key": "security.java.pkcs11-session-management",
        "category": "security",
        "language": "java",
        "code_example": "try (Session s = provider.openSession()) { ... }",
        "context": "Any PKCS11 crypto operation",
        "anti_pattern": "Never keep sessions open",
        "source_task_ids": ["task-old"],
        "source_files": ["CryptoService.java"],
        "usage_count": 1,
        "confidence": 0.7,
        "status": "pending",
        "expert_validated": False,
    }
    base.update(overrides)
    return base


def _mock_client(select_data=None, insert_data=None):
    """Mock supabase client supporting both learnings and patterns tables.

    Returns the same table mock for each table name so assertions
    can inspect calls made during the test.
    """
    client = MagicMock()
    _cache: dict[str, MagicMock] = {}

    def make_table(table_name):
        if table_name in _cache:
            return _cache[table_name]

        table = MagicMock()

        # select chain
        select = MagicMock()
        select.eq.return_value = select
        select.gte.return_value = select
        select.in_.return_value = select
        select.order.return_value = select
        select.limit.return_value = select
        execute_result = MagicMock()
        execute_result.data = select_data or []
        select.execute.return_value = execute_result
        table.select.return_value = select

        # insert chain
        insert_result = MagicMock()
        insert_result.data = insert_data or [{"id": "pat-new"}]
        insert_mock = MagicMock()
        insert_mock.execute.return_value = insert_result
        table.insert.return_value = insert_mock

        # update chain
        update = MagicMock()
        update.eq.return_value = update
        update.execute.return_value = MagicMock(data=[{}])
        table.update.return_value = update

        _cache[table_name] = table
        return table

    client.table.side_effect = make_table
    return client


# ---------------------------------------------------------------------------
# Tests: _code_pattern_key
# ---------------------------------------------------------------------------


class TestCodePatternKey:
    def test_basic_key(self):
        key = _code_pattern_key({
            "pattern_name": "PKCS11 Session Management",
            "category": "security",
            "language": "java",
        })
        assert key == "security.java.pkcs11-session-management"

    def test_default_language(self):
        key = _code_pattern_key({
            "pattern_name": "Error Handler",
            "category": "error-handling",
        })
        assert key.startswith("error-handling.java.")

    def test_special_chars_stripped(self):
        key = _code_pattern_key({
            "pattern_name": "Try/Catch (with finally!)",
            "category": "testing",
            "language": "python",
        })
        assert "/" not in key
        assert "(" not in key


# ---------------------------------------------------------------------------
# Tests: _validate_pattern
# ---------------------------------------------------------------------------


class TestValidatePattern:
    def test_valid_pattern(self):
        assert LearningProcessor._validate_pattern(_make_code_pattern()) is True

    def test_missing_name(self):
        assert LearningProcessor._validate_pattern(_make_code_pattern(pattern_name="")) is False

    def test_invalid_category(self):
        assert LearningProcessor._validate_pattern(_make_code_pattern(category="invalid")) is False

    def test_missing_code_example(self):
        assert LearningProcessor._validate_pattern(_make_code_pattern(code_example="")) is False

    def test_missing_context(self):
        assert LearningProcessor._validate_pattern(_make_code_pattern(context="")) is False


# ---------------------------------------------------------------------------
# Tests: _create_pattern
# ---------------------------------------------------------------------------


class TestCreatePattern:
    @pytest.mark.asyncio
    async def test_creates_new_pattern(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)

        pid = await processor._create_pattern(_make_task(), _make_code_pattern())

        assert pid == "pat-new"
        # Verify insert was called on the patterns table
        client.table.assert_any_call(PATTERNS_TABLE)

    @pytest.mark.asyncio
    async def test_handles_db_error(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB down")
        processor = LearningProcessor(supabase_client=client)

        pid = await processor._create_pattern(_make_task(), _make_code_pattern())
        assert pid is None


# ---------------------------------------------------------------------------
# Tests: _find_similar_pattern
# ---------------------------------------------------------------------------


class TestFindSimilarPattern:
    @pytest.mark.asyncio
    async def test_finds_by_key(self):
        existing = _make_existing_pattern()
        client = _mock_client(select_data=[existing])
        processor = LearningProcessor(supabase_client=client)

        result = await processor._find_similar_pattern(_make_code_pattern(), "proj-001")
        assert result is not None
        assert result["id"] == "pat-001"

    @pytest.mark.asyncio
    async def test_no_match(self):
        client = _mock_client(select_data=[])
        processor = LearningProcessor(supabase_client=client)

        result = await processor._find_similar_pattern(_make_code_pattern(), "proj-001")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _increment_pattern_usage
# ---------------------------------------------------------------------------


class TestIncrementPatternUsage:
    @pytest.mark.asyncio
    async def test_increments_count_and_confidence(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)
        existing = _make_existing_pattern(usage_count=2, confidence=0.8)

        await processor._increment_pattern_usage(existing, _make_task())

        # Find the update call on the patterns table
        update_call = client.table(PATTERNS_TABLE).update.call_args[0][0]
        assert update_call["usage_count"] == 3
        assert update_call["confidence"] == pytest.approx(0.8 + PATTERN_CONFIDENCE_INCREMENT)
        assert "task-001" in update_call["source_task_ids"]

    @pytest.mark.asyncio
    async def test_confidence_capped_at_1(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)
        existing = _make_existing_pattern(usage_count=10, confidence=0.98)

        await processor._increment_pattern_usage(existing, _make_task())

        update_call = client.table(PATTERNS_TABLE).update.call_args[0][0]
        assert update_call["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Tests: auto-promote threshold
# ---------------------------------------------------------------------------


class TestAutoPromote:
    @pytest.mark.asyncio
    async def test_promotes_at_threshold(self):
        """Pattern should be promoted when confidence >= 0.9 AND usage >= 3."""
        existing = _make_existing_pattern(
            usage_count=2,  # will become 3 after increment
            confidence=PATTERN_PROMOTE_CONFIDENCE - PATTERN_CONFIDENCE_INCREMENT,  # will cross 0.9
            status="pending",
        )
        client = _mock_client(select_data=[existing])
        notifier = MagicMock()
        notifier.on_pattern_promoted = AsyncMock()
        processor = LearningProcessor(supabase_client=client, notifier=notifier)

        await processor._process_code_patterns(_make_task(), [_make_code_pattern()])

        notifier.on_pattern_promoted.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_promote_below_threshold(self):
        """Pattern should NOT be promoted when usage < 3."""
        existing = _make_existing_pattern(
            usage_count=1,  # will become 2
            confidence=0.7,
            status="pending",
        )
        client = _mock_client(select_data=[existing])
        notifier = MagicMock()
        notifier.on_pattern_promoted = AsyncMock()
        processor = LearningProcessor(supabase_client=client, notifier=notifier)

        await processor._process_code_patterns(_make_task(), [_make_code_pattern()])

        notifier.on_pattern_promoted.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_promote_already_promoted(self):
        existing = _make_existing_pattern(
            usage_count=5, confidence=0.95, status="promoted",
        )
        client = _mock_client(select_data=[existing])
        notifier = MagicMock()
        notifier.on_pattern_promoted = AsyncMock()
        processor = LearningProcessor(supabase_client=client, notifier=notifier)

        await processor._process_code_patterns(_make_task(), [_make_code_pattern()])

        notifier.on_pattern_promoted.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: KB content generation
# ---------------------------------------------------------------------------


class TestBuildPatternKBContent:
    def test_includes_all_sections(self):
        pattern = _make_existing_pattern()
        content = LearningProcessor._build_pattern_kb_content(pattern)

        assert "PKCS11 Session Management" in content
        assert "security" in content
        assert "java" in content
        assert "When to Use" in content
        assert "Expert Implementation" in content
        assert "Anti-Pattern" in content
        assert "Provenance" in content
        assert "CryptoService.java" in content


# ---------------------------------------------------------------------------
# Tests: process integration (learnings + code patterns)
# ---------------------------------------------------------------------------


class TestProcessWithPatterns:
    @pytest.mark.asyncio
    async def test_processes_both_learnings_and_patterns(self):
        client = _mock_client(select_data=[])
        processor = LearningProcessor(supabase_client=client)

        learnings = [{"type": "error", "description": "Something broke", "area": "backend"}]
        patterns = [_make_code_pattern()]

        ids = await processor.process(_make_task(), learnings, patterns)
        # Learning stored
        assert len(ids) == 1

    @pytest.mark.asyncio
    async def test_skips_invalid_patterns(self):
        client = _mock_client(select_data=[])
        processor = LearningProcessor(supabase_client=client)

        invalid_patterns = [{"pattern_name": "", "category": "bad"}]
        await processor.process(_make_task(), [], invalid_patterns)
        # Should not raise


# ---------------------------------------------------------------------------
# Tests: query helpers
# ---------------------------------------------------------------------------


class TestPatternQueryHelpers:
    def test_list_code_patterns(self):
        client = _mock_client(select_data=[_make_existing_pattern()])
        processor = LearningProcessor(supabase_client=client)

        ok, result = processor.list_code_patterns(project_id="proj-001")
        assert ok is True
        assert result["count"] == 1

    def test_get_code_pattern(self):
        client = _mock_client(select_data=[_make_existing_pattern()])
        processor = LearningProcessor(supabase_client=client)

        ok, result = processor.get_code_pattern("pat-001")
        assert ok is True
        assert "pattern" in result

    def test_get_pattern_not_found(self):
        client = _mock_client(select_data=[])
        processor = LearningProcessor(supabase_client=client)

        ok, result = processor.get_code_pattern("nonexistent")
        assert ok is False

    @pytest.mark.asyncio
    async def test_validate_pattern_api(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)

        ok, msg = await processor.validate_pattern("pat-001")
        assert ok is True
        update_call = client.table(PATTERNS_TABLE).update.call_args[0][0]
        assert update_call["expert_validated"] is True
        assert update_call["status"] == "active"

    @pytest.mark.asyncio
    async def test_deprecate_pattern_api(self):
        client = _mock_client()
        processor = LearningProcessor(supabase_client=client)

        ok, msg = await processor.deprecate_pattern("pat-001")
        assert ok is True
        update_call = client.table(PATTERNS_TABLE).update.call_args[0][0]
        assert update_call["status"] == "deprecated"

    def test_get_pattern_stats(self):
        patterns = [
            _make_existing_pattern(category="security", status="promoted", usage_count=5, expert_validated=True),
            _make_existing_pattern(id="pat-002", category="testing", status="pending", usage_count=2),
        ]
        client = _mock_client(select_data=patterns)
        processor = LearningProcessor(supabase_client=client)

        ok, result = processor.get_pattern_stats()
        assert ok is True
        assert result["total"] == 2
        assert result["promoted"] == 1
        assert result["validated"] == 1
        assert "security" in result["by_category"]
        assert len(result["top_patterns"]) <= 5

    def test_get_relevant_patterns(self):
        patterns = [_make_existing_pattern(status="active", confidence=0.85)]
        client = _mock_client(select_data=patterns)
        processor = LearningProcessor(supabase_client=client)

        result = processor.get_relevant_patterns("proj-001")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests: CCSpawner CODE_PATTERNS parsing
# ---------------------------------------------------------------------------


class TestCCSpawnerCodePatternsParsing:
    def test_valid_code_patterns_parsed(self):
        stdout = (
            'RESULT: SUCCESS\n'
            'CODE_PATTERNS: [{"pattern_name":"Session Mgmt","category":"security",'
            '"code_example":"try {}","context":"crypto ops","source_files":["A.java"]}]\n'
        )
        parsed = CCSpawner.parse_result(stdout)
        assert len(parsed["code_patterns"]) == 1
        assert parsed["code_patterns"][0]["pattern_name"] == "Session Mgmt"

    def test_empty_code_patterns(self):
        stdout = "RESULT: SUCCESS\nCODE_PATTERNS: []\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["code_patterns"] == []

    def test_missing_code_patterns_defaults_empty(self):
        stdout = "RESULT: SUCCESS\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["code_patterns"] == []

    def test_malformed_code_patterns(self):
        stdout = "RESULT: SUCCESS\nCODE_PATTERNS: [not valid json]\n"
        parsed = CCSpawner.parse_result(stdout)
        assert parsed["code_patterns"] == []

    def test_both_learnings_and_patterns(self):
        stdout = (
            'RESULT: SUCCESS\n'
            'LEARNINGS: [{"type":"error","description":"oops","area":"backend"}]\n'
            'CODE_PATTERNS: [{"pattern_name":"P1","category":"testing",'
            '"code_example":"assert","context":"tests","source_files":[]}]\n'
        )
        parsed = CCSpawner.parse_result(stdout)
        assert len(parsed["learnings"]) == 1
        assert len(parsed["code_patterns"]) == 1


# ---------------------------------------------------------------------------
# Tests: PromptBuilder code pattern injection
# ---------------------------------------------------------------------------


class TestPromptBuilderPatterns:
    @pytest.mark.asyncio
    async def test_injects_patterns_into_prompt(self):
        from src.server.services.engine.prompt_builder import PromptBuilder

        mock_processor = MagicMock()
        mock_processor.get_relevant_patterns.return_value = [
            _make_existing_pattern(usage_count=5, confidence=0.95),
        ]

        builder = PromptBuilder(
            rag_service=MagicMock(perform_rag_query=AsyncMock(return_value=(True, {"results": []}))),
            learning_processor=mock_processor,
        )

        task = {"id": "t-1", "title": "Test task", "project_id": "proj-001"}
        prompt = await builder.build(task)

        assert "Expert Code Patterns" in prompt
        assert "PKCS11 Session Management" in prompt
        assert "CODE_PATTERNS:" in prompt

    @pytest.mark.asyncio
    async def test_prompt_without_processor(self):
        from src.server.services.engine.prompt_builder import PromptBuilder

        builder = PromptBuilder(
            rag_service=MagicMock(perform_rag_query=AsyncMock(return_value=(True, {"results": []}))),
        )

        task = {"id": "t-1", "title": "Test task"}
        prompt = await builder.build(task)

        # Should still have CODE_PATTERNS output section
        assert "CODE_PATTERNS:" in prompt
        # Should NOT have the injection section
        assert "Expert Code Patterns (from project library)" not in prompt
