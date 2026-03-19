"""Tests for CodePatternService."""

from unittest.mock import MagicMock

import pytest

from src.server.services.patterns.code_pattern_service import CodePatternService


# ============================================================================
# Helpers
# ============================================================================

def _mock_client(select_data=None, insert_data=None, update_data=None):
    """Create a chainable mock Supabase client."""
    client = MagicMock()
    table = MagicMock()

    # select chain
    select = MagicMock()
    select.eq.return_value = select
    select.order.return_value = select
    select.limit.return_value = select
    execute_result = MagicMock()
    execute_result.data = select_data if select_data is not None else []
    select.execute.return_value = execute_result
    table.select.return_value = select

    # insert chain
    insert = MagicMock()
    insert_result = MagicMock()
    insert_result.data = insert_data if insert_data is not None else [{"id": "pat-new"}]
    insert.execute.return_value = insert_result
    table.insert.return_value = insert

    # update chain
    update = MagicMock()
    update.eq.return_value = update
    update_result = MagicMock()
    update_result.data = update_data if update_data is not None else [{"id": "pat-1"}]
    update.execute.return_value = update_result
    table.update.return_value = update

    client.table.return_value = table
    return client


def _sample_pattern(**overrides):
    """Create a sample pattern dict."""
    data = {
        "id": "pat-1",
        "project_id": "proj-1",
        "pattern_name": "PKCS#11 Session Management",
        "pattern_key": "security.java.pkcs11-session-management",
        "category": "security",
        "language": "java",
        "code_example": "try (Session s = provider.openSession()) { ... }",
        "context": "Always use try-with-resources for PKCS#11 sessions",
        "anti_pattern": "Opening sessions without closing them",
        "source_task_ids": [],
        "source_files": ["KeyService.java"],
        "usage_count": 3,
        "confidence": 0.85,
        "status": "active",
    }
    data.update(overrides)
    return data


# ============================================================================
# Tests: create_pattern
# ============================================================================

class TestCreatePattern:
    def test_creates_pattern_successfully(self):
        client = _mock_client(insert_data=[_sample_pattern(id="pat-new")])
        service = CodePatternService(supabase_client=client)

        ok, result = service.create_pattern(
            project_id="proj-1",
            pattern_name="PKCS#11 Session Management",
            pattern_key="security.java.pkcs11-session-management",
            category="security",
            code_example="try (Session s = ...) { }",
            context="Use try-with-resources",
        )

        assert ok is True
        assert "pattern" in result
        assert result["pattern"]["id"] == "pat-new"

        # Verify insert was called with correct data
        insert_call = client.table.return_value.insert.call_args[0][0]
        assert insert_call["pattern_name"] == "PKCS#11 Session Management"
        assert insert_call["category"] == "security"
        assert insert_call["language"] == "java"
        assert insert_call["status"] == "pending"
        assert insert_call["usage_count"] == 1

    def test_rejects_invalid_category(self):
        client = _mock_client()
        service = CodePatternService(supabase_client=client)

        ok, result = service.create_pattern(
            project_id="proj-1",
            pattern_name="Test",
            pattern_key="test.key",
            category="invalid-category",
            code_example="code",
            context="ctx",
        )

        assert ok is False
        assert "Invalid category" in result["error"]

    def test_rejects_empty_required_fields(self):
        client = _mock_client()
        service = CodePatternService(supabase_client=client)

        ok, result = service.create_pattern(
            project_id="proj-1",
            pattern_name="",
            pattern_key="key",
            category="security",
            code_example="code",
            context="ctx",
        )

        assert ok is False
        assert "required" in result["error"]

    def test_custom_language_and_confidence(self):
        client = _mock_client(insert_data=[_sample_pattern(language="python", confidence=0.95)])
        service = CodePatternService(supabase_client=client)

        ok, result = service.create_pattern(
            project_id="proj-1",
            pattern_name="Pattern",
            pattern_key="testing.python.mock-client",
            category="testing",
            code_example="mock = MagicMock()",
            context="Use MagicMock for Supabase",
            language="python",
            confidence=0.95,
        )

        assert ok is True
        insert_call = client.table.return_value.insert.call_args[0][0]
        assert insert_call["language"] == "python"
        assert insert_call["confidence"] == 0.95

    def test_handles_db_error(self):
        client = _mock_client()
        client.table.return_value.insert.return_value.execute.side_effect = Exception("connection failed")
        service = CodePatternService(supabase_client=client)

        ok, result = service.create_pattern(
            project_id="proj-1",
            pattern_name="P",
            pattern_key="k",
            category="security",
            code_example="c",
            context="x",
        )

        assert ok is False
        assert "Database error" in result["error"]


# ============================================================================
# Tests: find_similar_pattern
# ============================================================================

class TestFindSimilarPattern:
    def test_finds_by_pattern_key(self):
        patterns = [_sample_pattern(usage_count=5), _sample_pattern(id="pat-2", usage_count=2)]
        client = _mock_client(select_data=patterns)
        service = CodePatternService(supabase_client=client)

        ok, result = service.find_similar_pattern("security.java.pkcs11-session-management")

        assert ok is True
        assert result["count"] == 2
        assert len(result["patterns"]) == 2

        # Verify query used eq on pattern_key
        client.table.return_value.select.return_value.eq.assert_called_with(
            "pattern_key", "security.java.pkcs11-session-management"
        )

    def test_returns_empty_when_not_found(self):
        client = _mock_client(select_data=[])
        service = CodePatternService(supabase_client=client)

        ok, result = service.find_similar_pattern("nonexistent.key")

        assert ok is True
        assert result["count"] == 0
        assert result["patterns"] == []

    def test_handles_db_error(self):
        client = _mock_client()
        client.table.return_value.select.return_value.eq.return_value.order.return_value.execute.side_effect = (
            Exception("timeout")
        )
        service = CodePatternService(supabase_client=client)

        ok, result = service.find_similar_pattern("key")

        assert ok is False
        assert "Database error" in result["error"]


# ============================================================================
# Tests: list_patterns
# ============================================================================

class TestListPatterns:
    def test_lists_all_patterns(self):
        patterns = [_sample_pattern(), _sample_pattern(id="pat-2", category="testing")]
        client = _mock_client(select_data=patterns)
        service = CodePatternService(supabase_client=client)

        ok, result = service.list_patterns()

        assert ok is True
        assert result["count"] == 2

    def test_filters_by_project(self):
        client = _mock_client(select_data=[_sample_pattern()])
        service = CodePatternService(supabase_client=client)

        ok, result = service.list_patterns(project_id="proj-1")

        assert ok is True
        # Verify eq was called for project_id filter
        client.table.return_value.select.return_value.eq.assert_called()

    def test_filters_by_category(self):
        client = _mock_client(select_data=[_sample_pattern()])
        service = CodePatternService(supabase_client=client)

        ok, result = service.list_patterns(category="security")

        assert ok is True

    def test_filters_by_status(self):
        client = _mock_client(select_data=[])
        service = CodePatternService(supabase_client=client)

        ok, result = service.list_patterns(status="promoted")

        assert ok is True
        assert result["count"] == 0


# ============================================================================
# Tests: increment_usage
# ============================================================================

class TestIncrementUsage:
    def test_increments_successfully(self):
        client = _mock_client(
            select_data=[{"id": "pat-1", "usage_count": 3}],
            update_data=[_sample_pattern(usage_count=4)],
        )
        service = CodePatternService(supabase_client=client)

        ok, result = service.increment_usage("pat-1")

        assert ok is True
        # Verify update was called with incremented count
        update_call = client.table.return_value.update.call_args[0][0]
        assert update_call["usage_count"] == 4
        assert "last_used_at" in update_call

    def test_not_found(self):
        client = _mock_client(select_data=[])
        service = CodePatternService(supabase_client=client)

        ok, result = service.increment_usage("nonexistent-id")

        assert ok is False
        assert "not found" in result["error"]

    def test_handles_db_error(self):
        client = _mock_client()
        client.table.return_value.select.return_value.eq.return_value.execute.side_effect = Exception("fail")
        service = CodePatternService(supabase_client=client)

        ok, result = service.increment_usage("pat-1")

        assert ok is False
        assert "Database error" in result["error"]


# ============================================================================
# Tests: get_stats
# ============================================================================

class TestGetStats:
    def test_calculates_stats(self):
        patterns = [
            _sample_pattern(category="security", status="active", language="java", usage_count=5, confidence=0.9),
            _sample_pattern(id="pat-2", category="testing", status="pending", language="python", usage_count=2, confidence=0.7),
            _sample_pattern(id="pat-3", category="security", status="active", language="java", usage_count=1, confidence=0.8),
        ]
        client = _mock_client(select_data=patterns)
        service = CodePatternService(supabase_client=client)

        ok, result = service.get_stats()

        assert ok is True
        stats = result["stats"]
        assert stats["total"] == 3
        assert stats["by_category"]["security"] == 2
        assert stats["by_category"]["testing"] == 1
        assert stats["by_status"]["active"] == 2
        assert stats["by_status"]["pending"] == 1
        assert stats["by_language"]["java"] == 2
        assert stats["by_language"]["python"] == 1
        assert stats["total_usage"] == 8
        assert stats["avg_confidence"] == 0.8

    def test_empty_stats(self):
        client = _mock_client(select_data=[])
        service = CodePatternService(supabase_client=client)

        ok, result = service.get_stats()

        assert ok is True
        stats = result["stats"]
        assert stats["total"] == 0
        assert stats["avg_confidence"] == 0.0
        assert stats["total_usage"] == 0

    def test_filters_by_project(self):
        client = _mock_client(select_data=[_sample_pattern()])
        service = CodePatternService(supabase_client=client)

        ok, result = service.get_stats(project_id="proj-1")

        assert ok is True
        client.table.return_value.select.return_value.eq.assert_called()
