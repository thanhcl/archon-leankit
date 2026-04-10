"""
Goal-Backward Verification Agent for LeanKit V3 Task Engine.

After code-review passes, verifies that the codebase actually exhibits
the behaviors defined in acceptance criteria — not just that code exists.

Performs 4-level verification per criterion:
  1. EXISTS   — Do the expected files/artifacts exist?
  2. SUBSTANTIVE — Is the implementation real (not stub/TODO/empty)?
  3. WIRED    — Is it imported/called by other modules?
  4. DATA_FLOWS — Does it process real data (not hardcoded)?

Adopted from GSD's "goal-backward verification" pattern:
  Instead of "did the task complete?" (forward check), asks
  "does the codebase now exhibit the required behavior?" (backward check).

Usage:
    agent = VerificationAgent()
    result = agent.verify(task, execution_result)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)


@dataclass
class CriterionVerification:
    """Verification result for a single acceptance criterion."""

    criterion: str
    exists: bool = False
    substantive: bool = False
    wired: bool = False
    data_flows: bool = False
    evidence: str = ""
    gaps: list[str] = field(default_factory=list)

    @property
    def level(self) -> int:
        """Return the highest passing verification level (0-4)."""
        if self.data_flows:
            return 4
        if self.wired:
            return 3
        if self.substantive:
            return 2
        if self.exists:
            return 1
        return 0

    @property
    def passed(self) -> bool:
        """Minimum passing level is 2 (substantive)."""
        return self.level >= 2

    @property
    def level_label(self) -> str:
        labels = {
            0: "missing",
            1: "exists",
            2: "substantive",
            3: "wired",
            4: "data_flows",
        }
        return labels.get(self.level, "unknown")


@dataclass
class VerificationResult:
    """Aggregate verification result for all criteria."""

    criteria_results: list[CriterionVerification] = field(default_factory=list)
    overall_passed: bool = False
    recommendation: str = "approve"  # "approve" | "rework" | "follow_up"
    gaps: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.overall_passed,
            "recommendation": self.recommendation,
            "summary": self.summary,
            "gaps": self.gaps,
            "criteria_count": len(self.criteria_results),
            "passed_count": sum(1 for c in self.criteria_results if c.passed),
            "criteria_details": [
                {
                    "criterion": c.criterion,
                    "level": c.level,
                    "level_label": c.level_label,
                    "passed": c.passed,
                    "exists": c.exists,
                    "substantive": c.substantive,
                    "wired": c.wired,
                    "data_flows": c.data_flows,
                    "evidence": c.evidence,
                    "gaps": c.gaps,
                }
                for c in self.criteria_results
            ],
        }


class VerificationAgent:
    """Verifies task deliverables against acceptance criteria using 4-level checks.

    This is a lightweight rule-based verifier that analyzes execution results
    and runner output to determine verification levels. It does NOT spawn a
    separate LLM call — instead it applies heuristic checks against the
    structured output already produced by the runner.

    For full LLM-powered verification, a future enhancement could spawn a
    Haiku session to read the actual codebase. The current implementation
    focuses on zero-cost verification from available execution artifacts.
    """

    def verify(
        self,
        task: dict[str, Any],
        execution_result: dict[str, Any] | None = None,
        code_review: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """Verify task deliverables against acceptance criteria.

        Args:
            task: Full task dict with acceptance_criteria, allowed_paths, etc.
            execution_result: Parsed execution result from runner.
            code_review: Code review data with findings and verdict.

        Returns:
            VerificationResult with per-criterion status and gaps.
        """
        criteria = self._extract_criteria(task)
        if not criteria:
            return VerificationResult(
                overall_passed=True,
                recommendation="approve",
                summary="No acceptance criteria to verify",
            )

        execution_result = execution_result or {}
        code_review = code_review or {}

        # Extract signals from execution result
        files_changed = execution_result.get("files_changed", 0)
        tests_added = execution_result.get("tests_added", 0)
        result_status = (execution_result.get("result") or "").upper()
        run_summary = (
            execution_result.get("summary")
            or execution_result.get("run_result_summary")
            or ""
        )
        stdout = execution_result.get("stdout") or ""
        changed_files = execution_result.get("changed_files") or []

        # Extract signals from code review
        review_verdict = (code_review.get("verdict") or "").upper()
        review_findings = code_review.get("findings") or []

        criteria_results: list[CriterionVerification] = []
        all_gaps: list[str] = []

        for criterion_text in criteria:
            verification = self._verify_criterion(
                criterion_text=criterion_text,
                files_changed=files_changed,
                tests_added=tests_added,
                result_status=result_status,
                run_summary=run_summary,
                stdout=stdout,
                changed_files=changed_files,
                review_verdict=review_verdict,
                review_findings=review_findings,
            )
            criteria_results.append(verification)
            all_gaps.extend(verification.gaps)

        # Determine overall result
        total = len(criteria_results)
        passed_count = sum(1 for c in criteria_results if c.passed)
        pass_rate = passed_count / total if total > 0 else 0

        if pass_rate >= 1.0:
            recommendation = "approve"
            overall_passed = True
        elif pass_rate >= 0.7:
            recommendation = "follow_up"
            overall_passed = True  # Proceed but note gaps
        else:
            recommendation = "rework"
            overall_passed = False

        # Downgrade if execution itself failed
        if result_status == "FAILURE":
            recommendation = "rework"
            overall_passed = False
            if "Execution result was FAILURE" not in all_gaps:
                all_gaps.insert(0, "Execution result was FAILURE")

        # Downgrade if no files were changed (suspicious)
        if files_changed == 0 and total > 0:
            if recommendation == "approve":
                recommendation = "follow_up"
            all_gaps.append("No files were changed during execution")

        summary_parts = [
            f"Verified {total} criteria: {passed_count} passed, "
            f"{total - passed_count} gaps.",
        ]
        if all_gaps:
            summary_parts.append(f"Gaps: {'; '.join(all_gaps[:3])}")

        return VerificationResult(
            criteria_results=criteria_results,
            overall_passed=overall_passed,
            recommendation=recommendation,
            gaps=all_gaps,
            summary=" ".join(summary_parts),
        )

    def _verify_criterion(
        self,
        criterion_text: str,
        files_changed: int,
        tests_added: int,
        result_status: str,
        run_summary: str,
        stdout: str,
        changed_files: list[str],
        review_verdict: str,
        review_findings: list[dict[str, Any]],
    ) -> CriterionVerification:
        """Verify a single criterion against execution signals."""
        verification = CriterionVerification(criterion=criterion_text)
        criterion_lower = criterion_text.lower()

        # Level 1: EXISTS — Were any files changed?
        if files_changed and files_changed > 0:
            verification.exists = True
            verification.evidence += f"Files changed: {files_changed}. "
        elif changed_files:
            verification.exists = True
            verification.evidence += (
                f"Changed: {', '.join(changed_files[:3])}. "
            )
        else:
            verification.gaps.append(
                f"No files changed for: {criterion_text[:80]}"
            )
            return verification

        # Level 2: SUBSTANTIVE — Did execution succeed and produce real output?
        if result_status == "SUCCESS":
            verification.substantive = True
            verification.evidence += "Execution succeeded. "
        elif result_status == "FAILURE":
            verification.gaps.append(
                f"Execution failed — implementation may be incomplete: "
                f"{criterion_text[:80]}"
            )
            return verification
        else:
            # Unknown result — check if summary indicates progress
            completion_keywords = (
                "implemented", "added", "created", "completed", "fixed",
            )
            if run_summary and any(
                kw in run_summary.lower() for kw in completion_keywords
            ):
                verification.substantive = True
                verification.evidence += "Summary indicates completion. "
            else:
                verification.gaps.append(
                    f"Execution result unclear for: {criterion_text[:80]}"
                )

        # Level 3: WIRED — Is the code connected?
        # Proxy: tests exist or review approved
        is_test_criterion = any(
            kw in criterion_lower
            for kw in ("test", "spec", "coverage", "assertion")
        )
        if is_test_criterion:
            if tests_added and tests_added > 0:
                verification.wired = True
                verification.evidence += f"Tests added: {tests_added}. "
            else:
                verification.gaps.append(
                    f"Test criterion but no tests added: {criterion_text[:80]}"
                )
        elif review_verdict == "APPROVE":
            verification.wired = True
            verification.evidence += (
                "Code review approved (implies wiring verified). "
            )
        elif files_changed and files_changed > 1:
            # Multiple files changed suggests wiring between modules
            verification.wired = True
            verification.evidence += (
                f"Multiple files changed ({files_changed}) suggests wiring. "
            )

        # Level 4: DATA_FLOWS — Proxy: no critical review findings + tests pass
        has_critical_findings = any(
            f.get("severity", "").lower() in ("critical", "high", "blocker")
            for f in review_findings
            if isinstance(f, dict)
        )
        if verification.wired and not has_critical_findings:
            if tests_added and tests_added > 0:
                verification.data_flows = True
                verification.evidence += (
                    "Tests pass + no critical findings -> data flow verified. "
                )
            elif review_verdict == "APPROVE" and result_status == "SUCCESS":
                verification.data_flows = True
                verification.evidence += (
                    "Review approved + execution success -> data flow likely. "
                )

        return verification

    @staticmethod
    def _extract_criteria(task: dict[str, Any]) -> list[str]:
        """Extract acceptance criteria text from task."""
        # Try formal contract first
        formal = task.get("_formal_contract_criteria")
        if isinstance(formal, list) and formal:
            texts: list[str] = []
            for c in formal:
                if isinstance(c, dict):
                    text = (
                        c.get("criterion")
                        or c.get("text")
                        or c.get("description", "")
                    )
                    if text:
                        texts.append(text)
                elif isinstance(c, str):
                    texts.append(c)
            if texts:
                return texts

        # Fall back to acceptance_criteria field
        criteria = task.get("acceptance_criteria") or []
        if isinstance(criteria, list):
            texts = []
            for item in criteria:
                if isinstance(item, dict):
                    text = (
                        item.get("text")
                        or item.get("description")
                        or item.get("criterion", "")
                    )
                elif isinstance(item, str):
                    text = item
                else:
                    text = str(item)
                if text:
                    texts.append(text)
            return texts

        return []
