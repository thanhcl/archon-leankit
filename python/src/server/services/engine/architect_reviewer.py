"""
Architect Auto-Reviewer for LeanKit V3 Task Engine.

Calls Claude API to review CC execution results against task
requirements, then decides the next lifecycle transition.

Usage:
    reviewer = ArchitectReviewer(api_key="sk-...")
    review, action = await reviewer.review(task, execution_result)
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 2000
DEFAULT_TEMPERATURE = 0.3
DEFAULT_TIMEOUT = 60


@dataclass
class ArchitectReviewResult:
    """Result of an architect review."""

    verdict: str  # "approve" | "changes-requested" | "escalate"
    confidence: float  # 0.0 – 1.0
    findings: list[dict[str, str]] = field(default_factory=list)
    feedback: str = ""
    summary: str = ""
    raw_response: str = ""
    error: str | None = None


@dataclass
class ReviewAction:
    """Decided lifecycle transition after review."""

    next_status: str  # "review" | "assigned" | "escalated"
    reason: str
    changed_by: str = "architect-reviewer"
    warning: str | None = None  # e.g. low-confidence approval


class ArchitectReviewer:
    """Calls Claude API to review task execution results."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: int = DEFAULT_TIMEOUT,
        kb_context_provider: Any | None = None,
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self._kb_context_provider = kb_context_provider

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def review(
        self,
        task: dict[str, Any],
        execution_result: dict[str, Any],
    ) -> tuple[ArchitectReviewResult, ReviewAction]:
        """Review execution results and decide next action.

        Returns:
            Tuple of (review_result, review_action).
        """
        review_result = await self._call_claude(task, execution_result)
        action = self._decide_action(task, review_result)

        logger.info(
            f"Architect review complete | task_id={task.get('id')} | "
            f"verdict={review_result.verdict} | confidence={review_result.confidence:.2f} | "
            f"next_status={action.next_status}"
        )

        return review_result, action

    # ------------------------------------------------------------------
    # Claude API call
    # ------------------------------------------------------------------

    async def _call_claude(
        self,
        task: dict[str, Any],
        execution_result: dict[str, Any],
    ) -> ArchitectReviewResult:
        """Call Claude Messages API for the review."""
        system_prompt = self._build_system_prompt(task)
        user_message = self._build_user_message(task, execution_result)

        if not self.api_key:
            logger.error("No Anthropic API key configured")
            return ArchitectReviewResult(
                verdict="escalate",
                confidence=0.0,
                summary="Review skipped: no API key",
                error="ANTHROPIC_API_KEY not configured",
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    ANTHROPIC_MESSAGES_URL,
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "max_tokens": self.max_tokens,
                        "temperature": self.temperature,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": user_message}],
                    },
                )

                if response.status_code == 429:
                    return ArchitectReviewResult(
                        verdict="escalate",
                        confidence=0.0,
                        summary="Review skipped: rate limited",
                        error="Anthropic API rate limited",
                    )

                if response.status_code != 200:
                    body = response.text[:500]
                    return ArchitectReviewResult(
                        verdict="escalate",
                        confidence=0.0,
                        summary=f"Review failed: API returned {response.status_code}",
                        error=f"HTTP {response.status_code}: {body}",
                    )

                data = response.json()
                raw_text = self._extract_text(data)
                return self._parse_review(raw_text)

        except httpx.TimeoutException:
            logger.error("Claude API timeout during review")
            return ArchitectReviewResult(
                verdict="escalate",
                confidence=0.0,
                summary="Review skipped: API timeout",
                error="Anthropic API timeout",
            )
        except Exception as e:
            logger.error(f"Claude API error during review: {e}", exc_info=True)
            return ArchitectReviewResult(
                verdict="escalate",
                confidence=0.0,
                summary=f"Review failed: {e}",
                error=str(e),
            )

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_system_prompt(self, task: dict[str, Any]) -> str:
        source_app = task.get("source_app") or "unknown"

        return (
            f"You are the Architect reviewer for project {source_app}.\n"
            "Your role: review implementation results against requirements and quality standards.\n\n"
            "Review criteria:\n"
            "- CORRECTNESS: Does implementation match requirements and acceptance criteria?\n"
            "- TESTS: Sufficient tests? Edge cases covered?\n"
            "- SECURITY: Any vulnerabilities? (XSS, injection, auth bypass, key exposure)\n"
            "- CONVENTIONS: Does code follow project standards?\n"
            "- BACKWARD COMPAT: Any breaking changes?\n"
            "- PERFORMANCE: N+1 queries? Large allocations?\n\n"
            "Respond ONLY with valid JSON:\n"
            "{\n"
            '  "verdict": "approve" | "changes-requested" | "escalate",\n'
            '  "confidence": 0.0 to 1.0,\n'
            '  "findings": [\n'
            '    {"severity": "critical|warning|suggestion", "category": "...", "description": "..."}\n'
            "  ],\n"
            '  "feedback": "specific actionable feedback if changes requested",\n'
            '  "summary": "one sentence overall assessment"\n'
            "}"
        )

    @staticmethod
    def _build_user_message(
        task: dict[str, Any],
        execution_result: dict[str, Any],
    ) -> str:
        title = task.get("title", "Untitled")
        description = task.get("description", "")
        criteria = task.get("acceptance_criteria") or []

        criteria_text = "\n".join(
            f"- {c.get('text', str(c)) if isinstance(c, dict) else str(c)}"
            for c in criteria
        ) or "- Task completed as described"

        result_summary = execution_result.get("summary", "")
        files_changed = execution_result.get("files_changed", "unknown")
        tests_added = execution_result.get("tests_added", "unknown")
        exit_code = execution_result.get("exit_code", "unknown")
        duration = execution_result.get("duration_seconds", "unknown")
        stderr_preview = execution_result.get("stderr_preview", "")

        parts = [
            f"## Task: {title}",
            "",
            "### Description",
            description or "_No description_",
            "",
            "### Acceptance Criteria",
            criteria_text,
            "",
            "### Execution Result",
            f"- Exit code: {exit_code}",
            f"- Duration: {duration}s",
            f"- Files changed: {files_changed}",
            f"- Tests added: {tests_added}",
        ]

        if result_summary:
            parts += [f"- Summary: {result_summary}"]

        if stderr_preview:
            parts += ["", "### Stderr (preview)", f"```\n{stderr_preview[:500]}\n```"]

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(api_response: dict[str, Any]) -> str:
        """Extract text content from Claude Messages API response."""
        content = api_response.get("content", [])
        for block in content:
            if block.get("type") == "text":
                return block.get("text", "")
        return ""

    @staticmethod
    def _parse_review(raw_text: str) -> ArchitectReviewResult:
        """Parse JSON review from Claude's response."""
        # Try to extract JSON from the response (Claude may wrap it in markdown)
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            json_lines = []
            inside = False
            for line in lines:
                if line.strip().startswith("```") and not inside:
                    inside = True
                    continue
                if line.strip().startswith("```") and inside:
                    break
                if inside:
                    json_lines.append(line)
            text = "\n".join(json_lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return ArchitectReviewResult(
                verdict="escalate",
                confidence=0.0,
                summary="Failed to parse review response",
                raw_response=raw_text[:2000],
                error="Invalid JSON in Claude response",
            )

        verdict = data.get("verdict", "escalate")
        if verdict not in ("approve", "changes-requested", "escalate"):
            verdict = "escalate"

        confidence = data.get("confidence", 0.0)
        try:
            confidence = float(confidence)
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.0

        return ArchitectReviewResult(
            verdict=verdict,
            confidence=confidence,
            findings=data.get("findings", []),
            feedback=data.get("feedback", ""),
            summary=data.get("summary", ""),
            raw_response=raw_text[:2000],
        )

    # ------------------------------------------------------------------
    # Decision logic
    # ------------------------------------------------------------------

    @staticmethod
    def _decide_action(
        task: dict[str, Any],
        review: ArchitectReviewResult,
    ) -> ReviewAction:
        """Map review verdict to lifecycle transition.

        Rules:
        - Any critical security finding → escalated (never auto-approve)
        - verdict=escalate → escalated
        - verdict=approve, confidence >= 0.8 → review (Owner final check)
        - verdict=approve, confidence < 0.8 → review with warning
        - verdict=changes-requested, retry_count < max_retries → assigned (retry)
        - verdict=changes-requested, retry_count >= max_retries → escalated
        """
        # Check for critical security findings — always escalate
        has_critical_security = any(
            f.get("severity") == "critical"
            and f.get("category", "").lower() in ("security", "auth", "injection", "xss")
            for f in review.findings
        )
        if has_critical_security:
            return ReviewAction(
                next_status="escalated",
                reason=f"Critical security finding: {review.feedback or review.summary}",
            )

        if review.verdict == "escalate" or review.error:
            return ReviewAction(
                next_status="escalated",
                reason=review.feedback or review.summary or review.error or "Architect escalation",
            )

        if review.verdict == "approve":
            warning = None
            if review.confidence < 0.8:
                warning = f"Low confidence approval ({review.confidence:.2f})"
            return ReviewAction(
                next_status="review",
                reason=review.summary or "Approved by architect",
                warning=warning,
            )

        # changes-requested
        retry_count = task.get("retry_count") or 0
        max_retries = task.get("max_retries") or 3

        if retry_count < max_retries:
            return ReviewAction(
                next_status="assigned",
                reason=review.feedback or review.summary or "Changes requested",
            )
        else:
            return ReviewAction(
                next_status="escalated",
                reason=f"Max retries ({max_retries}) reached. Last feedback: {review.feedback or review.summary}",
            )
