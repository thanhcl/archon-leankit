"""Tests for BugTaskCreator — auto-creates bug tasks from review findings."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.bug_task_creator import BugTaskCreator, _truncate


def _make_parent_task(**overrides):
    base = {
        "id": "parent-001",
        "project_id": "proj-001",
        "title": "Implement auth endpoint",
        "status": "architect-review",
    }
    base.update(overrides)
    return base


def _make_findings(*severities):
    """Create findings list with given severities."""
    findings = []
    for i, severity in enumerate(severities):
        findings.append({
            "severity": severity,
            "category": f"category-{i}",
            "description": f"Finding {i} description",
        })
    return findings


def _setup_creator():
    """Create a BugTaskCreator with mocked task_service."""
    task_service = MagicMock()
    task_service.create_task = AsyncMock(return_value=(
        True,
        {"task": {"id": "bug-001", "status": "approved", "priority": "high"}},
    ))
    creator = BugTaskCreator(task_service=task_service)
    return creator, task_service


class TestCriticalFindings:
    @pytest.mark.asyncio
    async def test_critical_creates_approved_high_priority_task(self):
        creator, task_service = _setup_creator()
        parent = _make_parent_task()
        findings = [{"severity": "critical", "category": "security", "description": "SQL injection found"}]

        result = await creator.create_bug_tasks_from_findings(parent, findings)

        assert len(result) == 1
        task_service.create_task.assert_called_once()
        call_kwargs = task_service.create_task.call_args[1]
        assert call_kwargs["status"] == "approved"
        assert call_kwargs["priority"] == "high"
        assert call_kwargs["blocked_by"] == ["parent-001"]
        assert call_kwargs["source_app"] == "architect-review"

    @pytest.mark.asyncio
    async def test_critical_task_references_parent(self):
        creator, task_service = _setup_creator()
        parent = _make_parent_task()
        findings = [{"severity": "critical", "category": "auth", "description": "Missing auth check"}]

        await creator.create_bug_tasks_from_findings(parent, findings)

        call_kwargs = task_service.create_task.call_args[1]
        assert "parent-001" in call_kwargs["description"]
        assert "Implement auth endpoint" in call_kwargs["description"]


class TestWarningFindings:
    @pytest.mark.asyncio
    async def test_warning_creates_draft_medium_priority_task(self):
        creator, task_service = _setup_creator()
        task_service.create_task = AsyncMock(return_value=(
            True,
            {"task": {"id": "bug-002", "status": "draft", "priority": "medium"}},
        ))
        parent = _make_parent_task()
        findings = [{"severity": "warning", "category": "performance", "description": "N+1 query detected"}]

        result = await creator.create_bug_tasks_from_findings(parent, findings)

        assert len(result) == 1
        call_kwargs = task_service.create_task.call_args[1]
        assert call_kwargs["status"] == "draft"
        assert call_kwargs["priority"] == "medium"
        assert call_kwargs["blocked_by"] == ["parent-001"]

    @pytest.mark.asyncio
    async def test_warning_task_references_parent(self):
        creator, task_service = _setup_creator()
        task_service.create_task = AsyncMock(return_value=(
            True,
            {"task": {"id": "bug-002", "status": "draft", "priority": "medium"}},
        ))
        parent = _make_parent_task()
        findings = [{"severity": "warning", "category": "perf", "description": "Slow query"}]

        await creator.create_bug_tasks_from_findings(parent, findings)

        call_kwargs = task_service.create_task.call_args[1]
        assert "parent-001" in call_kwargs["description"]


class TestSuggestionFindings:
    @pytest.mark.asyncio
    async def test_suggestions_are_logged_not_created(self):
        creator, task_service = _setup_creator()
        parent = _make_parent_task()
        findings = _make_findings("suggestion", "suggestion")

        result = await creator.create_bug_tasks_from_findings(parent, findings)

        assert len(result) == 0
        task_service.create_task.assert_not_called()


class TestMixedFindings:
    @pytest.mark.asyncio
    async def test_mixed_severities_only_create_for_critical_and_warning(self):
        creator, task_service = _setup_creator()
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            return True, {"task": {"id": f"bug-{call_count}", "status": kwargs.get("status"), "priority": kwargs.get("priority")}}

        task_service.create_task = mock_create
        parent = _make_parent_task()
        findings = _make_findings("critical", "warning", "suggestion", "critical")

        result = await creator.create_bug_tasks_from_findings(parent, findings)

        assert len(result) == 3  # 2 critical + 1 warning
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_empty_findings_returns_empty(self):
        creator, task_service = _setup_creator()
        parent = _make_parent_task()

        result = await creator.create_bug_tasks_from_findings(parent, [])

        assert result == []
        task_service.create_task.assert_not_called()


class TestFileAndLineInfo:
    @pytest.mark.asyncio
    async def test_file_and_line_included_in_description(self):
        creator, task_service = _setup_creator()
        parent = _make_parent_task()
        findings = [{
            "severity": "critical",
            "category": "security",
            "description": "Hardcoded secret",
            "file": "src/config.py",
            "line": "42",
        }]

        await creator.create_bug_tasks_from_findings(parent, findings)

        call_kwargs = task_service.create_task.call_args[1]
        assert "src/config.py" in call_kwargs["description"]
        assert "line 42" in call_kwargs["description"]

    @pytest.mark.asyncio
    async def test_file_without_line(self):
        creator, task_service = _setup_creator()
        parent = _make_parent_task()
        findings = [{
            "severity": "warning",
            "category": "style",
            "description": "Long function",
            "file": "src/utils.py",
        }]

        await creator.create_bug_tasks_from_findings(parent, findings)

        call_kwargs = task_service.create_task.call_args[1]
        assert "src/utils.py" in call_kwargs["description"]


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_missing_parent_id_returns_empty(self):
        creator, task_service = _setup_creator()
        parent = {"project_id": "proj-001", "title": "Test"}

        result = await creator.create_bug_tasks_from_findings(parent, _make_findings("critical"))

        assert result == []
        task_service.create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_project_id_returns_empty(self):
        creator, task_service = _setup_creator()
        parent = {"id": "task-001", "title": "Test"}

        result = await creator.create_bug_tasks_from_findings(parent, _make_findings("critical"))

        assert result == []
        task_service.create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_task_creation_failure_skips_and_continues(self):
        creator, task_service = _setup_creator()
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return False, {"error": "DB error"}
            return True, {"task": {"id": "bug-002", "status": "approved", "priority": "high"}}

        task_service.create_task = mock_create
        parent = _make_parent_task()
        findings = _make_findings("critical", "critical")

        result = await creator.create_bug_tasks_from_findings(parent, findings)

        assert len(result) == 1  # First fails, second succeeds
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_unknown_severity_skipped(self):
        creator, task_service = _setup_creator()
        parent = _make_parent_task()
        findings = [{"severity": "info", "category": "style", "description": "Minor thing"}]

        result = await creator.create_bug_tasks_from_findings(parent, findings)

        assert result == []
        task_service.create_task.assert_not_called()


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_exact_length_unchanged(self):
        assert _truncate("hello", 5) == "hello"

    def test_long_text_truncated(self):
        result = _truncate("a" * 100, 20)
        assert len(result) == 20
        assert result.endswith("...")

    def test_title_format(self):
        creator, _ = _setup_creator()
        # Title should be readable and contain severity + category
        # Just testing the format indirectly through _truncate
        assert _truncate("SQL injection in auth module causing data leak", 30) == "SQL injection in auth modul..."
