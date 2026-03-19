"""
Hybrid Architect Auto-Reviewer for LeanKit V3 Task Engine.

Supports two review modes:
  1. self-review: parse structured self-review from CC output (zero cost)
  2. api: call external LLM (Anthropic / OpenAI / Google) for review

Owner configures per-complexity: simple tasks → self-review, complex → api, etc.

Usage:
    reviewer = ArchitectReviewer()
    review, action = await reviewer.review(task, execution_result, config)
"""

import json
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# ── Security keyword detection ────────────────────────────────────────────

_SECURITY_KEYWORDS = frozenset([
    "auth", "security", "crypto", "pkcs", "hsm", "key", "encrypt",
    "decrypt", "token", "password", "certificate", "tls", "ssl",
    "injection", "xss", "csrf", "oauth", "jwt", "saml", "credential",
])

# ── Regex for self-review parsing ─────────────────────────────────────────

_SELF_VERDICT_RE = re.compile(
    r"\*{0,2}SELF[_-]?REVIEW:?\*{0,2}\s*(PASS|NEEDS_ATTENTION)",
    re.IGNORECASE,
)
_SELF_CONFIDENCE_RE = re.compile(
    r"\*{0,2}REVIEW[_-]?CONFIDENCE:?\*{0,2}\s*([\d.]+)",
    re.IGNORECASE,
)
_SELF_FINDINGS_RE = re.compile(
    r"\*{0,2}REVIEW[_-]?FINDINGS:?\*{0,2}\s*(\[.*\])",
    re.IGNORECASE | re.DOTALL,
)

# ── Provider defaults ─────────────────────────────────────────────────────

PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "model": "claude-sonnet-4-20250514",
        "url": "https://api.anthropic.com/v1/messages",
        "key_name": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "model": "gpt-4o",
        "url": "https://api.openai.com/v1/chat/completions",
        "key_name": "OPENAI_API_KEY",
    },
    "google": {
        "model": "gemini-2.0-flash",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "key_name": "GOOGLE_API_KEY",
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ReviewConfig:
    """Per-project reviewer configuration."""

    review_mode: str = "self-review"  # "self-review" | "api" | "multi-perspective"
    security_override_to_api: bool = True  # CC high-risk + security → force API

    provider: str = "anthropic"  # "anthropic" | "openai" | "google"
    model: str = ""  # empty → auto from PROVIDER_DEFAULTS
    temperature: float = 0.3
    max_tokens: int = 2000
    timeout: int = 60

    api_fallback_to_self_review: bool = True

    independent_review_enabled: bool = True  # Toggle independent code review after self-review

    confidence_approve_threshold: float = 0.8
    confidence_retry_threshold: float = 0.5


@dataclass
class ArchitectReviewResult:
    """Result of an architect review."""

    verdict: str  # "approve" | "changes-requested" | "escalate"
    confidence: float
    findings: list[dict[str, str]] = field(default_factory=list)
    feedback: str = ""
    summary: str = ""
    mode: str = ""  # "self-review" | "api"
    provider: str = ""  # "anthropic" | "openai" | "google" | ""
    raw_response: str = ""
    error: str | None = None


@dataclass
class QualityGateScore:
    """Compound quality gate score from review results."""

    tests_pass: float  # 0-100, weight 30%
    self_review: float  # 0-100, weight 20%
    confidence: float  # 0-100, weight 20%
    code_review_clean: float  # 0-100, weight 20%
    acceptance_criteria: float  # 0-100, weight 10%
    compound_score: float  # weighted average 0-100
    gate_result: str  # "pass" | "retry" | "escalate"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tests_pass": self.tests_pass,
            "self_review": self.self_review,
            "confidence": self.confidence,
            "code_review_clean": self.code_review_clean,
            "acceptance_criteria": self.acceptance_criteria,
            "compound_score": self.compound_score,
            "gate_result": self.gate_result,
        }


@dataclass
class ReviewHistoryEntry:
    """A single entry in the review history — append-only."""

    review_id: str
    review_number: int
    reviewed_at: str
    mode: str
    provider: str
    verdict: str
    confidence: float
    quality_gate: dict[str, Any]
    findings: list[dict[str, str]]
    feedback: str
    summary: str
    retry_count: int
    error: str | None = None
    escalation_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "review_id": self.review_id,
            "review_number": self.review_number,
            "reviewed_at": self.reviewed_at,
            "mode": self.mode,
            "provider": self.provider,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "quality_gate": self.quality_gate,
            "findings": self.findings,
            "feedback": self.feedback,
            "summary": self.summary,
            "retry_count": self.retry_count,
        }
        if self.error:
            entry["error"] = self.error
        if self.escalation_reason:
            entry["escalation_reason"] = self.escalation_reason
        return entry


@dataclass
class ReviewAction:
    """Decided lifecycle transition after review."""

    next_status: str  # "review" | "assigned" | "escalated"
    reason: str
    changed_by: str = "architect-reviewer"
    warning: str | None = None
    escalation_reason: str | None = None  # structured reason code for escalations


# ═══════════════════════════════════════════════════════════════════════════
# Shared system prompt
# ═══════════════════════════════════════════════════════════════════════════

SELF_REVIEW_PROMPT_SECTION = """
## Self-Review (REQUIRED before reporting)
After implementation, review your own changes:
1. Check each acceptance criteria — all must pass
2. Security scan: XSS, injection, auth bypass, key exposure
3. Backward compatibility: existing APIs must not break
4. Performance: no N+1 queries, no large allocations

Include in your output:
SELF_REVIEW: PASS|NEEDS_ATTENTION
REVIEW_CONFIDENCE: 0.0-1.0
REVIEW_FINDINGS: [{"severity":"critical|warning|suggestion","category":"...","description":"..."}]
"""


def _build_review_system_prompt(task: dict[str, Any]) -> str:
    source_app = task.get("source_app") or "unknown"
    return (
        f"You are the Architect reviewer for project {source_app}.\n"
        "Review implementation results against requirements and quality standards.\n\n"
        "Review criteria:\n"
        "- CORRECTNESS: Does implementation match requirements and acceptance criteria?\n"
        "- TESTS: Sufficient tests? Edge cases covered?\n"
        "- SECURITY: Any vulnerabilities? (XSS, injection, auth bypass)\n"
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
        '  "summary": "one sentence assessment"\n'
        "}"
    )


def _build_review_user_message(
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
        parts.append(f"- Summary: {result_summary}")
    if stderr_preview:
        parts += ["", "### Stderr (preview)", f"```\n{stderr_preview[:500]}\n```"]
    return "\n".join(parts)


def _parse_llm_json(raw_text: str) -> ArchitectReviewResult:
    """Parse JSON review from any LLM's text response."""
    text = raw_text.strip()

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        json_lines: list[str] = []
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
            verdict="escalate", confidence=0.0,
            summary="Failed to parse review response",
            raw_response=raw_text[:2000],
            error="Invalid JSON in LLM response",
        )

    verdict = data.get("verdict", "escalate")
    if verdict not in ("approve", "changes-requested", "escalate"):
        verdict = "escalate"

    confidence = data.get("confidence", 0.0)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
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


# ═══════════════════════════════════════════════════════════════════════════
# Quality Gate Scoring
# ═══════════════════════════════════════════════════════════════════════════

# Severity deduction weights for code_review_clean scoring
_SEVERITY_DEDUCTIONS = {"critical": 40, "warning": 15, "suggestion": 5}


def calculate_quality_gate_score(
    review: ArchitectReviewResult,
    execution_result: dict[str, Any],
) -> QualityGateScore:
    """Calculate compound quality gate score.

    Weights: tests_pass 30%, self_review 20%, confidence 20%,
             code_review_clean 20%, acceptance_criteria 10%.

    Score >= 80 → pass, 60-79 → retry, < 60 → escalate.
    """
    # tests_pass: 100 if exit_code == 0 and not timed_out, else 0
    exit_code = execution_result.get("exit_code", -1)
    timed_out = execution_result.get("timed_out", False)
    tests_pass = 100.0 if exit_code == 0 and not timed_out else 0.0

    # self_review: based on CC's self-review field (explicit field takes precedence)
    result_str = execution_result.get("result", "").upper()
    self_review_field = execution_result.get("self_review", "").upper()
    if self_review_field == "NEEDS_ATTENTION":
        self_review = 40.0
    elif self_review_field == "PASS" or result_str == "SUCCESS":
        self_review = 100.0
    else:
        self_review = 50.0  # unknown/missing

    # confidence: direct mapping from review confidence (0-1 → 0-100)
    confidence = review.confidence * 100.0

    # code_review_clean: start at 100, deduct per finding severity
    code_review_clean = 100.0
    for finding in review.findings:
        severity = finding.get("severity", "suggestion")
        code_review_clean -= _SEVERITY_DEDUCTIONS.get(severity, 5)
    code_review_clean = max(0.0, code_review_clean)

    # acceptance_criteria: based on verdict
    if review.verdict == "approve":
        acceptance_criteria = 100.0
    elif review.verdict == "changes-requested":
        acceptance_criteria = 30.0
    else:
        acceptance_criteria = 0.0

    # Compound score with weights
    compound_score = (
        tests_pass * 0.30
        + self_review * 0.20
        + confidence * 0.20
        + code_review_clean * 0.20
        + acceptance_criteria * 0.10
    )

    # Gate result
    if compound_score >= 80:
        gate_result = "pass"
    elif compound_score >= 60:
        gate_result = "retry"
    else:
        gate_result = "escalate"

    return QualityGateScore(
        tests_pass=round(tests_pass, 1),
        self_review=round(self_review, 1),
        confidence=round(confidence, 1),
        code_review_clean=round(code_review_clean, 1),
        acceptance_criteria=round(acceptance_criteria, 1),
        compound_score=round(compound_score, 1),
        gate_result=gate_result,
    )


def build_review_history_entry(
    review: ArchitectReviewResult,
    action: ReviewAction,
    quality_gate: QualityGateScore,
    task: dict[str, Any],
    existing_history: list[dict[str, Any]] | None = None,
) -> ReviewHistoryEntry:
    """Build a review history entry to append to the task's review_history array."""
    history = existing_history or []
    review_number = len(history) + 1

    return ReviewHistoryEntry(
        review_id=str(uuid.uuid4()),
        review_number=review_number,
        reviewed_at=datetime.now(timezone.utc).isoformat(),
        mode=review.mode,
        provider=review.provider,
        verdict=review.verdict,
        confidence=review.confidence,
        quality_gate=quality_gate.to_dict(),
        findings=review.findings,
        feedback=review.feedback,
        summary=review.summary,
        retry_count=task.get("retry_count", 0),
        error=review.error,
        escalation_reason=action.escalation_reason,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Review Providers
# ═══════════════════════════════════════════════════════════════════════════


class ReviewProvider(ABC):
    """Base class for LLM review providers."""

    @abstractmethod
    async def call_api(
        self, system_prompt: str, user_message: str, config: ReviewConfig,
    ) -> ArchitectReviewResult:
        ...


class AnthropicReviewProvider(ReviewProvider):
    """Anthropic Messages API provider."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def call_api(
        self, system_prompt: str, user_message: str, config: ReviewConfig,
    ) -> ArchitectReviewResult:
        model = config.model or PROVIDER_DEFAULTS["anthropic"]["model"]
        url = PROVIDER_DEFAULTS["anthropic"]["url"]

        async with httpx.AsyncClient(timeout=config.timeout) as client:
            resp = await client.post(
                url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": config.max_tokens,
                    "temperature": config.temperature,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_message}],
                },
            )

        if resp.status_code == 429:
            return ArchitectReviewResult(
                verdict="escalate", confidence=0.0,
                summary="Review skipped: rate limited", error="Rate limited",
            )
        if resp.status_code != 200:
            return ArchitectReviewResult(
                verdict="escalate", confidence=0.0,
                summary=f"API error {resp.status_code}",
                error=f"HTTP {resp.status_code}: {resp.text[:500]}",
            )

        data = resp.json()
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "")
                break

        result = _parse_llm_json(text)
        result.provider = "anthropic"
        result.mode = "api"
        return result


class OpenAIReviewProvider(ReviewProvider):
    """OpenAI Chat Completions API provider (also used by Google via OpenAI-compat)."""

    def __init__(self, api_key: str, provider_name: str = "openai"):
        self.api_key = api_key
        self.provider_name = provider_name

    async def call_api(
        self, system_prompt: str, user_message: str, config: ReviewConfig,
    ) -> ArchitectReviewResult:
        defaults = PROVIDER_DEFAULTS.get(self.provider_name, PROVIDER_DEFAULTS["openai"])
        model = config.model or defaults["model"]
        url = defaults["url"]

        headers: dict[str, str] = {
            "content-type": "application/json",
        }
        # Google uses key param, OpenAI uses Bearer token
        if self.provider_name == "google":
            url = f"{url}?key={self.api_key}"
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=config.timeout) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "max_tokens": config.max_tokens,
                    "temperature": config.temperature,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                },
            )

        if resp.status_code == 429:
            return ArchitectReviewResult(
                verdict="escalate", confidence=0.0,
                summary="Review skipped: rate limited", error="Rate limited",
            )
        if resp.status_code != 200:
            return ArchitectReviewResult(
                verdict="escalate", confidence=0.0,
                summary=f"API error {resp.status_code}",
                error=f"HTTP {resp.status_code}: {resp.text[:500]}",
            )

        data = resp.json()
        choices = data.get("choices", [])
        text = choices[0]["message"]["content"] if choices else ""

        result = _parse_llm_json(text)
        result.provider = self.provider_name
        result.mode = "api"
        return result


# ═══════════════════════════════════════════════════════════════════════════
# Main Reviewer
# ═══════════════════════════════════════════════════════════════════════════


class ArchitectReviewer:
    """Hybrid architect reviewer — self-review or API based on config."""

    def __init__(self, credential_getter=None):
        """
        Args:
            credential_getter: Async callable(key: str) → str|None.
                Defaults to credential_service.get_credential at runtime.
        """
        self._credential_getter = credential_getter

    async def _get_credential(self, key: str) -> str | None:
        if self._credential_getter:
            return await self._credential_getter(key)
        # Lazy import to avoid circular deps
        from ..credential_service import credential_service
        return await credential_service.get_credential(key)

    # ── Public API ────────────────────────────────────────────────────────

    async def review(
        self,
        task: dict[str, Any],
        execution_result: dict[str, Any],
        config: ReviewConfig | None = None,
    ) -> tuple[ArchitectReviewResult, ReviewAction]:
        """Review execution results and decide next action.

        Supports 3 modes: self-review, api, multi-perspective.
        Multi-perspective runs 3 parallel reviewers (security, performance, contract)
        and returns a consensus result. The multi_perspective_detail is stored on the
        result's raw_response for callers that need per-perspective breakdown.
        """
        cfg = config or ReviewConfig()
        mode = self._get_mode(task, cfg, execution_result)

        if mode == "multi-perspective":
            review_result, action = await self._multi_perspective_review(task, execution_result, cfg)
        elif mode == "self-review":
            review_result = self._parse_self_review(execution_result)
            action = self._decide_action(task, review_result, cfg)
        else:
            review_result = await self._api_review(task, execution_result, cfg)
            action = self._decide_action(task, review_result, cfg)

        logger.info(
            f"Architect review | task_id={task.get('id')} | "
            f"mode={review_result.mode} | verdict={review_result.verdict} | "
            f"confidence={review_result.confidence:.2f} | next={action.next_status}"
        )

        return review_result, action

    # ── Mode selection ────────────────────────────────────────────────────

    @staticmethod
    def _get_mode(
        task: dict[str, Any],
        config: ReviewConfig,
        execution_result: dict[str, Any] | None = None,
    ) -> str:
        """Determine review mode using CC's own task assessment.

        If CC assessed the task as high-risk AND task is security-sensitive,
        override to API mode regardless of global setting.
        Otherwise use the global review_mode.
        Valid modes: "self-review", "api", "multi-perspective".
        """
        if config.security_override_to_api and execution_result:
            estimated_risk = execution_result.get("estimated_risk", "").lower()
            if estimated_risk == "high" and ArchitectReviewer._is_security_sensitive(task):
                # For multi-perspective mode, keep it (already includes security perspective)
                if config.review_mode == "multi-perspective":
                    return "multi-perspective"
                return "api"
        return config.review_mode

    @staticmethod
    def _is_security_sensitive(task: dict[str, Any]) -> bool:
        text = (
            (task.get("title") or "") + " " + (task.get("description") or "")
        ).lower()
        return any(kw in text for kw in _SECURITY_KEYWORDS)

    # ── Self-review parsing ───────────────────────────────────────────────

    @staticmethod
    def _extract_cc_text(stdout: str) -> str:
        """Extract searchable text from CC stdout.

        CC output may be:
        - Plain text containing SELF_REVIEW markers directly
        - JSON like {"type":"result","result":"...SELF_REVIEW: PASS..."}
        - Multiple JSON lines (one per line)

        Returns the best text to search for self-review markers.
        If JSON with a 'result'/'text' field is found, returns that
        (avoids issues with escaped quotes in raw JSON).
        """
        if not stdout:
            return ""

        extracted: list[str] = []

        # Try parsing entire stdout as single JSON object
        stripped = stdout.strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict):
                    result_text = data.get("result") or data.get("text") or ""
                    if result_text:
                        extracted.append(result_text)
            except (json.JSONDecodeError, TypeError):
                pass

        # Try parsing each line as JSON (multi-line CC output)
        if not extracted:
            for line in stdout.splitlines():
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                    if isinstance(data, dict):
                        result_text = data.get("result") or data.get("text") or ""
                        if result_text:
                            extracted.append(result_text)
                except (json.JSONDecodeError, TypeError):
                    continue

        # If we extracted text from JSON, use that; otherwise use raw stdout
        return "\n".join(extracted) if extracted else stdout

    @staticmethod
    def _parse_self_review(execution_result: dict[str, Any]) -> ArchitectReviewResult:
        """Parse structured self-review from CC stdout."""
        stdout = execution_result.get("stdout") or ""
        text = ArchitectReviewer._extract_cc_text(stdout)

        m = _SELF_VERDICT_RE.search(text)
        if not m:
            # No self-review block → treat as needs attention
            return ArchitectReviewResult(
                verdict="changes-requested",
                confidence=0.5,
                summary="No self-review output found in CC response",
                mode="self-review",
            )

        verdict_raw = m.group(1).upper()
        verdict = "approve" if verdict_raw == "PASS" else "changes-requested"

        confidence = 0.7
        m_conf = _SELF_CONFIDENCE_RE.search(text)
        if m_conf:
            try:
                confidence = max(0.0, min(1.0, float(m_conf.group(1))))
            except ValueError:
                pass

        findings: list[dict[str, str]] = []
        m_find = _SELF_FINDINGS_RE.search(text)
        if m_find:
            try:
                findings = json.loads(m_find.group(1))
            except json.JSONDecodeError:
                pass

        return ArchitectReviewResult(
            verdict=verdict,
            confidence=confidence,
            findings=findings,
            summary=f"Self-review: {verdict_raw}",
            mode="self-review",
        )

    # ── Multi-perspective review ─────────────────────────────────────────

    async def _multi_perspective_review(
        self,
        task: dict[str, Any],
        execution_result: dict[str, Any],
        config: ReviewConfig,
    ) -> tuple[ArchitectReviewResult, ReviewAction]:
        """Run 3-perspective parallel review and return consensus."""
        from .multi_perspective_reviewer import (
            MultiPerspectiveReviewer,
            multi_perspective_review_to_dict,
        )

        try:
            mp_reviewer = MultiPerspectiveReviewer(
                provider_factory=lambda cfg: self._build_provider(cfg),
            )
            consensus, unified, action = await mp_reviewer.review(
                task, execution_result, config,
            )

            # Store per-perspective breakdown in raw_response for callers
            import json
            unified.raw_response = json.dumps(multi_perspective_review_to_dict(consensus))

            return unified, action

        except Exception as e:
            logger.error(f"Multi-perspective review failed: {e}", exc_info=True)
            if config.api_fallback_to_self_review:
                logger.warning("Falling back to self-review from multi-perspective")
                review_result = self._parse_self_review(execution_result)
                review_result.error = f"Multi-perspective failed ({e}), used self-review fallback"
                action = self._decide_action(task, review_result, config)
                return review_result, action
            review_result = ArchitectReviewResult(
                verdict="escalate", confidence=0.0,
                summary=f"Multi-perspective review failed: {e}",
                error=str(e), mode="multi-perspective",
            )
            action = self._decide_action(task, review_result, config)
            return review_result, action

    # ── API review ────────────────────────────────────────────────────────

    async def _api_review(
        self,
        task: dict[str, Any],
        execution_result: dict[str, Any],
        config: ReviewConfig,
    ) -> ArchitectReviewResult:
        try:
            provider = await self._build_provider(config)
            system_prompt = _build_review_system_prompt(task)
            user_message = _build_review_user_message(task, execution_result)
            return await provider.call_api(system_prompt, user_message, config)
        except Exception as e:
            logger.error(f"API review failed: {e}", exc_info=True)
            if config.api_fallback_to_self_review:
                logger.warning("Falling back to self-review")
                result = self._parse_self_review(execution_result)
                result.error = f"API failed ({e}), used self-review fallback"
                return result
            return ArchitectReviewResult(
                verdict="escalate", confidence=0.0,
                summary=f"API review failed: {e}",
                error=str(e), mode="api", provider=config.provider,
            )

    async def _build_provider(self, config: ReviewConfig) -> ReviewProvider:
        defaults = PROVIDER_DEFAULTS.get(config.provider)
        if not defaults:
            raise ValueError(f"Unknown provider: {config.provider}")

        api_key = await self._get_credential(defaults["key_name"])
        if not api_key:
            raise ValueError(f"{defaults['key_name']} not configured")

        if config.provider == "anthropic":
            return AnthropicReviewProvider(api_key)
        # OpenAI and Google both use OpenAI-compatible chat completions
        return OpenAIReviewProvider(api_key, provider_name=config.provider)

    # ── Decision logic ────────────────────────────────────────────────────

    @staticmethod
    def _decide_action(
        task: dict[str, Any],
        review: ArchitectReviewResult,
        config: ReviewConfig | None = None,
    ) -> ReviewAction:
        """Map review verdict + confidence to lifecycle transition.

        Uses configurable thresholds (same logic for self-review and API):

        1. Critical security finding → ALWAYS escalated
        2. verdict=approve + confidence >= approve_threshold → review (Owner)
        3. verdict=approve + confidence < approve_threshold → escalated (low confidence)
        4. verdict=changes-requested + retries left → assigned (auto-retry)
        5. verdict=changes-requested + no retries → escalated
        6. verdict=escalate or error → escalated
        """
        cfg = config or ReviewConfig()
        confidence = review.confidence
        retry_count = task.get("retry_count") or 0
        max_retries = task.get("max_retries") or 3

        def _esc_reason(feedback: str, conf: float) -> str:
            return f"Escalated: {feedback}. Review confidence: {conf:.2f}. Details: {review.feedback or review.summary}"

        # Rule 1: Critical security finding → ALWAYS escalate
        has_critical_security = any(
            f.get("severity") == "critical"
            and f.get("category", "").lower() in ("security", "auth", "injection", "xss")
            for f in review.findings
        )
        if has_critical_security:
            reason = _esc_reason("critical_security_finding", confidence)
            return ReviewAction(
                next_status="escalated",
                reason=reason,
                escalation_reason="critical_security_finding",
            )

        # Rule 6 (early): error → escalated
        if review.error:
            reason = _esc_reason("api_failure", confidence)
            return ReviewAction(
                next_status="escalated",
                reason=reason,
                escalation_reason="api_failure",
            )

        # Rule 6: verdict=escalate → escalated
        if review.verdict == "escalate":
            reason = _esc_reason("reviewer_escalate", confidence)
            return ReviewAction(
                next_status="escalated",
                reason=reason,
                escalation_reason="reviewer_escalate",
            )

        # Rule 2–3: verdict=approve
        if review.verdict == "approve":
            if confidence >= cfg.confidence_approve_threshold:
                return ReviewAction(
                    next_status="review",
                    reason=review.summary or "Approved by architect",
                )
            # Low confidence approve → escalate for Owner decision
            reason = _esc_reason("low_confidence_approve", confidence)
            return ReviewAction(
                next_status="escalated",
                reason=reason,
                escalation_reason="low_confidence_approve",
            )

        # Rule 4–5: verdict=changes-requested
        if retry_count < max_retries:
            return ReviewAction(
                next_status="assigned",
                reason=review.feedback or review.summary or "Changes requested",
            )
        reason = _esc_reason("max_retries_exceeded", confidence)
        return ReviewAction(
            next_status="escalated",
            reason=reason,
            escalation_reason="max_retries_exceeded",
        )
