"""
Multi-Perspective Architect Review — agent debate pattern.

Runs 3 specialized review perspectives in parallel:
  1. Security Critic   — XSS, injection, auth bypass, key exposure
  2. Performance Analyst — N+1 queries, unnecessary renders, large allocations
  3. API Contract Checker — field name mismatches between FE/BE/MCP

Each perspective produces its own verdict + confidence + findings.
A consensus function aggregates into a single ArchitectReviewResult.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ...config.logfire_config import get_logger
from .architect_reviewer import (
    ArchitectReviewResult,
    ReviewAction,
    ReviewConfig,
    ReviewProvider,
    _build_review_user_message,
)

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Perspective definitions
# ═══════════════════════════════════════════════════════════════════════════


PERSPECTIVE_PROMPTS: dict[str, str] = {
    "security": (
        "You are a **Security Critic** reviewing implementation results.\n"
        "Focus EXCLUSIVELY on security vulnerabilities:\n"
        "- XSS (cross-site scripting): unsanitized user input in HTML/JS output\n"
        "- SQL/NoSQL injection: dynamic queries without parameterization\n"
        "- Auth bypass: missing/weak authentication or authorization checks\n"
        "- Key/secret exposure: hardcoded credentials, keys in logs or responses\n"
        "- CSRF: missing token validation on state-changing requests\n"
        "- Insecure deserialization: untrusted data in pickle/eval/exec\n"
        "- Path traversal: unsanitized file paths\n\n"
        "Be strict. If you find ANY potential security issue, mark it.\n"
        "Weight your confidence based on exploitability and impact.\n\n"
        "Respond ONLY with valid JSON:\n"
        "{\n"
        '  "verdict": "approve" | "changes-requested" | "escalate",\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "findings": [\n'
        '    {"severity": "critical|warning|suggestion", "category": "security", "description": "..."}\n'
        "  ],\n"
        '  "feedback": "specific actionable security feedback",\n'
        '  "summary": "one sentence security assessment"\n'
        "}"
    ),
    "performance": (
        "You are a **Performance Analyst** reviewing implementation results.\n"
        "Focus EXCLUSIVELY on performance issues:\n"
        "- N+1 queries: loops that issue one DB query per item instead of batch\n"
        "- Unnecessary renders: React components re-rendering without data changes\n"
        "- Missing memoization: expensive computations recalculated on every render\n"
        "- Large allocations: loading entire tables/collections into memory\n"
        "- Missing pagination: unbounded list fetches\n"
        "- Synchronous blocking: blocking the event loop with CPU-heavy work\n"
        "- Missing indexes: queries on unindexed columns\n"
        "- Redundant API calls: duplicate fetches for the same data\n\n"
        "Be practical. Only flag issues that would have measurable impact.\n\n"
        "Respond ONLY with valid JSON:\n"
        "{\n"
        '  "verdict": "approve" | "changes-requested" | "escalate",\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "findings": [\n'
        '    {"severity": "critical|warning|suggestion", "category": "performance", "description": "..."}\n'
        "  ],\n"
        '  "feedback": "specific actionable performance feedback",\n'
        '  "summary": "one sentence performance assessment"\n'
        "}"
    ),
    "contract": (
        "You are an **API Contract Checker** reviewing implementation results.\n"
        "Focus EXCLUSIVELY on API contract consistency:\n"
        "- Field name mismatches: frontend expects 'userName' but backend returns 'user_name'\n"
        "- Missing fields: response missing fields that frontend/MCP tools depend on\n"
        "- Type mismatches: string vs number, array vs object, null handling\n"
        "- Status code inconsistency: wrong HTTP status codes for operations\n"
        "- Endpoint naming: REST convention violations, inconsistent URL patterns\n"
        "- Request/response shape changes: breaking changes to existing contracts\n"
        "- MCP tool parameter mismatches: tool params don't match service expectations\n\n"
        "Be precise. Flag concrete mismatches, not hypothetical ones.\n\n"
        "Respond ONLY with valid JSON:\n"
        "{\n"
        '  "verdict": "approve" | "changes-requested" | "escalate",\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "findings": [\n'
        '    {"severity": "critical|warning|suggestion", "category": "contract", "description": "..."}\n'
        "  ],\n"
        '  "feedback": "specific actionable contract feedback",\n'
        '  "summary": "one sentence contract assessment"\n'
        "}"
    ),
}

PERSPECTIVE_NAMES = list(PERSPECTIVE_PROMPTS.keys())


# ═══════════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class PerspectiveResult:
    """Result from a single review perspective."""

    perspective: str  # "security" | "performance" | "contract"
    review: ArchitectReviewResult
    weight: float = 1.0  # relative weight in consensus


@dataclass
class MultiPerspectiveResult:
    """Aggregated result from all perspectives."""

    consensus_verdict: str  # "approve" | "changes-requested" | "escalate"
    consensus_confidence: float
    perspective_results: list[PerspectiveResult] = field(default_factory=list)
    aggregated_findings: list[dict[str, str]] = field(default_factory=list)
    consensus_summary: str = ""
    dissenting_perspectives: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Consensus logic
# ═══════════════════════════════════════════════════════════════════════════


# Severity weights for scoring findings
_SEVERITY_WEIGHTS = {"critical": 10.0, "warning": 3.0, "suggestion": 1.0}

# Perspective weights — security findings carry more weight
_PERSPECTIVE_WEIGHTS: dict[str, float] = {
    "security": 1.5,
    "performance": 1.0,
    "contract": 1.2,
}


def compute_consensus(results: list[PerspectiveResult]) -> MultiPerspectiveResult:
    """Aggregate perspective results into a consensus verdict.

    Rules (ordered by priority):
    1. ANY perspective with critical security finding → escalate
    2. Majority (>=2) say changes-requested or escalate → changes-requested
    3. ALL approve → approve
    4. Mixed → use weighted confidence to decide

    Confidence = weighted average of per-perspective confidences,
    penalized by finding severity.
    """
    if not results:
        return MultiPerspectiveResult(
            consensus_verdict="escalate",
            consensus_confidence=0.0,
            consensus_summary="No perspective results available",
        )

    # Collect all findings
    all_findings: list[dict[str, str]] = []
    for pr in results:
        for f in pr.review.findings:
            finding = dict(f)
            finding["perspective"] = pr.perspective
            all_findings.append(finding)

    # Rule 1: Any critical security finding → escalate immediately
    has_critical_security = any(
        f.get("severity") == "critical"
        and f.get("category", "").lower() in ("security", "auth", "injection", "xss")
        for f in all_findings
    )
    if has_critical_security:
        return MultiPerspectiveResult(
            consensus_verdict="escalate",
            consensus_confidence=0.95,
            perspective_results=results,
            aggregated_findings=all_findings,
            consensus_summary="Critical security finding detected — escalation required",
            dissenting_perspectives=[],
        )

    # Count verdicts
    verdicts = {pr.perspective: pr.review.verdict for pr in results}
    negative_count = sum(1 for v in verdicts.values() if v in ("changes-requested", "escalate"))
    approve_count = sum(1 for v in verdicts.values() if v == "approve")

    # Identify dissenters (minority opinion)
    majority_approves = approve_count > negative_count
    dissenting = [
        p for p, v in verdicts.items()
        if (majority_approves and v != "approve") or (not majority_approves and v == "approve")
    ]

    # Compute weighted confidence
    total_weight = 0.0
    weighted_confidence = 0.0
    for pr in results:
        w = _PERSPECTIVE_WEIGHTS.get(pr.perspective, 1.0)
        weighted_confidence += pr.review.confidence * w
        total_weight += w

    avg_confidence = weighted_confidence / total_weight if total_weight > 0 else 0.0

    # Penalty for findings severity
    severity_penalty = sum(
        _SEVERITY_WEIGHTS.get(f.get("severity", "suggestion"), 1.0)
        for f in all_findings
    ) * 0.02  # 2% per severity-weighted finding
    avg_confidence = max(0.0, min(1.0, avg_confidence - severity_penalty))

    # Rule 2: Majority negative → changes-requested
    if negative_count >= 2:
        return MultiPerspectiveResult(
            consensus_verdict="changes-requested",
            consensus_confidence=avg_confidence,
            perspective_results=results,
            aggregated_findings=all_findings,
            consensus_summary=f"{negative_count}/{len(results)} perspectives flagged issues",
            dissenting_perspectives=dissenting,
        )

    # Rule 3: All approve → approve
    if approve_count == len(results):
        return MultiPerspectiveResult(
            consensus_verdict="approve",
            consensus_confidence=avg_confidence,
            perspective_results=results,
            aggregated_findings=all_findings,
            consensus_summary="All perspectives approve",
            dissenting_perspectives=[],
        )

    # Rule 4: Mixed (1 negative, 2 approve) → approve with reduced confidence
    return MultiPerspectiveResult(
        consensus_verdict="approve",
        consensus_confidence=avg_confidence * 0.85,  # 15% reduction for dissent
        perspective_results=results,
        aggregated_findings=all_findings,
        consensus_summary=f"Majority approve with dissent from: {', '.join(dissenting)}",
        dissenting_perspectives=dissenting,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Multi-Perspective Reviewer
# ═══════════════════════════════════════════════════════════════════════════


class MultiPerspectiveReviewer:
    """Runs 3 review perspectives in parallel, aggregates into consensus."""

    def __init__(self, provider_factory=None):
        """
        Args:
            provider_factory: Async callable(config: ReviewConfig) → ReviewProvider.
                Used to create LLM provider for API calls.
        """
        self._provider_factory = provider_factory

    async def review(
        self,
        task: dict[str, Any],
        execution_result: dict[str, Any],
        config: ReviewConfig,
    ) -> tuple[MultiPerspectiveResult, ArchitectReviewResult, ReviewAction]:
        """Run all perspectives in parallel and compute consensus.

        Returns:
            (multi_result, unified_review, action) — the multi-perspective breakdown,
            a unified ArchitectReviewResult (for backward compat), and the lifecycle action.
        """
        perspective_coros = [
            self._run_perspective(perspective, task, execution_result, config)
            for perspective in PERSPECTIVE_NAMES
        ]

        perspective_results = await asyncio.gather(*perspective_coros, return_exceptions=True)

        # Filter out exceptions, log them
        valid_results: list[PerspectiveResult] = []
        for name, result in zip(PERSPECTIVE_NAMES, perspective_results, strict=True):
            if isinstance(result, Exception):
                logger.error(f"Perspective {name} failed: {result}", exc_info=result)
                # Create a fallback result for failed perspectives
                valid_results.append(PerspectiveResult(
                    perspective=name,
                    review=ArchitectReviewResult(
                        verdict="escalate",
                        confidence=0.0,
                        summary=f"Perspective {name} failed: {result}",
                        error=str(result),
                        mode="api",
                    ),
                ))
            else:
                valid_results.append(result)

        consensus = compute_consensus(valid_results)

        # Build unified ArchitectReviewResult for backward compatibility
        unified = ArchitectReviewResult(
            verdict=consensus.consensus_verdict,
            confidence=consensus.consensus_confidence,
            findings=consensus.aggregated_findings,
            feedback=self._build_unified_feedback(valid_results),
            summary=consensus.consensus_summary,
            mode="multi-perspective",
        )

        # Use the standard decision logic from ArchitectReviewer
        from .architect_reviewer import ArchitectReviewer
        action = ArchitectReviewer._decide_action(task, unified, config)

        logger.info(
            f"Multi-perspective review | task_id={task.get('id')} | "
            f"verdicts={{{', '.join(f'{pr.perspective}={pr.review.verdict}' for pr in valid_results)}}} | "
            f"consensus={consensus.consensus_verdict} | confidence={consensus.consensus_confidence:.2f} | "
            f"next={action.next_status}"
        )

        return consensus, unified, action

    async def _run_perspective(
        self,
        perspective: str,
        task: dict[str, Any],
        execution_result: dict[str, Any],
        config: ReviewConfig,
    ) -> PerspectiveResult:
        """Run a single perspective review via LLM API."""
        system_prompt = PERSPECTIVE_PROMPTS[perspective]
        user_message = _build_review_user_message(task, execution_result)

        provider = await self._get_provider(config)
        review = await provider.call_api(system_prompt, user_message, config)
        review.mode = f"multi-perspective:{perspective}"

        return PerspectiveResult(
            perspective=perspective,
            review=review,
            weight=_PERSPECTIVE_WEIGHTS.get(perspective, 1.0),
        )

    async def _get_provider(self, config: ReviewConfig) -> ReviewProvider:
        """Get LLM provider from factory (supports both sync and async factories)."""
        if not self._provider_factory:
            raise ValueError("No provider_factory configured for MultiPerspectiveReviewer")
        import inspect
        result = self._provider_factory(config)
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _build_unified_feedback(results: list[PerspectiveResult]) -> str:
        """Combine feedback from all perspectives into a single string."""
        parts: list[str] = []
        for pr in results:
            if pr.review.feedback:
                parts.append(f"[{pr.perspective.upper()}] {pr.review.feedback}")
        return "\n".join(parts) if parts else ""


def multi_perspective_review_to_dict(result: MultiPerspectiveResult) -> dict[str, Any]:
    """Serialize MultiPerspectiveResult for storage on task."""
    return {
        "consensus_verdict": result.consensus_verdict,
        "consensus_confidence": result.consensus_confidence,
        "consensus_summary": result.consensus_summary,
        "dissenting_perspectives": result.dissenting_perspectives,
        "aggregated_findings": result.aggregated_findings,
        "perspectives": [
            {
                "perspective": pr.perspective,
                "verdict": pr.review.verdict,
                "confidence": pr.review.confidence,
                "findings": pr.review.findings,
                "feedback": pr.review.feedback,
                "summary": pr.review.summary,
            }
            for pr in result.perspective_results
        ],
    }
