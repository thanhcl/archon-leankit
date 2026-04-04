"""Tests for contract-aware review scoring (B-P4-04)."""

import pytest

from src.server.services.engine.contract_scorer import ContractScorer


def _make_task(**overrides):
    base = {
        "id": "t1",
        "acceptance_criteria": [],
        "current_contract": None,
    }
    base.update(overrides)
    return base


class TestContractScorer:

    def test_no_criteria_returns_none(self):
        scorer = ContractScorer()
        result = scorer.score_against_contract(_make_task())
        assert result["passed"] is None
        assert result["reason"] == "no_contract_criteria"

    def test_all_criteria_pass(self):
        scorer = ContractScorer()
        task = _make_task(acceptance_criteria=[
            {"description": "Tests pass"},
            {"description": "Linting clean"},
        ])
        # No findings = pass (absence of failure)
        result = scorer.score_against_contract(task, review_findings=[])
        assert result["passed"] is True
        assert result["pass_rate"] == 1.0
        assert len(result["criteria_scores"]) == 2

    def test_critical_finding_fails_criterion(self):
        scorer = ContractScorer()
        task = _make_task(acceptance_criteria=[
            {"description": "Token expiry handling"},
        ])
        findings = [
            {"type": "token expiry", "severity": "critical", "description": "Wrong timezone used"},
        ]
        result = scorer.score_against_contract(task, review_findings=findings)

        assert result["criteria_scores"][0]["passed"] is False
        assert result["criteria_scores"][0]["score"] == 1.0

    def test_mixed_criteria_partial_pass(self):
        scorer = ContractScorer()
        task = _make_task(acceptance_criteria=[
            {"description": "Auth logic correct"},
            {"description": "Tests cover edge cases"},
            {"description": "Documentation updated"},
        ])
        findings = [
            {"type": "auth", "severity": "critical", "description": "Missing validation"},
        ]
        result = scorer.score_against_contract(task, review_findings=findings)

        # auth criterion fails, others pass
        auth_score = next(c for c in result["criteria_scores"] if "auth" in c["criterion"].lower())
        docs_score = next(c for c in result["criteria_scores"] if "documentation" in c["criterion"].lower())

        assert auth_score["passed"] is False
        assert docs_score["passed"] is True
        assert result["pass_rate"] < 1.0

    def test_contract_criteria_preferred_over_task(self):
        scorer = ContractScorer()
        task = _make_task(
            acceptance_criteria=[{"description": "Task level"}],
            current_contract={
                "acceptance_criteria": [{"description": "Contract level"}],
            },
        )
        result = scorer.score_against_contract(task)
        assert result["criteria_scores"][0]["criterion"] == "Contract level"

    def test_string_criteria(self):
        scorer = ContractScorer()
        task = _make_task(acceptance_criteria=["Tests pass", "No regressions"])
        result = scorer.score_against_contract(task)
        assert len(result["criteria_scores"]) == 2
        assert result["criteria_scores"][0]["criterion"] == "Tests pass"

    def test_aggregate_score_below_threshold_fails(self):
        scorer = ContractScorer(pass_threshold=7.0)
        task = _make_task(acceptance_criteria=[
            {"description": "Feature A"},
            {"description": "Feature B"},
        ])
        findings = [
            {"type": "feature a", "severity": "high", "description": "Incomplete"},
            {"type": "feature b", "severity": "high", "description": "Buggy"},
        ]
        result = scorer.score_against_contract(task, review_findings=findings)
        assert result["aggregate_score"] == 3.0  # both high severity
        assert result["passed"] is False

    def test_custom_thresholds(self):
        scorer = ContractScorer(pass_threshold=5.0, criterion_pass_score=4.0)
        task = _make_task(acceptance_criteria=[{"description": "Basic check"}])
        findings = [
            {"type": "basic check", "severity": "medium", "description": "Minor issue"},
        ]
        result = scorer.score_against_contract(task, review_findings=findings)
        # medium severity = score 6.0 > criterion_pass_score 4.0
        assert result["criteria_scores"][0]["passed"] is True

    def test_finding_with_explicit_score(self):
        scorer = ContractScorer()
        task = _make_task(acceptance_criteria=[{"description": "Performance"}])
        findings = [
            {"type": "performance", "score": 9.0, "description": "Excellent"},
        ]
        result = scorer.score_against_contract(task, review_findings=findings)
        assert result["criteria_scores"][0]["score"] == 9.0
        assert result["criteria_scores"][0]["passed"] is True

    def test_evidence_from_findings(self):
        scorer = ContractScorer()
        task = _make_task(acceptance_criteria=[{"description": "Security"}])
        findings = [
            {"type": "security", "severity": "low", "description": "Minor: unused import"},
        ]
        result = scorer.score_against_contract(task, review_findings=findings)
        assert "unused import" in result["criteria_scores"][0]["evidence"]
