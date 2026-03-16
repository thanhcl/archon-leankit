"""Tests for ArchitectReviewer — verdict logic, API handling, decision rules."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.architect_reviewer import (
    ArchitectReviewer,
    ArchitectReviewResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides) -> dict:
    base = {
        "id": "task-r1",
        "title": "Add login endpoint",
        "description": "Implement POST /api/auth/login with JWT",
        "status": "architect-review",
        "priority": "high",
        "assignee": "Agent-1",
        "complexity": "simple",
        "source_app": "leankit",
        "acceptance_criteria": [
            {"text": "POST /api/auth/login returns JWT"},
            {"text": "Invalid credentials return 401"},
        ],
        "retry_count": 0,
        "max_retries": 3,
        "architect_review": None,
        "execution_result": None,
        "rejection_reason": None,
    }
    base.update(overrides)
    return base


def _make_exec_result(**overrides) -> dict:
    base = {
        "exit_code": 0,
        "duration_seconds": 45.0,
        "result": "SUCCESS",
        "files_changed": 3,
        "tests_added": 2,
        "summary": "Implemented login endpoint with JWT token generation",
    }
    base.update(overrides)
    return base


def _mock_api_response(review_json: dict) -> MagicMock:
    """Create a mock httpx response with the given review JSON."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "content": [{"type": "text", "text": json.dumps(review_json)}],
    }
    return resp


def _approve_review(confidence: float = 0.95, findings: list | None = None) -> dict:
    return {
        "verdict": "approve",
        "confidence": confidence,
        "findings": findings or [],
        "feedback": "",
        "summary": "Implementation looks good",
    }


def _changes_review(feedback: str = "Fix the auth logic", findings: list | None = None) -> dict:
    return {
        "verdict": "changes-requested",
        "confidence": 0.9,
        "findings": findings or [{"severity": "warning", "category": "correctness", "description": "Missing validation"}],
        "feedback": feedback,
        "summary": "Needs changes",
    }


def _escalate_review() -> dict:
    return {
        "verdict": "escalate",
        "confidence": 0.85,
        "findings": [{"severity": "critical", "category": "architecture", "description": "Wrong pattern used"}],
        "feedback": "Needs human review",
        "summary": "Architecture concern",
    }


def _security_finding_review() -> dict:
    return {
        "verdict": "approve",
        "confidence": 0.9,
        "findings": [{"severity": "critical", "category": "security", "description": "SQL injection in query builder"}],
        "feedback": "",
        "summary": "Code works but has security issue",
    }


# ---------------------------------------------------------------------------
# Tests: _decide_action
# ---------------------------------------------------------------------------


class TestDecideAction:
    def test_approve_high_confidence(self):
        task = _make_task()
        review = ArchitectReviewResult(verdict="approve", confidence=0.95, summary="Looks good")

        action = ArchitectReviewer._decide_action(task, review)

        assert action.next_status == "review"
        assert action.warning is None

    def test_approve_low_confidence(self):
        task = _make_task()
        review = ArchitectReviewResult(verdict="approve", confidence=0.6, summary="Probably ok")

        action = ArchitectReviewer._decide_action(task, review)

        assert action.next_status == "review"
        assert action.warning is not None
        assert "0.60" in action.warning

    def test_changes_requested_retry_available(self):
        task = _make_task(retry_count=1, max_retries=3)
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.9, feedback="Fix auth logic",
        )

        action = ArchitectReviewer._decide_action(task, review)

        assert action.next_status == "assigned"
        assert "Fix auth logic" in action.reason

    def test_changes_requested_max_retries_reached(self):
        task = _make_task(retry_count=3, max_retries=3)
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.9, feedback="Still broken",
        )

        action = ArchitectReviewer._decide_action(task, review)

        assert action.next_status == "escalated"
        assert "Max retries" in action.reason

    def test_escalate_verdict(self):
        task = _make_task()
        review = ArchitectReviewResult(
            verdict="escalate", confidence=0.85, feedback="Needs human review",
        )

        action = ArchitectReviewer._decide_action(task, review)

        assert action.next_status == "escalated"

    def test_critical_security_always_escalates(self):
        """Even with approve verdict, critical security findings force escalation."""
        task = _make_task()
        review = ArchitectReviewResult(
            verdict="approve",
            confidence=0.95,
            findings=[{"severity": "critical", "category": "security", "description": "SQL injection"}],
            summary="Looks good otherwise",
        )

        action = ArchitectReviewer._decide_action(task, review)

        assert action.next_status == "escalated"
        assert "security" in action.reason.lower()

    def test_critical_non_security_does_not_escalate(self):
        """Critical findings in non-security categories don't force escalation."""
        task = _make_task()
        review = ArchitectReviewResult(
            verdict="approve",
            confidence=0.9,
            findings=[{"severity": "critical", "category": "performance", "description": "N+1 query"}],
        )

        action = ArchitectReviewer._decide_action(task, review)

        assert action.next_status == "review"

    def test_error_in_review_escalates(self):
        task = _make_task()
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.0, error="API timeout",
        )

        action = ArchitectReviewer._decide_action(task, review)

        assert action.next_status == "escalated"

    def test_auth_category_also_escalates(self):
        task = _make_task()
        review = ArchitectReviewResult(
            verdict="approve",
            confidence=0.9,
            findings=[{"severity": "critical", "category": "auth", "description": "Auth bypass"}],
        )

        action = ArchitectReviewer._decide_action(task, review)

        assert action.next_status == "escalated"


# ---------------------------------------------------------------------------
# Tests: _parse_review
# ---------------------------------------------------------------------------


class TestParseReview:
    def test_valid_json(self):
        raw = json.dumps(_approve_review())
        result = ArchitectReviewer._parse_review(raw)

        assert result.verdict == "approve"
        assert result.confidence == 0.95
        assert result.summary == "Implementation looks good"

    def test_json_in_markdown_fence(self):
        raw = "```json\n" + json.dumps(_approve_review()) + "\n```"
        result = ArchitectReviewer._parse_review(raw)

        assert result.verdict == "approve"

    def test_invalid_json(self):
        result = ArchitectReviewer._parse_review("not json at all")

        assert result.verdict == "escalate"
        assert result.error is not None

    def test_invalid_verdict_defaults_to_escalate(self):
        raw = json.dumps({"verdict": "invalid", "confidence": 0.5})
        result = ArchitectReviewer._parse_review(raw)

        assert result.verdict == "escalate"

    def test_confidence_clamped(self):
        raw = json.dumps({"verdict": "approve", "confidence": 1.5})
        result = ArchitectReviewer._parse_review(raw)

        assert result.confidence == 1.0

    def test_negative_confidence(self):
        raw = json.dumps({"verdict": "approve", "confidence": -0.5})
        result = ArchitectReviewer._parse_review(raw)

        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Tests: _build_user_message
# ---------------------------------------------------------------------------


class TestBuildUserMessage:
    def test_includes_task_details(self):
        task = _make_task()
        exec_result = _make_exec_result()

        msg = ArchitectReviewer._build_user_message(task, exec_result)

        assert "Add login endpoint" in msg
        assert "JWT" in msg
        assert "POST /api/auth/login returns JWT" in msg

    def test_includes_execution_stats(self):
        exec_result = _make_exec_result()

        msg = ArchitectReviewer._build_user_message(_make_task(), exec_result)

        assert "Exit code: 0" in msg
        assert "Files changed: 3" in msg
        assert "Tests added: 2" in msg


# ---------------------------------------------------------------------------
# Tests: Claude API integration (mocked)
# ---------------------------------------------------------------------------


class TestCallClaude:
    @pytest.mark.asyncio
    async def test_successful_api_call(self):
        reviewer = ArchitectReviewer(api_key="test-key")
        mock_resp = _mock_api_response(_approve_review())

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await reviewer._call_claude(_make_task(), _make_exec_result())

        assert result.verdict == "approve"
        assert result.confidence == 0.95

    @pytest.mark.asyncio
    async def test_no_api_key(self):
        reviewer = ArchitectReviewer(api_key="")

        result = await reviewer._call_claude(_make_task(), _make_exec_result())

        assert result.verdict == "escalate"
        assert "API_KEY" in result.error or "API key" in result.error

    @pytest.mark.asyncio
    async def test_rate_limited(self):
        reviewer = ArchitectReviewer(api_key="test-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await reviewer._call_claude(_make_task(), _make_exec_result())

        assert result.verdict == "escalate"
        assert "rate limited" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_api_error(self):
        reviewer = ArchitectReviewer(api_key="test-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await reviewer._call_claude(_make_task(), _make_exec_result())

        assert result.verdict == "escalate"
        assert "500" in result.error

    @pytest.mark.asyncio
    async def test_timeout(self):
        import httpx as httpx_mod

        reviewer = ArchitectReviewer(api_key="test-key")

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx_mod.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await reviewer._call_claude(_make_task(), _make_exec_result())

        assert result.verdict == "escalate"
        assert "timeout" in result.error.lower()


# ---------------------------------------------------------------------------
# Tests: full review() flow
# ---------------------------------------------------------------------------


class TestReview:
    @pytest.mark.asyncio
    async def test_approve_flow(self):
        reviewer = ArchitectReviewer(api_key="test-key")
        mock_resp = _mock_api_response(_approve_review(confidence=0.95))

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            review_result, action = await reviewer.review(_make_task(), _make_exec_result())

        assert review_result.verdict == "approve"
        assert action.next_status == "review"

    @pytest.mark.asyncio
    async def test_changes_requested_flow(self):
        reviewer = ArchitectReviewer(api_key="test-key")
        mock_resp = _mock_api_response(_changes_review("Fix the auth flow"))

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            review_result, action = await reviewer.review(
                _make_task(retry_count=0), _make_exec_result(),
            )

        assert review_result.verdict == "changes-requested"
        assert action.next_status == "assigned"
        assert "Fix the auth flow" in action.reason

    @pytest.mark.asyncio
    async def test_security_override(self):
        reviewer = ArchitectReviewer(api_key="test-key")
        mock_resp = _mock_api_response(_security_finding_review())

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            review_result, action = await reviewer.review(_make_task(), _make_exec_result())

        assert review_result.verdict == "approve"  # Claude said approve
        assert action.next_status == "escalated"   # But security overrides
