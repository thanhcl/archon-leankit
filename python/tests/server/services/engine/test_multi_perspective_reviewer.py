"""Tests for MultiPerspectiveReviewer — consensus logic, parallel execution,
perspective prompts, error handling, and integration with ArchitectReviewer."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.architect_reviewer import (
    ArchitectReviewer,
    ArchitectReviewResult,
    ReviewConfig,
)
from src.server.services.engine.multi_perspective_reviewer import (
    MultiPerspectiveReviewer,
    MultiPerspectiveResult,
    PerspectiveResult,
    PERSPECTIVE_NAMES,
    PERSPECTIVE_PROMPTS,
    compute_consensus,
    multi_perspective_review_to_dict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides) -> dict:
    base = {
        "id": "task-mp1",
        "title": "Add user registration endpoint",
        "description": "Implement POST /api/users with email/password",
        "status": "architect-review",
        "priority": "high",
        "assignee": "Agent-1",
        "source_app": "leankit",
        "acceptance_criteria": [
            {"text": "POST /api/users creates new user"},
            {"text": "Returns 201 with user object"},
        ],
        "retry_count": 0,
        "max_retries": 3,
    }
    base.update(overrides)
    return base


def _make_exec_result(**overrides) -> dict:
    base = {
        "exit_code": 0,
        "duration_seconds": 60.0,
        "result": "SUCCESS",
        "files_changed": 5,
        "tests_added": 3,
        "summary": "Implemented user registration",
        "stdout": "",
        "estimated_risk": "medium",
    }
    base.update(overrides)
    return base


def _perspective_review(
    verdict: str = "approve",
    confidence: float = 0.9,
    findings: list | None = None,
    feedback: str = "",
    summary: str = "Looks good",
) -> ArchitectReviewResult:
    return ArchitectReviewResult(
        verdict=verdict,
        confidence=confidence,
        findings=findings or [],
        feedback=feedback,
        summary=summary,
        mode="api",
    )


def _perspective_result(
    perspective: str,
    verdict: str = "approve",
    confidence: float = 0.9,
    findings: list | None = None,
    feedback: str = "",
) -> PerspectiveResult:
    return PerspectiveResult(
        perspective=perspective,
        review=_perspective_review(
            verdict=verdict,
            confidence=confidence,
            findings=findings,
            feedback=feedback,
        ),
    )


def _mock_provider_response(review_json: dict) -> MagicMock:
    """Create a mock provider that returns a parsed ArchitectReviewResult."""
    result = ArchitectReviewResult(
        verdict=review_json.get("verdict", "approve"),
        confidence=review_json.get("confidence", 0.9),
        findings=review_json.get("findings", []),
        feedback=review_json.get("feedback", ""),
        summary=review_json.get("summary", "OK"),
        mode="api",
    )
    return result


# ---------------------------------------------------------------------------
# Tests: PERSPECTIVE_PROMPTS
# ---------------------------------------------------------------------------


class TestPerspectivePrompts:
    def test_all_three_perspectives_defined(self):
        assert set(PERSPECTIVE_NAMES) == {"security", "performance", "contract"}

    def test_security_prompt_mentions_xss(self):
        assert "XSS" in PERSPECTIVE_PROMPTS["security"]

    def test_performance_prompt_mentions_n_plus_1(self):
        assert "N+1" in PERSPECTIVE_PROMPTS["performance"]

    def test_contract_prompt_mentions_field_mismatch(self):
        assert "Field name mismatches" in PERSPECTIVE_PROMPTS["contract"]

    def test_all_prompts_request_json_response(self):
        for name, prompt in PERSPECTIVE_PROMPTS.items():
            assert '"verdict"' in prompt, f"{name} prompt missing verdict field"
            assert '"confidence"' in prompt, f"{name} prompt missing confidence field"


# ---------------------------------------------------------------------------
# Tests: compute_consensus
# ---------------------------------------------------------------------------


class TestComputeConsensus:
    def test_all_approve(self):
        results = [
            _perspective_result("security", "approve", 0.95),
            _perspective_result("performance", "approve", 0.90),
            _perspective_result("contract", "approve", 0.88),
        ]
        consensus = compute_consensus(results)
        assert consensus.consensus_verdict == "approve"
        assert consensus.consensus_confidence > 0.8
        assert consensus.dissenting_perspectives == []
        assert "All perspectives approve" in consensus.consensus_summary

    def test_majority_changes_requested(self):
        results = [
            _perspective_result("security", "changes-requested", 0.8,
                                findings=[{"severity": "warning", "category": "security", "description": "Missing CSRF"}]),
            _perspective_result("performance", "changes-requested", 0.7,
                                findings=[{"severity": "warning", "category": "performance", "description": "N+1 query"}]),
            _perspective_result("contract", "approve", 0.9),
        ]
        consensus = compute_consensus(results)
        assert consensus.consensus_verdict == "changes-requested"
        assert "2/3" in consensus.consensus_summary
        assert "contract" in consensus.dissenting_perspectives

    def test_critical_security_finding_escalates(self):
        results = [
            _perspective_result("security", "approve", 0.95,
                                findings=[{"severity": "critical", "category": "security", "description": "SQL injection"}]),
            _perspective_result("performance", "approve", 0.9),
            _perspective_result("contract", "approve", 0.9),
        ]
        consensus = compute_consensus(results)
        assert consensus.consensus_verdict == "escalate"
        assert consensus.consensus_confidence == 0.95
        assert "Critical security finding" in consensus.consensus_summary

    def test_critical_non_security_does_not_auto_escalate(self):
        results = [
            _perspective_result("performance", "approve", 0.9,
                                findings=[{"severity": "critical", "category": "performance", "description": "Memory leak"}]),
            _perspective_result("security", "approve", 0.95),
            _perspective_result("contract", "approve", 0.88),
        ]
        consensus = compute_consensus(results)
        assert consensus.consensus_verdict == "approve"

    def test_one_dissenter_reduces_confidence(self):
        results = [
            _perspective_result("security", "approve", 0.95),
            _perspective_result("performance", "changes-requested", 0.7,
                                feedback="N+1 detected"),
            _perspective_result("contract", "approve", 0.9),
        ]
        consensus = compute_consensus(results)
        assert consensus.consensus_verdict == "approve"
        # Confidence should be reduced by the 0.85 dissent factor
        assert consensus.consensus_confidence < 0.9
        assert "performance" in consensus.dissenting_perspectives

    def test_all_escalate(self):
        results = [
            _perspective_result("security", "escalate", 0.3),
            _perspective_result("performance", "escalate", 0.4),
            _perspective_result("contract", "escalate", 0.5),
        ]
        consensus = compute_consensus(results)
        assert consensus.consensus_verdict == "changes-requested"
        assert "3/3" in consensus.consensus_summary

    def test_empty_results(self):
        consensus = compute_consensus([])
        assert consensus.consensus_verdict == "escalate"
        assert consensus.consensus_confidence == 0.0

    def test_findings_aggregated_across_perspectives(self):
        results = [
            _perspective_result("security", "approve", 0.9,
                                findings=[{"severity": "warning", "category": "security", "description": "Missing auth"}]),
            _perspective_result("performance", "approve", 0.9,
                                findings=[{"severity": "suggestion", "category": "performance", "description": "Add cache"}]),
            _perspective_result("contract", "approve", 0.9),
        ]
        consensus = compute_consensus(results)
        assert len(consensus.aggregated_findings) == 2
        # Findings should have perspective tag
        perspectives_in_findings = {f.get("perspective") for f in consensus.aggregated_findings}
        assert "security" in perspectives_in_findings
        assert "performance" in perspectives_in_findings

    def test_severity_penalty_reduces_confidence(self):
        # No findings → higher confidence
        clean = compute_consensus([
            _perspective_result("security", "approve", 0.9),
            _perspective_result("performance", "approve", 0.9),
            _perspective_result("contract", "approve", 0.9),
        ])

        # Warnings → lower confidence
        with_warnings = compute_consensus([
            _perspective_result("security", "approve", 0.9,
                                findings=[
                                    {"severity": "warning", "category": "security", "description": "W1"},
                                    {"severity": "warning", "category": "security", "description": "W2"},
                                ]),
            _perspective_result("performance", "approve", 0.9),
            _perspective_result("contract", "approve", 0.9),
        ])

        assert with_warnings.consensus_confidence < clean.consensus_confidence

    def test_security_weight_higher(self):
        """Security perspective has higher weight (1.5x), so a low-confidence
        security review should pull the weighted average down more."""
        # Security low confidence
        low_sec = compute_consensus([
            _perspective_result("security", "approve", 0.5),
            _perspective_result("performance", "approve", 0.95),
            _perspective_result("contract", "approve", 0.95),
        ])

        # Performance low confidence (weight 1.0)
        low_perf = compute_consensus([
            _perspective_result("security", "approve", 0.95),
            _perspective_result("performance", "approve", 0.5),
            _perspective_result("contract", "approve", 0.95),
        ])

        assert low_sec.consensus_confidence < low_perf.consensus_confidence


# ---------------------------------------------------------------------------
# Tests: MultiPerspectiveReviewer
# ---------------------------------------------------------------------------


class TestMultiPerspectiveReviewer:
    @pytest.mark.asyncio
    async def test_all_perspectives_run_in_parallel(self):
        """All 3 perspectives should be called via the provider."""
        call_count = 0
        perspective_names_called = []

        async def mock_call_api(system_prompt, user_message, config):
            nonlocal call_count
            call_count += 1
            # Detect which perspective from the prompt
            if "Security Critic" in system_prompt:
                perspective_names_called.append("security")
            elif "Performance Analyst" in system_prompt:
                perspective_names_called.append("performance")
            elif "API Contract Checker" in system_prompt:
                perspective_names_called.append("contract")
            return ArchitectReviewResult(
                verdict="approve", confidence=0.9,
                findings=[], feedback="", summary="OK", mode="api",
            )

        mock_provider = AsyncMock()
        mock_provider.call_api = mock_call_api

        async def provider_factory(config):
            return mock_provider

        reviewer = MultiPerspectiveReviewer(provider_factory=provider_factory)
        consensus, unified, action = await reviewer.review(
            _make_task(), _make_exec_result(), ReviewConfig(review_mode="multi-perspective"),
        )

        assert call_count == 3
        assert set(perspective_names_called) == {"security", "performance", "contract"}
        assert unified.verdict == "approve"
        assert unified.mode == "multi-perspective"

    @pytest.mark.asyncio
    async def test_one_perspective_fails_gracefully(self):
        """If one perspective throws, it should be treated as escalate with error."""
        call_idx = 0

        async def mock_call_api(system_prompt, user_message, config):
            nonlocal call_idx
            call_idx += 1
            if "Performance Analyst" in system_prompt:
                raise RuntimeError("LLM timeout")
            return ArchitectReviewResult(
                verdict="approve", confidence=0.9,
                findings=[], feedback="", summary="OK", mode="api",
            )

        mock_provider = AsyncMock()
        mock_provider.call_api = mock_call_api

        async def provider_factory(config):
            return mock_provider

        reviewer = MultiPerspectiveReviewer(provider_factory=provider_factory)
        consensus, unified, action = await reviewer.review(
            _make_task(), _make_exec_result(), ReviewConfig(review_mode="multi-perspective"),
        )

        # Should still produce a result, with the failed perspective counted as escalate
        assert len(consensus.perspective_results) == 3
        perf_result = next(pr for pr in consensus.perspective_results if pr.perspective == "performance")
        assert perf_result.review.verdict == "escalate"
        assert perf_result.review.error is not None

    @pytest.mark.asyncio
    async def test_consensus_passed_to_unified_result(self):
        """Unified result should reflect consensus verdict and confidence."""
        async def mock_call_api(system_prompt, user_message, config):
            if "Security Critic" in system_prompt:
                return ArchitectReviewResult(
                    verdict="changes-requested", confidence=0.8,
                    findings=[{"severity": "warning", "category": "security", "description": "Missing CSRF"}],
                    feedback="Add CSRF token", summary="Security issue", mode="api",
                )
            return ArchitectReviewResult(
                verdict="approve", confidence=0.9, findings=[], feedback="", summary="OK", mode="api",
            )

        class MockProvider:
            async def call_api(self, system_prompt, user_message, config):
                return await mock_call_api(system_prompt, user_message, config)

        provider = MockProvider()

        reviewer = MultiPerspectiveReviewer(
            provider_factory=lambda cfg: provider,
        )
        consensus, unified, action = await reviewer.review(
            _make_task(), _make_exec_result(), ReviewConfig(review_mode="multi-perspective"),
        )

        # 1 dissenter (security), 2 approve → approve with reduced confidence
        assert unified.verdict == "approve"
        assert "[SECURITY]" in unified.feedback

    @pytest.mark.asyncio
    async def test_no_provider_factory_raises(self):
        """Missing provider_factory should raise ValueError."""
        reviewer = MultiPerspectiveReviewer(provider_factory=None)
        with pytest.raises(ValueError, match="No provider_factory"):
            await reviewer._run_perspective(
                "security", _make_task(), _make_exec_result(),
                ReviewConfig(review_mode="multi-perspective"),
            )

    @pytest.mark.asyncio
    async def test_unified_feedback_combines_perspectives(self):
        """Unified feedback should combine feedback from all perspectives."""
        results = [
            _perspective_result("security", "approve", 0.9, feedback="No issues found"),
            _perspective_result("performance", "changes-requested", 0.7, feedback="N+1 in user list"),
            _perspective_result("contract", "approve", 0.88, feedback=""),
        ]

        feedback = MultiPerspectiveReviewer._build_unified_feedback(results)
        assert "[SECURITY]" in feedback
        assert "[PERFORMANCE]" in feedback
        assert "N+1" in feedback
        assert "[CONTRACT]" not in feedback  # empty feedback excluded


# ---------------------------------------------------------------------------
# Tests: Integration with ArchitectReviewer
# ---------------------------------------------------------------------------


class TestArchitectReviewerMultiPerspective:
    @pytest.mark.asyncio
    async def test_multi_perspective_mode_via_config(self):
        """ArchitectReviewer with review_mode='multi-perspective' should use MP reviewer."""
        async def mock_call_api(system_prompt, user_message, config):
            return ArchitectReviewResult(
                verdict="approve", confidence=0.92,
                findings=[], feedback="", summary="All good", mode="api",
            )

        mock_provider = MagicMock()
        mock_provider.call_api = mock_call_api

        reviewer = ArchitectReviewer(
            credential_getter=AsyncMock(return_value="fake-key"),
        )

        config = ReviewConfig(review_mode="multi-perspective", provider="anthropic")

        with patch.object(reviewer, "_build_provider", return_value=mock_provider):
            result, action = await reviewer.review(
                _make_task(title="Add button", description="UI"),
                _make_exec_result(),
                config,
            )

        assert result.mode == "multi-perspective"
        assert result.verdict == "approve"
        assert action.next_status == "review"

    @pytest.mark.asyncio
    async def test_multi_perspective_fallback_to_self_review(self):
        """If MultiPerspectiveReviewer itself throws, fallback to self-review."""
        reviewer = ArchitectReviewer(
            credential_getter=AsyncMock(return_value="fake-key"),
        )

        config = ReviewConfig(
            review_mode="multi-perspective",
            api_fallback_to_self_review=True,
        )

        exec_result = _make_exec_result(
            stdout="SELF_REVIEW: PASS\nREVIEW_CONFIDENCE: 0.85\n",
        )

        # Patch MultiPerspectiveReviewer.review to raise
        with patch(
            "src.server.services.engine.multi_perspective_reviewer.MultiPerspectiveReviewer.review",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection refused"),
        ):
            result, action = await reviewer.review(
                _make_task(title="Add button", description="UI"),
                exec_result,
                config,
            )

        assert result.mode == "self-review"
        assert "Multi-perspective failed" in result.error

    @pytest.mark.asyncio
    async def test_all_perspectives_fail_gives_escalate_consensus(self):
        """If all perspectives fail (no API key), consensus is changes-requested (3 escalates)."""
        reviewer = ArchitectReviewer(
            credential_getter=AsyncMock(return_value=None),
        )

        config = ReviewConfig(
            review_mode="multi-perspective",
            api_fallback_to_self_review=True,
        )

        result, action = await reviewer.review(
            _make_task(title="Add button", description="UI"),
            _make_exec_result(),
            config,
        )

        # All 3 perspectives fail → each gets escalate → consensus = changes-requested
        assert result.mode == "multi-perspective"
        assert result.verdict == "changes-requested"

    @pytest.mark.asyncio
    async def test_multi_perspective_no_fallback_escalates(self):
        """If MultiPerspectiveReviewer itself throws and no fallback, escalate."""
        reviewer = ArchitectReviewer(
            credential_getter=AsyncMock(return_value="fake-key"),
        )

        config = ReviewConfig(
            review_mode="multi-perspective",
            api_fallback_to_self_review=False,
        )

        with patch(
            "src.server.services.engine.multi_perspective_reviewer.MultiPerspectiveReviewer.review",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection refused"),
        ):
            result, action = await reviewer.review(
                _make_task(title="Add button", description="UI"),
                _make_exec_result(),
                config,
            )

        assert result.verdict == "escalate"
        assert action.next_status == "escalated"

    def test_get_mode_multi_perspective_preserved(self):
        """Multi-perspective mode should not be overridden by security check."""
        config = ReviewConfig(review_mode="multi-perspective", security_override_to_api=True)
        task = _make_task(title="Fix auth bypass")
        exec_result = _make_exec_result(estimated_risk="high")
        assert ArchitectReviewer._get_mode(task, config, exec_result) == "multi-perspective"

    def test_get_mode_returns_multi_perspective(self):
        """Normal task with multi-perspective mode."""
        config = ReviewConfig(review_mode="multi-perspective")
        task = _make_task(title="Add pagination")
        assert ArchitectReviewer._get_mode(task, config, _make_exec_result()) == "multi-perspective"


# ---------------------------------------------------------------------------
# Tests: multi_perspective_review_to_dict
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_serialization_roundtrip(self):
        consensus = MultiPerspectiveResult(
            consensus_verdict="approve",
            consensus_confidence=0.88,
            consensus_summary="All perspectives approve",
            dissenting_perspectives=[],
            aggregated_findings=[
                {"severity": "suggestion", "category": "performance", "description": "Add cache", "perspective": "performance"},
            ],
            perspective_results=[
                _perspective_result("security", "approve", 0.95),
                _perspective_result("performance", "approve", 0.85,
                                    findings=[{"severity": "suggestion", "category": "performance", "description": "Add cache"}]),
                _perspective_result("contract", "approve", 0.9),
            ],
        )

        data = multi_perspective_review_to_dict(consensus)
        assert data["consensus_verdict"] == "approve"
        assert len(data["perspectives"]) == 3
        assert data["perspectives"][0]["perspective"] == "security"
        assert data["perspectives"][1]["verdict"] == "approve"
        assert len(data["aggregated_findings"]) == 1

        # Should be JSON-serializable
        json_str = json.dumps(data)
        assert json.loads(json_str) == data

    def test_empty_result_serialization(self):
        consensus = MultiPerspectiveResult(
            consensus_verdict="escalate",
            consensus_confidence=0.0,
            consensus_summary="No results",
        )
        data = multi_perspective_review_to_dict(consensus)
        assert data["perspectives"] == []
        assert data["aggregated_findings"] == []
