"""Tests for hybrid ArchitectReviewer — mode selection via CC assessment,
self-review, multi-provider API, decision logic, security escalation."""

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
        "task_assessment": "simple",
        "estimated_risk": "low",
        "estimated_files": 3,
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
    async def getter(key: str):
        return mapping.get(key)
    return getter


# ---------------------------------------------------------------------------
# Tests: _get_mode — CC assessment-based
# ---------------------------------------------------------------------------


class TestGetMode:
    def test_global_self_review(self):
        """Default global mode is self-review."""
        config = ReviewConfig(review_mode="self-review")
        task = _make_task(title="Add button", description="UI work")
        exec_result = _make_exec_result(estimated_risk="low")
        assert ArchitectReviewer._get_mode(task, config, exec_result) == "self-review"

    def test_global_api(self):
        """Global mode set to API."""
        config = ReviewConfig(review_mode="api")
        task = _make_task(title="Add button", description="UI")
        assert ArchitectReviewer._get_mode(task, config, _make_exec_result()) == "api"

    def test_high_risk_security_overrides_to_api(self):
        """CC assessed high risk + security-sensitive task → API override."""
        config = ReviewConfig(review_mode="self-review", security_override_to_api=True)
        task = _make_task(title="Fix authentication bypass")
        exec_result = _make_exec_result(estimated_risk="high")
        assert ArchitectReviewer._get_mode(task, config, exec_result) == "api"

    def test_high_risk_non_security_stays_global(self):
        """CC assessed high risk but NOT security-sensitive → use global mode."""
        config = ReviewConfig(review_mode="self-review", security_override_to_api=True)
        task = _make_task(title="Refactor database layer", description="Restructure queries")
        exec_result = _make_exec_result(estimated_risk="high")
        assert ArchitectReviewer._get_mode(task, config, exec_result) == "self-review"

    def test_low_risk_security_stays_global(self):
        """Security-sensitive task but CC assessed low risk → use global mode."""
        config = ReviewConfig(review_mode="self-review", security_override_to_api=True)
        task = _make_task(title="Fix auth error message")
        exec_result = _make_exec_result(estimated_risk="low")
        assert ArchitectReviewer._get_mode(task, config, exec_result) == "self-review"

    def test_override_disabled(self):
        """security_override_to_api=False → always use global mode."""
        config = ReviewConfig(review_mode="self-review", security_override_to_api=False)
        task = _make_task(title="Fix authentication bypass")
        exec_result = _make_exec_result(estimated_risk="high")
        assert ArchitectReviewer._get_mode(task, config, exec_result) == "self-review"

    def test_no_execution_result(self):
        """No execution_result (pre-execution) → use global mode."""
        config = ReviewConfig(review_mode="self-review", security_override_to_api=True)
        task = _make_task(title="Fix auth bypass")
        assert ArchitectReviewer._get_mode(task, config, None) == "self-review"

    def test_medium_risk_security_stays_global(self):
        """Only high risk triggers override, not medium."""
        config = ReviewConfig(review_mode="self-review", security_override_to_api=True)
        task = _make_task(title="Update password hashing")
        exec_result = _make_exec_result(estimated_risk="medium")
        assert ArchitectReviewer._get_mode(task, config, exec_result) == "self-review"


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
            "stdout": "SELF_REVIEW: PASS\nREVIEW_CONFIDENCE: 0.92\n",
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
        assert len(result.findings) == 1

    def test_no_self_review_block(self):
        result = ArchitectReviewer._parse_self_review({"stdout": "Just normal output"})
        assert result.verdict == "changes-requested"
        assert result.confidence == 0.5

    def test_empty_stdout(self):
        result = ArchitectReviewer._parse_self_review({"stdout": ""})
        assert result.verdict == "changes-requested"


# ---------------------------------------------------------------------------
# Tests: Multi-provider API (mock)
# ---------------------------------------------------------------------------


class TestAnthropicProvider:
    @pytest.mark.asyncio
    async def test_approve_flow(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({"ANTHROPIC_API_KEY": "key"}))
        config = ReviewConfig(review_mode="api", provider="anthropic")
        mock_resp = _mock_anthropic_response(_approve_review())

        with patch("httpx.AsyncClient") as MC:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=mock_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=False)
            MC.return_value = mc

            task = _make_task(title="Add button", description="UI work")
            result, action = await reviewer.review(task, _make_exec_result(), config)

        assert result.verdict == "approve"
        assert result.provider == "anthropic"
        assert action.next_status == "review"


class TestOpenAIProvider:
    @pytest.mark.asyncio
    async def test_approve_flow(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({"OPENAI_API_KEY": "key"}))
        config = ReviewConfig(review_mode="api", provider="openai")
        mock_resp = _mock_openai_response(_approve_review())

        with patch("httpx.AsyncClient") as MC:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=mock_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=False)
            MC.return_value = mc

            task = _make_task(title="Add button", description="UI")
            result, action = await reviewer.review(task, _make_exec_result(), config)

        assert result.provider == "openai"
        assert action.next_status == "review"


class TestGoogleProvider:
    @pytest.mark.asyncio
    async def test_approve_flow(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({"GOOGLE_API_KEY": "key"}))
        config = ReviewConfig(review_mode="api", provider="google")
        mock_resp = _mock_openai_response(_approve_review())

        with patch("httpx.AsyncClient") as MC:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=mock_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=False)
            MC.return_value = mc

            task = _make_task(title="Add button", description="UI")
            result, _ = await reviewer.review(task, _make_exec_result(), config)

        assert result.provider == "google"


# ---------------------------------------------------------------------------
# Tests: API fallback
# ---------------------------------------------------------------------------


class TestAPIFallback:
    @pytest.mark.asyncio
    async def test_fallback_on_no_api_key(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({}))
        config = ReviewConfig(review_mode="api", api_fallback_to_self_review=True)

        exec_result = _make_exec_result(stdout="SELF_REVIEW: PASS\nREVIEW_CONFIDENCE: 0.85\n")
        task = _make_task(title="Add button", description="UI")
        result, _ = await reviewer.review(task, exec_result, config)

        assert result.mode == "self-review"
        assert "API failed" in result.error

    @pytest.mark.asyncio
    async def test_no_fallback_escalates(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({}))
        config = ReviewConfig(review_mode="api", api_fallback_to_self_review=False)

        task = _make_task(title="Add button", description="UI")
        result, action = await reviewer.review(task, _make_exec_result(), config)

        assert result.verdict == "escalate"
        assert action.next_status == "escalated"


# ---------------------------------------------------------------------------
# Tests: _decide_action with confidence thresholds
# ---------------------------------------------------------------------------


class TestDecideAction:
    def test_approve_above_threshold(self):
        review = ArchitectReviewResult(verdict="approve", confidence=0.95, summary="Good")
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "review"
        assert action.escalation_reason is None

    def test_approve_exactly_at_threshold(self):
        review = ArchitectReviewResult(verdict="approve", confidence=0.8, summary="Ok")
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "review"

    def test_approve_below_threshold_escalates(self):
        review = ArchitectReviewResult(verdict="approve", confidence=0.6, summary="Unsure")
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "low_confidence_approve"

    def test_approve_custom_threshold(self):
        config = ReviewConfig(confidence_approve_threshold=0.9)
        review = ArchitectReviewResult(verdict="approve", confidence=0.85, summary="Ok")
        action = ArchitectReviewer._decide_action(_make_task(), review, config)
        assert action.next_status == "escalated"

    def test_changes_requested_retry(self):
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.7, feedback="Fix auth",
        )
        action = ArchitectReviewer._decide_action(_make_task(retry_count=1), review)
        assert action.next_status == "assigned"

    def test_changes_requested_max_retries(self):
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.9, feedback="Still broken",
        )
        action = ArchitectReviewer._decide_action(_make_task(retry_count=3, max_retries=3), review)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "max_retries_exceeded"

    def test_escalate_verdict(self):
        review = ArchitectReviewResult(verdict="escalate", confidence=0.85, feedback="Human needed")
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "reviewer_escalate"

    def test_critical_security_always_escalates(self):
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.99,
            findings=[{"severity": "critical", "category": "security", "description": "SQL injection"}],
        )
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "critical_security_finding"

    def test_critical_non_security_does_not_escalate(self):
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.9,
            findings=[{"severity": "critical", "category": "performance", "description": "N+1"}],
        )
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "review"

    def test_error_escalates(self):
        review = ArchitectReviewResult(verdict="approve", confidence=0.0, error="API timeout")
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert action.next_status == "escalated"
        assert action.escalation_reason == "api_failure"

    def test_escalation_reason_format(self):
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.5,
            feedback="Not sure", summary="Needs review",
        )
        action = ArchitectReviewer._decide_action(_make_task(), review)
        assert "0.50" in action.reason
        assert action.escalation_reason == "low_confidence_approve"


# ---------------------------------------------------------------------------
# Tests: full review() with CC assessment override
# ---------------------------------------------------------------------------


class TestFullReviewFlow:
    @pytest.mark.asyncio
    async def test_self_review_mode(self):
        reviewer = ArchitectReviewer(credential_getter=_async_cred({}))
        config = ReviewConfig(review_mode="self-review")

        task = _make_task(title="Add button", description="UI")
        exec_result = _make_exec_result(
            stdout="SELF_REVIEW: PASS\nREVIEW_CONFIDENCE: 0.9\n",
            estimated_risk="low",
        )

        result, action = await reviewer.review(task, exec_result, config)

        assert result.mode == "self-review"
        assert result.verdict == "approve"
        assert action.next_status == "review"

    @pytest.mark.asyncio
    async def test_cc_high_risk_security_forces_api(self):
        """CC assessed high-risk on security task → override to API."""
        reviewer = ArchitectReviewer(credential_getter=_async_cred({"ANTHROPIC_API_KEY": "key"}))
        config = ReviewConfig(review_mode="self-review", security_override_to_api=True)

        mock_resp = _mock_anthropic_response(_approve_review())

        with patch("httpx.AsyncClient") as MC:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=mock_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=False)
            MC.return_value = mc

            task = _make_task(title="Fix authentication bypass")
            exec_result = _make_exec_result(estimated_risk="high")
            result, _ = await reviewer.review(task, exec_result, config)

        assert result.mode == "api"
        assert result.provider == "anthropic"

    @pytest.mark.asyncio
    async def test_cc_high_risk_non_security_stays_self_review(self):
        """CC assessed high-risk but non-security → stays self-review."""
        reviewer = ArchitectReviewer(credential_getter=_async_cred({}))
        config = ReviewConfig(review_mode="self-review", security_override_to_api=True)

        task = _make_task(title="Refactor database", description="Restructure models")
        exec_result = _make_exec_result(
            stdout="SELF_REVIEW: PASS\nREVIEW_CONFIDENCE: 0.85\n",
            estimated_risk="high",
        )

        result, _ = await reviewer.review(task, exec_result, config)

        assert result.mode == "self-review"
