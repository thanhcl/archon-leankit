"""Tests for hybrid ArchitectReviewer — mode selection, self-review,
multi-provider API, decision logic, security escalation, fallback."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.architect_reviewer import (
    ArchitectReviewer,
    ArchitectReviewResult,
    ReviewConfig,
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
        "summary": "Implemented login endpoint",
        "stdout": "",
    }
    base.update(overrides)
    return base


def _mock_anthropic_response(review_json: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "content": [{"type": "text", "text": json.dumps(review_json)}],
    }
    resp.text = json.dumps(review_json)
    return resp


def _mock_openai_response(review_json: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(review_json)}}],
    }
    resp.text = json.dumps(review_json)
    return resp


def _approve_review(confidence: float = 0.95) -> dict:
    return {
        "verdict": "approve",
        "confidence": confidence,
        "findings": [],
        "feedback": "",
        "summary": "Implementation looks good",
    }


def _changes_review(feedback: str = "Fix the auth logic") -> dict:
    return {
        "verdict": "changes-requested",
        "confidence": 0.9,
        "findings": [{"severity": "warning", "category": "correctness", "description": "Missing validation"}],
        "feedback": feedback,
        "summary": "Needs changes",
    }


def _async_cred(mapping: dict[str, str | None]):
    """Return a credential_getter that uses a mapping."""
    async def getter(key: str):
        return mapping.get(key)
    return getter


# ---------------------------------------------------------------------------
# Tests: _get_mode
# ---------------------------------------------------------------------------


class TestGetMode:
    def test_simple_task_default_self_review(self):
        task = _make_task(complexity="simple", title="Add button", description="Add a button to UI")
        config = ReviewConfig()
        assert ArchitectReviewer._get_mode(task, config) == "self-review"

    def test_complex_task_default_api(self):
        task = _make_task(complexity="complex", title="Refactor DB layer", description="Restructure models")
        config = ReviewConfig()
        assert ArchitectReviewer._get_mode(task, config) == "api"

    def test_security_sensitive_overrides(self):
        task = _make_task(complexity="simple", title="Fix authentication bypass")
        config = ReviewConfig(security_sensitive_mode="api")
        assert ArchitectReviewer._get_mode(task, config) == "api"

    def test_security_keywords_in_description(self):
        task = _make_task(title="Update config", description="Change the encryption key rotation policy")
        config = ReviewConfig()
        assert ArchitectReviewer._get_mode(task, config) == "api"

    def test_custom_modes(self):
        task = _make_task(complexity="simple", title="Add button", description="UI")
        config = ReviewConfig(simple_task_mode="api")
        assert ArchitectReviewer._get_mode(task, config) == "api"


# ---------------------------------------------------------------------------
# Tests: _is_security_sensitive
# ---------------------------------------------------------------------------


class TestIsSecuritySensitive:
    @pytest.mark.parametrize("title", [
        "Fix auth bypass", "Update JWT token handling", "Add password hashing",
        "Configure TLS certificates", "Implement OAuth flow", "Encrypt user data",
        "HSM integration for key management", "PKCS#11 token storage",
    ])
    def test_detects_security_keywords(self, title):
        assert ArchitectReviewer._is_security_sensitive(_make_task(title=title))

    @pytest.mark.parametrize("title", [
        "Add pagination", "Fix CSS layout", "Update README", "Refactor service layer",
    ])
    def test_non_security_tasks(self, title):
        assert not ArchitectReviewer._is_security_sensitive(
            _make_task(title=title, description="Normal task")
        )


# ---------------------------------------------------------------------------
# Tests: _parse_self_review
# ---------------------------------------------------------------------------


class TestParseSelfReview:
    def test_pass_verdict(self):
        result = ArchitectReviewer._parse_self_review({
            "stdout": "Some output...\nSELF_REVIEW: PASS\nREVIEW_CONFIDENCE: 0.92\n",
        })
        assert result.verdict == "approve"
        assert result.confidence == 0.92
        assert result.mode == "self-review"

    def test_needs_attention_verdict(self):
        result = ArchitectReviewer._parse_self_review({
            "stdout": "SELF_REVIEW: NEEDS_ATTENTION\nREVIEW_CONFIDENCE: 0.6\n",
        })
        assert result.verdict == "changes-requested"
        assert result.confidence == 0.6

    def test_with_findings(self):
        findings = [{"severity": "suggestion", "category": "perf", "description": "Add index"}]
        result = ArchitectReviewer._parse_self_review({
            "stdout": f"SELF_REVIEW: PASS\nREVIEW_FINDINGS: {json.dumps(findings)}\n",
        })
        assert result.verdict == "approve"
        assert len(result.findings) == 1
        assert result.findings[0]["category"] == "perf"

    def test_no_self_review_block(self):
        result = ArchitectReviewer._parse_self_review({"stdout": "Just normal output"})
        assert result.verdict == "changes-requested"
        assert result.confidence == 0.5

    def test_empty_stdout(self):
        result = ArchitectReviewer._parse_self_review({"stdout": ""})
        assert result.verdict == "changes-requested"


# ---------------------------------------------------------------------------
# Tests: Anthropic provider (mock API)
# ---------------------------------------------------------------------------


class TestAnthropicProvider:
    @pytest.mark.asyncio
    async def test_approve_flow(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({"ANTHROPIC_API_KEY": "test-key"}))
        config = ReviewConfig(simple_task_mode="api", provider="anthropic")
        mock_resp = _mock_anthropic_response(_approve_review())

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            # Force non-security title
            task = _make_task(title="Add button", description="UI work", complexity="simple")
            result, action = await reviewer.review(task, _make_exec_result(), config)

        assert result.verdict == "approve"
        assert result.provider == "anthropic"
        assert result.mode == "api"
        assert action.next_status == "review"


# ---------------------------------------------------------------------------
# Tests: OpenAI provider (mock API)
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    @pytest.mark.asyncio
    async def test_approve_flow(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({"OPENAI_API_KEY": "test-key"}))
        config = ReviewConfig(simple_task_mode="api", provider="openai")
        mock_resp = _mock_openai_response(_approve_review())

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            task = _make_task(title="Add button", description="UI work", complexity="simple")
            result, action = await reviewer.review(task, _make_exec_result(), config)

        assert result.verdict == "approve"
        assert result.provider == "openai"
        assert action.next_status == "review"


# ---------------------------------------------------------------------------
# Tests: Google provider (mock API — uses OpenAI-compatible)
# ---------------------------------------------------------------------------


class TestGoogleProvider:
    @pytest.mark.asyncio
    async def test_approve_flow(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({"GOOGLE_API_KEY": "test-key"}))
        config = ReviewConfig(simple_task_mode="api", provider="google")
        mock_resp = _mock_openai_response(_approve_review())

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            task = _make_task(title="Add button", description="UI work", complexity="simple")
            result, action = await reviewer.review(task, _make_exec_result(), config)

        assert result.verdict == "approve"
        assert result.provider == "google"


# ---------------------------------------------------------------------------
# Tests: API fallback to self-review
# ---------------------------------------------------------------------------


class TestAPIFallback:
    @pytest.mark.asyncio
    async def test_fallback_on_no_api_key(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({}))
        config = ReviewConfig(simple_task_mode="api", api_fallback_to_self_review=True)

        task = _make_task(title="Add button", description="UI", complexity="simple")
        exec_result = _make_exec_result(stdout="SELF_REVIEW: PASS\nREVIEW_CONFIDENCE: 0.85\n")

        result, action = await reviewer.review(task, exec_result, config)

        assert result.mode == "self-review"
        assert result.error is not None
        assert "API failed" in result.error

    @pytest.mark.asyncio
    async def test_no_fallback_escalates(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({}))
        config = ReviewConfig(simple_task_mode="api", api_fallback_to_self_review=False)

        task = _make_task(title="Add button", description="UI", complexity="simple")
        result, action = await reviewer.review(task, _make_exec_result(), config)

        assert result.verdict == "escalate"
        assert action.next_status == "escalated"

    @pytest.mark.asyncio
    async def test_fallback_on_rate_limit(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({"ANTHROPIC_API_KEY": "key"}))
        config = ReviewConfig(simple_task_mode="api", api_fallback_to_self_review=True)

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            task = _make_task(title="Add button", description="UI", complexity="simple")
            result, _ = await reviewer.review(task, _make_exec_result(), config)

        # Rate limited → escalate (API returned a result, not an exception)
        assert result.verdict == "escalate"


# ---------------------------------------------------------------------------
# Tests: _decide_action
# ---------------------------------------------------------------------------


class TestDecideAction:
    """Tests for _decide_action with configurable confidence thresholds."""

    # -- Approve with thresholds --

    def test_approve_above_threshold_goes_to_review(self):
        """confidence >= 0.8 + approve → review (Owner final check)"""
        review = ArchitectReviewResult(verdict="approve", confidence=0.95, summary="Good")
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "review"
        assert action.escalation_reason is None

    def test_approve_exactly_at_threshold(self):
        """confidence == 0.8 → review"""
        review = ArchitectReviewResult(verdict="approve", confidence=0.8, summary="Ok")
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "review"

    def test_approve_below_threshold_escalates(self):
        """confidence 0.5-0.8 + approve → escalated (low confidence)"""
        review = ArchitectReviewResult(verdict="approve", confidence=0.6, summary="Unsure")
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "low_confidence_approve"
        assert "0.60" in action.reason

    def test_approve_very_low_confidence_escalates(self):
        """confidence < 0.5 + approve → escalated"""
        review = ArchitectReviewResult(verdict="approve", confidence=0.3, summary="Guess")
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "low_confidence_approve"

    def test_approve_custom_threshold(self):
        """Custom threshold 0.9 — confidence 0.85 should escalate."""
        config = ReviewConfig(confidence_approve_threshold=0.9)
        review = ArchitectReviewResult(verdict="approve", confidence=0.85, summary="Ok")
        action = ArchitectReviewer._decide_action(_make_task(), review, config)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "low_confidence_approve"

    # -- Changes-requested with retries --

    def test_changes_requested_low_confidence_retry(self):
        """confidence < 0.5 + changes-requested + retries left → assigned"""
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.3, feedback="Fix auth",
        )
        action = ArchitectReviewer._decide_action(_make_task(retry_count=0), review)
        assert action.next_status == "assigned"
        assert "Fix auth" in action.reason

    def test_changes_requested_mid_confidence_retry(self):
        """confidence 0.5-0.8 + changes-requested + retries left → assigned"""
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.7, feedback="Missing validation",
        )
        action = ArchitectReviewer._decide_action(_make_task(retry_count=1), review)
        assert action.next_status == "assigned"

    def test_changes_requested_high_confidence_retry(self):
        """confidence >= 0.8 + changes-requested + retries left → assigned"""
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.9, feedback="Fix auth",
        )
        action = ArchitectReviewer._decide_action(_make_task(retry_count=1), review)
        assert action.next_status == "assigned"

    def test_changes_requested_max_retries_escalates(self):
        """changes-requested + max retries exceeded → escalated"""
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.9, feedback="Still broken",
        )
        action = ArchitectReviewer._decide_action(
            _make_task(retry_count=3, max_retries=3), review,
        )
        assert action.next_status == "escalated"
        assert action.escalation_reason == "max_retries_exceeded"
        assert "max_retries_exceeded" in action.reason

    def test_changes_requested_mid_confidence_max_retries_escalates(self):
        """confidence 0.5-0.8 + changes-requested + max retries → escalated"""
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.6, feedback="Ongoing issue",
        )
        action = ArchitectReviewer._decide_action(
            _make_task(retry_count=3, max_retries=3), review,
        )
        assert action.next_status == "escalated"
        assert action.escalation_reason == "max_retries_exceeded"

    # -- Escalate verdict --

    def test_escalate_verdict(self):
        review = ArchitectReviewResult(verdict="escalate", confidence=0.85, feedback="Human needed")
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "reviewer_escalate"

    # -- Security overrides --

    def test_critical_security_always_escalates(self):
        """Critical security finding overrides even high-confidence approve."""
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.99,
            findings=[{"severity": "critical", "category": "security", "description": "SQL injection"}],
        )
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "critical_security_finding"

    def test_critical_non_security_does_not_escalate(self):
        """Critical findings in non-security categories don't force escalation."""
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.9,
            findings=[{"severity": "critical", "category": "performance", "description": "N+1"}],
        )
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "review"

    def test_auth_category_escalates(self):
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.9,
            findings=[{"severity": "critical", "category": "auth", "description": "Auth bypass"}],
        )
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "critical_security_finding"

    # -- Error handling --

    def test_error_escalates(self):
        review = ArchitectReviewResult(verdict="approve", confidence=0.0, error="API timeout")
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "api_failure"

    # -- Escalation reason stored correctly --

    def test_escalation_reason_format(self):
        """Escalation reason includes confidence and feedback details."""
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.5,
            feedback="Not sure about error handling",
            summary="Needs review",
        )
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "escalated"
        assert "0.50" in action.reason
        assert "low_confidence_approve" in action.reason
        assert action.escalation_reason == "low_confidence_approve"


# ---------------------------------------------------------------------------
# Tests: full review() flow with mode selection
# ---------------------------------------------------------------------------


class TestFullReviewFlow:
    @pytest.mark.asyncio
    async def test_simple_task_uses_self_review(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({}))
        config = ReviewConfig(simple_task_mode="self-review")

        task = _make_task(title="Add button", description="UI", complexity="simple")
        exec_result = _make_exec_result(stdout="SELF_REVIEW: PASS\nREVIEW_CONFIDENCE: 0.9\n")

        result, action = await reviewer.review(task, exec_result, config)

        assert result.mode == "self-review"
        assert result.verdict == "approve"
        assert action.next_status == "review"

    @pytest.mark.asyncio
    async def test_security_task_forces_api(self):
        """Security-sensitive task should use API mode even if simple."""
        reviewer = ArchitectReviewer(credential_getter=_async_cred({"ANTHROPIC_API_KEY": "key"}))
        config = ReviewConfig(simple_task_mode="self-review", security_sensitive_mode="api")

        mock_resp = _mock_anthropic_response(_approve_review())

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            task = _make_task(title="Fix authentication bypass", complexity="simple")
            result, _ = await reviewer.review(task, _make_exec_result(), config)

        assert result.mode == "api"
        assert result.provider == "anthropic"
