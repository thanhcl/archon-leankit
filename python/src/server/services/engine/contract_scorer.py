"""
Contract-aware review scoring (B-P4-04).

Evaluates execution results against contract acceptance criteria to produce
structured per-criterion pass/fail scores. Used by review stages to generate
deterministic evaluation instead of purely LLM-subjective review.

Usage:
    scorer = ContractScorer()
    result = scorer.score_against_contract(task, execution_result)
    # result = {
    #     "criteria_scores": [
    #         {"criterion": "...", "passed": True, "score": 8.5, "evidence": "..."},
    #     ],
    #     "aggregate_score": 7.2,
    #     "pass_rate": 0.75,
    #     "passed": True,
    #     "threshold": 7.0,
    # }
"""

from __future__ import annotations

from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

DEFAULT_PASS_THRESHOLD = 7.0
DEFAULT_CRITERION_PASS_SCORE = 6.0


class ContractScorer:
    """Score execution results against contract acceptance criteria."""

    def __init__(
        self,
        pass_threshold: float = DEFAULT_PASS_THRESHOLD,
        criterion_pass_score: float = DEFAULT_CRITERION_PASS_SCORE,
    ):
        self.pass_threshold = pass_threshold
        self.criterion_pass_score = criterion_pass_score

    def score_against_contract(
        self,
        task: dict[str, Any],
        execution_result: dict[str, Any] | None = None,
        review_findings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Score the execution against the task's contract criteria.

        Uses review findings (from architect or code review) to determine
        per-criterion pass/fail. Falls back to acceptance_criteria from
        the task if no formal contract exists.

        Returns:
            Dict with criteria_scores, aggregate_score, pass_rate, passed.
        """
        criteria = self._extract_criteria(task)
        if not criteria:
            return {
                "criteria_scores": [],
                "aggregate_score": None,
                "pass_rate": None,
                "passed": None,
                "threshold": self.pass_threshold,
                "reason": "no_contract_criteria",
            }

        findings_by_criterion = self._index_findings(review_findings or [])

        criteria_scores: list[dict[str, Any]] = []
        for criterion in criteria:
            criterion_text = self._get_criterion_text(criterion)
            score_result = self._score_criterion(criterion_text, findings_by_criterion)
            criteria_scores.append(score_result)

        # Compute aggregate
        scored = [c for c in criteria_scores if c.get("score") is not None]
        if scored:
            aggregate = sum(c["score"] for c in scored) / len(scored)
            pass_count = sum(1 for c in scored if c["passed"])
            pass_rate = pass_count / len(scored)
        else:
            aggregate = None
            pass_rate = None

        passed = aggregate is not None and aggregate >= self.pass_threshold

        return {
            "criteria_scores": criteria_scores,
            "aggregate_score": round(aggregate, 2) if aggregate is not None else None,
            "pass_rate": round(pass_rate, 3) if pass_rate is not None else None,
            "passed": passed,
            "threshold": self.pass_threshold,
        }

    def _extract_criteria(self, task: dict[str, Any]) -> list[dict[str, Any] | str]:
        """Extract acceptance criteria from task contract or task fields."""
        # 1. Formal contract (current_contract)
        contract = task.get("current_contract")
        if isinstance(contract, dict):
            criteria = contract.get("acceptance_criteria")
            if isinstance(criteria, list) and criteria:
                return criteria

        # 2. Task-level acceptance criteria
        criteria = task.get("acceptance_criteria")
        if isinstance(criteria, list) and criteria:
            return criteria

        return []

    @staticmethod
    def _get_criterion_text(criterion: dict[str, Any] | str) -> str:
        """Extract text from a criterion (may be string or dict)."""
        if isinstance(criterion, str):
            return criterion
        if isinstance(criterion, dict):
            return (
                criterion.get("description")
                or criterion.get("text")
                or criterion.get("criterion")
                or criterion.get("title")
                or str(criterion)
            )
        return str(criterion)

    def _score_criterion(
        self,
        criterion_text: str,
        findings_by_criterion: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Score a single criterion based on matching findings."""
        criterion_lower = criterion_text.lower()

        # Find matching findings
        matched_findings: list[dict[str, Any]] = []
        for key, findings in findings_by_criterion.items():
            if key in criterion_lower or criterion_lower in key:
                matched_findings.extend(findings)

        if not matched_findings:
            # No findings matched — assume pass (absence of failure signals)
            return {
                "criterion": criterion_text,
                "passed": True,
                "score": 8.0,
                "evidence": "No findings matched this criterion",
                "matched_findings": 0,
            }

        # Aggregate findings for this criterion
        severity_scores = {"critical": 1.0, "high": 3.0, "medium": 6.0, "low": 8.0}
        scores: list[float] = []
        evidence_parts: list[str] = []

        for finding in matched_findings:
            severity = str(finding.get("severity", "medium")).lower()
            score = finding.get("score")
            if score is not None:
                scores.append(float(score))
            else:
                scores.append(severity_scores.get(severity, 6.0))
            desc = finding.get("description", "")
            if desc:
                evidence_parts.append(desc[:100])

        avg_score = sum(scores) / len(scores) if scores else 6.0
        passed = avg_score >= self.criterion_pass_score

        return {
            "criterion": criterion_text,
            "passed": passed,
            "score": round(avg_score, 1),
            "evidence": "; ".join(evidence_parts[:3]) if evidence_parts else "",
            "matched_findings": len(matched_findings),
        }

    @staticmethod
    def _index_findings(
        findings: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Index findings by their type/category for criterion matching."""
        index: dict[str, list[dict[str, Any]]] = {}
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            key = (
                finding.get("type")
                or finding.get("category")
                or finding.get("criterion")
                or "general"
            ).lower()
            if key not in index:
                index[key] = []
            index[key].append(finding)
        return index
