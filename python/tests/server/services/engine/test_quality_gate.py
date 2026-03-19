"""Tests for Quality Gate scoring and review history."""

import pytest

from src.server.services.engine.architect_reviewer import (
    ArchitectReviewResult,
    QualityGateScore,
    ReviewAction,
    ReviewHistoryEntry,
    build_review_history_entry,
    calculate_quality_gate_score,
)


class TestQualityGateScore:
    def test_perfect_score_passes(self):
        """Approve verdict with high confidence, no findings, clean exit → pass."""
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.95, findings=[], mode="self-review",
        )
        execution_result = {
            "exit_code": 0, "timed_out": False,
            "result": "SUCCESS", "self_review": "PASS",
        }
        score = calculate_quality_gate_score(review, execution_result)

        assert score.tests_pass == 100.0
        assert score.self_review == 100.0
        assert score.confidence == 95.0
        assert score.code_review_clean == 100.0
        assert score.acceptance_criteria == 100.0
        assert score.compound_score >= 80
        assert score.gate_result == "pass"

    def test_failed_tests_drops_score(self):
        """Non-zero exit code drops tests_pass to 0 → score drops significantly."""
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.9, findings=[], mode="api",
        )
        execution_result = {"exit_code": 1, "timed_out": False, "result": "SUCCESS"}
        score = calculate_quality_gate_score(review, execution_result)

        assert score.tests_pass == 0.0
        # Without tests_pass (30%), max possible is 70 → retry or escalate
        assert score.compound_score < 80
        assert score.gate_result in ("retry", "escalate")

    def test_timed_out_drops_tests(self):
        """Timed out execution drops tests_pass to 0."""
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.9, findings=[], mode="self-review",
        )
        execution_result = {"exit_code": 0, "timed_out": True, "result": "SUCCESS"}
        score = calculate_quality_gate_score(review, execution_result)

        assert score.tests_pass == 0.0

    def test_critical_finding_drops_code_review(self):
        """Critical finding deducts 40 from code_review_clean."""
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.9,
            findings=[{"severity": "critical", "category": "security", "description": "XSS"}],
            mode="api",
        )
        execution_result = {"exit_code": 0, "timed_out": False, "result": "SUCCESS"}
        score = calculate_quality_gate_score(review, execution_result)

        assert score.code_review_clean == 60.0

    def test_multiple_findings_cumulative(self):
        """Multiple findings deduct cumulatively, floored at 0."""
        review = ArchitectReviewResult(
            verdict="approve", confidence=0.9,
            findings=[
                {"severity": "critical", "category": "security", "description": "XSS"},
                {"severity": "critical", "category": "auth", "description": "bypass"},
                {"severity": "warning", "category": "perf", "description": "N+1"},
            ],
            mode="api",
        )
        execution_result = {"exit_code": 0, "timed_out": False, "result": "SUCCESS"}
        score = calculate_quality_gate_score(review, execution_result)

        # 100 - 40 - 40 - 15 = 5
        assert score.code_review_clean == 5.0

    def test_changes_requested_lowers_acceptance(self):
        """changes-requested verdict → acceptance_criteria = 30."""
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.6, findings=[], mode="self-review",
        )
        execution_result = {"exit_code": 0, "timed_out": False, "result": "SUCCESS"}
        score = calculate_quality_gate_score(review, execution_result)

        assert score.acceptance_criteria == 30.0

    def test_escalate_verdict_zeros_acceptance(self):
        """escalate verdict → acceptance_criteria = 0."""
        review = ArchitectReviewResult(
            verdict="escalate", confidence=0.3, findings=[], mode="api",
        )
        execution_result = {"exit_code": 0, "timed_out": False, "result": "SUCCESS"}
        score = calculate_quality_gate_score(review, execution_result)

        assert score.acceptance_criteria == 0.0

    def test_retry_range(self):
        """Score in 60-79 range → retry."""
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.5,
            findings=[{"severity": "warning", "category": "perf", "description": "slow"}],
            mode="self-review",
        )
        execution_result = {"exit_code": 0, "timed_out": False, "self_review": "NEEDS_ATTENTION"}
        score = calculate_quality_gate_score(review, execution_result)

        # tests=100*0.3=30, self_review=40*0.2=8, confidence=50*0.2=10,
        # code_review=85*0.2=17, acceptance=30*0.1=3 → 68
        assert 60 <= score.compound_score < 80
        assert score.gate_result == "retry"

    def test_escalate_range(self):
        """Score below 60 → escalate."""
        review = ArchitectReviewResult(
            verdict="escalate", confidence=0.2,
            findings=[
                {"severity": "critical", "category": "security", "description": "bad"},
                {"severity": "critical", "category": "auth", "description": "bad2"},
            ],
            mode="api",
        )
        execution_result = {"exit_code": 1, "timed_out": False, "result": "FAILURE"}
        score = calculate_quality_gate_score(review, execution_result)

        assert score.compound_score < 60
        assert score.gate_result == "escalate"

    def test_needs_attention_self_review(self):
        """NEEDS_ATTENTION self-review → self_review = 40."""
        review = ArchitectReviewResult(
            verdict="changes-requested", confidence=0.5, findings=[], mode="self-review",
        )
        execution_result = {
            "exit_code": 0, "timed_out": False,
            "result": "SUCCESS", "self_review": "NEEDS_ATTENTION",
        }
        score = calculate_quality_gate_score(review, execution_result)

        assert score.self_review == 40.0

    def test_to_dict(self):
        """to_dict returns all fields."""
        score = QualityGateScore(
            tests_pass=100, self_review=100, confidence=95,
            code_review_clean=80, acceptance_criteria=100,
            compound_score=95, gate_result="pass",
        )
        d = score.to_dict()
        assert d["compound_score"] == 95
        assert d["gate_result"] == "pass"
        assert len(d) == 7


class TestReviewHistoryEntry:
    def _make_review(self, **overrides):
        defaults = {
            "verdict": "approve", "confidence": 0.9, "findings": [],
            "feedback": "Looks good", "summary": "Approved", "mode": "self-review",
        }
        defaults.update(overrides)
        return ArchitectReviewResult(**defaults)

    def _make_action(self, **overrides):
        defaults = {"next_status": "review", "reason": "Approved"}
        defaults.update(overrides)
        return ReviewAction(**defaults)

    def _make_quality_gate(self):
        return QualityGateScore(
            tests_pass=100, self_review=100, confidence=90,
            code_review_clean=100, acceptance_criteria=100,
            compound_score=98, gate_result="pass",
        )

    def test_first_entry_number_is_1(self):
        entry = build_review_history_entry(
            review=self._make_review(),
            action=self._make_action(),
            quality_gate=self._make_quality_gate(),
            task={"retry_count": 0},
            existing_history=[],
        )
        assert entry.review_number == 1
        assert entry.review_id  # UUID generated

    def test_increments_review_number(self):
        existing = [{"review_number": 1}, {"review_number": 2}]
        entry = build_review_history_entry(
            review=self._make_review(),
            action=self._make_action(),
            quality_gate=self._make_quality_gate(),
            task={"retry_count": 1},
            existing_history=existing,
        )
        assert entry.review_number == 3

    def test_captures_retry_count(self):
        entry = build_review_history_entry(
            review=self._make_review(),
            action=self._make_action(),
            quality_gate=self._make_quality_gate(),
            task={"retry_count": 2},
        )
        assert entry.retry_count == 2

    def test_captures_escalation_reason(self):
        entry = build_review_history_entry(
            review=self._make_review(verdict="escalate", confidence=0.0),
            action=self._make_action(
                next_status="escalated", reason="Critical",
                escalation_reason="critical_security_finding",
            ),
            quality_gate=self._make_quality_gate(),
            task={"retry_count": 0},
        )
        assert entry.escalation_reason == "critical_security_finding"

    def test_to_dict_excludes_none_optionals(self):
        entry = build_review_history_entry(
            review=self._make_review(),
            action=self._make_action(),
            quality_gate=self._make_quality_gate(),
            task={"retry_count": 0},
        )
        d = entry.to_dict()
        assert "error" not in d
        assert "escalation_reason" not in d
        assert "quality_gate" in d
        assert d["quality_gate"]["gate_result"] == "pass"

    def test_to_dict_includes_error_when_present(self):
        entry = build_review_history_entry(
            review=self._make_review(error="API timeout"),
            action=self._make_action(),
            quality_gate=self._make_quality_gate(),
            task={"retry_count": 0},
        )
        d = entry.to_dict()
        assert d["error"] == "API timeout"

    def test_preserves_findings(self):
        findings = [{"severity": "warning", "category": "perf", "description": "N+1 query"}]
        entry = build_review_history_entry(
            review=self._make_review(findings=findings),
            action=self._make_action(),
            quality_gate=self._make_quality_gate(),
            task={"retry_count": 0},
        )
        assert entry.findings == findings
        assert entry.to_dict()["findings"] == findings
