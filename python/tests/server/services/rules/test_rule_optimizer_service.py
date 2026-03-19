"""Tests for RuleOptimizerService — task metric analysis and rule suggestions."""

from unittest.mock import MagicMock

import pytest

from src.server.services.rules.rule_optimizer_service import (
    HIGH_RETRY_THRESHOLD,
    MIN_TASKS_FOR_ANALYSIS,
    PATTERN_CONFIDENCE_THRESHOLD,
    RECURRING_LEARNING_THRESHOLD,
    RuleOptimizerService,
    _compute_confidence,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "title": "Test task",
        "status": "done",
        "retry_count": 0,
        "complexity": "simple",
        "priority": "medium",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T01:00:00",
        "state_changed_at": "2026-01-01T01:00:00",
        "execution_result": None,
    }
    base.update(overrides)
    return base


def _make_learning(**overrides):
    base = {
        "id": "learn-001",
        "project_id": "proj-123",
        "task_id": "task-001",
        "type": "error",
        "description": "Missing validation on input",
        "area": "backend",
        "suggested_rule": "Always validate API inputs before processing.",
        "pattern_key": "error:backend:missing validation",
        "recurrence_count": 1,
        "related_tasks": ["task-001"],
        "status": "pending",
    }
    base.update(overrides)
    return base


def _make_code_pattern(**overrides):
    base = {
        "id": "pat-001",
        "project_id": "proj-123",
        "pattern_name": "Input Validation Guard",
        "pattern_key": "security.python.input-validation-guard",
        "category": "security",
        "language": "python",
        "code_example": "if not data:\n    raise ValueError('empty')",
        "context": "Use at API boundaries for input validation",
        "usage_count": 1,
        "confidence": 0.7,
        "status": "pending",
    }
    base.update(overrides)
    return base


def _make_rule(**overrides):
    base = {
        "id": "rule-001",
        "project_id": None,
        "section": "validation",
        "rule_text": "Always validate inputs.",
        "priority": 10,
        "source": "manual",
        "enabled": True,
    }
    base.update(overrides)
    return base


def _mock_client(
    tasks=None,
    learnings=None,
    code_patterns=None,
    rules=None,
):
    """Build a mock Supabase client that returns different data per table."""
    client = MagicMock()

    table_data = {
        "archon_tasks": tasks or [],
        "archon_learnings": learnings or [],
        "archon_code_patterns": code_patterns or [],
        "archon_rules": rules or [],
    }

    def make_table_mock(table_name):
        data = table_data.get(table_name, [])
        table = MagicMock()
        select = MagicMock()
        select.eq.return_value = select
        select.or_.return_value = select
        select.gte.return_value = select
        select.order.return_value = select
        select.limit.return_value = select
        select.in_.return_value = select
        execute_result = MagicMock()
        execute_result.data = data
        select.execute.return_value = execute_result
        table.select.return_value = select
        return table

    def table_side_effect(name):
        return make_table_mock(name)

    client.table.side_effect = table_side_effect
    return client


# ---------------------------------------------------------------------------
# Tests: _compute_confidence
# ---------------------------------------------------------------------------


class TestComputeConfidence:
    def test_base_confidence(self):
        score = _compute_confidence(recurrence=1, supporting_tasks=0)
        assert score == 0.5

    def test_increases_with_recurrence(self):
        score = _compute_confidence(recurrence=4, supporting_tasks=0)
        assert score > 0.5
        assert score == 0.8  # 0.5 + 3*0.1

    def test_increases_with_tasks(self):
        score = _compute_confidence(recurrence=1, supporting_tasks=3)
        assert score == 0.65  # 0.5 + 3*0.05

    def test_caps_at_max(self):
        score = _compute_confidence(recurrence=20, supporting_tasks=20)
        assert score == 0.95

    def test_combined(self):
        score = _compute_confidence(recurrence=3, supporting_tasks=2)
        assert score == 0.8  # 0.5 + 2*0.1 + 2*0.05


# ---------------------------------------------------------------------------
# Tests: optimize_rules — insufficient data
# ---------------------------------------------------------------------------


class TestOptimizeRulesInsufficientData:
    @pytest.mark.asyncio
    async def test_returns_empty_when_too_few_tasks(self):
        tasks = [_make_task(id=f"t-{i}") for i in range(MIN_TASKS_FOR_ANALYSIS - 1)]
        client = _mock_client(tasks=tasks)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        assert result["suggestions"] == []
        assert "Not enough tasks" in result["analysis"]["message"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_tasks(self):
        client = _mock_client(tasks=[])
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        assert result["suggestions"] == []


# ---------------------------------------------------------------------------
# Tests: optimize_rules — retry pattern suggestions
# ---------------------------------------------------------------------------


class TestRetryPatternSuggestions:
    @pytest.mark.asyncio
    async def test_suggests_rule_for_high_retry_area(self):
        """Tasks with high retry in the same area should trigger a suggestion."""
        tasks = [
            _make_task(
                id=f"t-{i}",
                retry_count=HIGH_RETRY_THRESHOLD,
                execution_result={
                    "learnings": [{"area": "backend", "description": "test"}],
                },
            )
            for i in range(4)
        ]
        client = _mock_client(tasks=tasks)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        suggestions = result["suggestions"]
        backend_suggestions = [s for s in suggestions if "backend" in s.get("rule_text", "").lower()]
        assert len(backend_suggestions) >= 1
        assert backend_suggestions[0]["action"] == "add"
        assert backend_suggestions[0]["confidence"] > 0.5

    @pytest.mark.asyncio
    async def test_no_suggestion_when_retry_rate_low(self):
        """Low retry rate should not trigger retry-based suggestions."""
        tasks = [_make_task(id=f"t-{i}", retry_count=0) for i in range(5)]
        client = _mock_client(tasks=tasks)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        retry_suggestions = [
            s for s in result["suggestions"]
            if "retries" in s.get("reason", "").lower() or "retry" in s.get("rule_text", "").lower()
        ]
        assert len(retry_suggestions) == 0


# ---------------------------------------------------------------------------
# Tests: optimize_rules — learning-based suggestions
# ---------------------------------------------------------------------------


class TestLearningSuggestions:
    @pytest.mark.asyncio
    async def test_suggests_from_recurring_learning(self):
        """Learnings recurring >= threshold with suggested_rule should become suggestions."""
        tasks = [_make_task(id=f"t-{i}") for i in range(3)]
        learnings = [
            _make_learning(
                recurrence_count=RECURRING_LEARNING_THRESHOLD,
                related_tasks=["t-0", "t-1"],
                suggested_rule="Always validate API inputs before processing.",
            ),
        ]
        client = _mock_client(tasks=tasks, learnings=learnings)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        suggestions = result["suggestions"]
        learning_suggestions = [s for s in suggestions if s.get("evidence", {}).get("learning_id")]
        assert len(learning_suggestions) >= 1
        assert "validate API inputs" in learning_suggestions[0]["rule_text"]

    @pytest.mark.asyncio
    async def test_skips_learning_without_suggested_rule(self):
        """Learnings without suggested_rule should not generate suggestions."""
        tasks = [_make_task(id=f"t-{i}") for i in range(3)]
        learnings = [
            _make_learning(
                recurrence_count=5,
                suggested_rule=None,
            ),
        ]
        client = _mock_client(tasks=tasks, learnings=learnings)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        learning_suggestions = [s for s in result["suggestions"] if s.get("evidence", {}).get("learning_id")]
        assert len(learning_suggestions) == 0

    @pytest.mark.asyncio
    async def test_skips_learning_below_threshold(self):
        tasks = [_make_task(id=f"t-{i}") for i in range(3)]
        learnings = [_make_learning(recurrence_count=1)]
        client = _mock_client(tasks=tasks, learnings=learnings)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        learning_suggestions = [s for s in result["suggestions"] if s.get("evidence", {}).get("learning_id")]
        assert len(learning_suggestions) == 0


# ---------------------------------------------------------------------------
# Tests: optimize_rules — code pattern suggestions
# ---------------------------------------------------------------------------


class TestCodePatternSuggestions:
    @pytest.mark.asyncio
    async def test_suggests_from_high_confidence_pattern(self):
        tasks = [_make_task(id=f"t-{i}") for i in range(3)]
        patterns = [
            _make_code_pattern(
                confidence=PATTERN_CONFIDENCE_THRESHOLD + 0.05,
                usage_count=3,
                pattern_name="Error Boundary Pattern",
                category="error-handling",
                context="Wrap API calls with error boundaries",
            ),
        ]
        client = _mock_client(tasks=tasks, code_patterns=patterns)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        pattern_suggestions = [s for s in result["suggestions"] if s.get("evidence", {}).get("pattern_id")]
        assert len(pattern_suggestions) >= 1
        assert "Error Boundary Pattern" in pattern_suggestions[0]["rule_text"]

    @pytest.mark.asyncio
    async def test_skips_low_confidence_pattern(self):
        tasks = [_make_task(id=f"t-{i}") for i in range(3)]
        patterns = [_make_code_pattern(confidence=0.5, usage_count=1)]
        client = _mock_client(tasks=tasks, code_patterns=patterns)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        pattern_suggestions = [s for s in result["suggestions"] if s.get("evidence", {}).get("pattern_id")]
        assert len(pattern_suggestions) == 0


# ---------------------------------------------------------------------------
# Tests: optimize_rules — failure pattern suggestions
# ---------------------------------------------------------------------------


class TestFailurePatternSuggestions:
    @pytest.mark.asyncio
    async def test_suggests_on_high_failure_rate(self):
        """High failure rate without specific areas triggers generic suggestion."""
        tasks = [
            _make_task(id="t-0", status="failed"),
            _make_task(id="t-1", status="failed"),
            _make_task(id="t-2", status="done"),
        ]
        client = _mock_client(tasks=tasks)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        assert result["analysis"]["failure_rate"] > 0
        # Should have at least one failure-related suggestion
        failure_suggestions = [
            s for s in result["suggestions"]
            if "failure" in s.get("reason", "").lower() or "fail" in s.get("rule_text", "").lower()
        ]
        assert len(failure_suggestions) >= 1

    @pytest.mark.asyncio
    async def test_no_suggestion_when_failure_rate_low(self):
        tasks = [_make_task(id=f"t-{i}", status="done") for i in range(5)]
        client = _mock_client(tasks=tasks)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        assert result["analysis"]["failure_rate"] == 0


# ---------------------------------------------------------------------------
# Tests: overlap detection
# ---------------------------------------------------------------------------


class TestOverlapDetection:
    @pytest.mark.asyncio
    async def test_skips_suggestion_overlapping_existing_rule(self):
        """Learnings that overlap with existing rules should be skipped."""
        tasks = [_make_task(id=f"t-{i}") for i in range(3)]
        learnings = [
            _make_learning(
                recurrence_count=5,
                suggested_rule="Always validate inputs before processing.",
            ),
        ]
        existing_rules = [
            _make_rule(rule_text="Always validate inputs before processing them."),
        ]
        client = _mock_client(tasks=tasks, learnings=learnings, rules=existing_rules)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        learning_suggestions = [s for s in result["suggestions"] if s.get("evidence", {}).get("learning_id")]
        assert len(learning_suggestions) == 0


# ---------------------------------------------------------------------------
# Tests: analysis metrics
# ---------------------------------------------------------------------------


class TestAnalysisMetrics:
    @pytest.mark.asyncio
    async def test_analysis_contains_expected_fields(self):
        tasks = [
            _make_task(id="t-0", status="done", retry_count=0),
            _make_task(id="t-1", status="done", retry_count=3),
            _make_task(id="t-2", status="failed", retry_count=2),
        ]
        client = _mock_client(tasks=tasks)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        analysis = result["analysis"]
        assert analysis["total_tasks"] == 3
        assert analysis["done_count"] == 2
        assert analysis["failed_count"] == 1
        assert analysis["high_retry_count"] == 2
        assert analysis["total_retries"] == 5
        assert "failure_rate" in analysis
        assert "complexity_distribution" in analysis


# ---------------------------------------------------------------------------
# Tests: deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_deduplicates_by_section_action(self):
        """Only the highest confidence suggestion per section+action survives."""
        tasks = [
            _make_task(
                id=f"t-{i}",
                status="failed",
                retry_count=HIGH_RETRY_THRESHOLD,
                execution_result={
                    "learnings": [
                        {"area": "backend", "description": "error A"},
                        {"area": "backend", "description": "error B"},
                    ],
                },
            )
            for i in range(4)
        ]
        client = _mock_client(tasks=tasks)
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        assert ok is True
        # Count suggestions per section+action combo
        combos = [(s["section"], s["action"]) for s in result["suggestions"]]
        assert len(combos) == len(set(combos)), "Duplicate section+action found"


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_gracefully_handles_db_error_on_fetch(self):
        """Individual fetch failures return empty lists; optimize still succeeds."""
        client = MagicMock()
        client.table.side_effect = Exception("DB down")
        service = RuleOptimizerService(supabase_client=client)

        ok, result = await service.optimize_rules("proj-123")

        # Fetch failures are caught per-table; returns empty suggestions
        assert ok is True
        assert result["suggestions"] == []
        assert "Not enough tasks" in result["analysis"]["message"]

    @pytest.mark.asyncio
    async def test_handles_unexpected_error_in_optimize(self):
        """Unexpected error in the main flow is caught and returns failure."""
        service = RuleOptimizerService(supabase_client=MagicMock())
        # Monkey-patch _fetch_tasks to raise inside optimize_rules try block
        service._fetch_tasks = MagicMock(side_effect=RuntimeError("unexpected"))

        ok, result = await service.optimize_rules("proj-123")

        assert ok is False
        assert "Error" in result["error"]


# ---------------------------------------------------------------------------
# Tests: helper methods
# ---------------------------------------------------------------------------


class TestHelperMethods:
    def test_area_to_section_mapping(self):
        assert RuleOptimizerService._area_to_section("frontend") == "coding-style"
        assert RuleOptimizerService._area_to_section("security") == "security"
        assert RuleOptimizerService._area_to_section("tests") == "testing"
        assert RuleOptimizerService._area_to_section("database") == "architecture"
        assert RuleOptimizerService._area_to_section("unknown") == "validation"

    def test_category_to_section_mapping(self):
        assert RuleOptimizerService._category_to_section("security") == "security"
        assert RuleOptimizerService._category_to_section("testing") == "testing"
        assert RuleOptimizerService._category_to_section("performance") == "performance"
        assert RuleOptimizerService._category_to_section("unknown") == "coding-style"

    def test_text_overlaps_existing(self):
        rules = [_make_rule(rule_text="Always validate user inputs before database queries.")]
        assert RuleOptimizerService._text_overlaps_existing(rules, "Always validate user inputs before processing.") is True
        assert RuleOptimizerService._text_overlaps_existing(rules, "Use TypeScript strict mode.") is False

    def test_text_overlap_short_text_no_match(self):
        rules = [_make_rule(rule_text="Some rule.")]
        assert RuleOptimizerService._text_overlaps_existing(rules, "ab") is False

    def test_rule_exists_for_area(self):
        rules = [_make_rule(section="coding-style", rule_text="Backend services must handle errors.")]
        assert RuleOptimizerService._rule_exists_for_area(rules, "coding-style", "backend") is True
        assert RuleOptimizerService._rule_exists_for_area(rules, "coding-style", "frontend") is False
        assert RuleOptimizerService._rule_exists_for_area(rules, "testing", "backend") is False
