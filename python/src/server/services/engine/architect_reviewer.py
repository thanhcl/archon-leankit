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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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

_SELF_VERDICT_RE = re.compile(r"SELF_REVIEW:\s*(PASS|NEEDS_ATTENTION)", re.IGNORECASE)
_SELF_CONFIDENCE_RE = re.compile(r"REVIEW_CONFIDENCE:\s*([\d.]+)", re.IGNORECASE)
_SELF_FINDINGS_RE = re.compile(r"REVIEW_FINDINGS:\s*(\[.*\])", re.IGNORECASE | re.DOTALL)

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

    simple_task_mode: str = "self-review"  # "self-review" | "api"
    complex_task_mode: str = "api"
    security_sensitive_mode: str = "api"

    provider: str = "anthropic"  # "anthropic" | "openai" | "google"
    model: str = ""  # empty → auto from PROVIDER_DEFAULTS
    temperature: float = 0.3
    max_tokens: int = 2000
    timeout: int = 60

    api_fallback_to_self_review: bool = True


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
class ReviewAction:
    """Decided lifecycle transition after review."""

    next_status: str  # "review" | "assigned" | "escalated"
    reason: str
    changed_by: str = "architect-reviewer"
    warning: str | None = None


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
        """Review execution results and decide next action."""
        cfg = config or ReviewConfig()
        mode = self._get_mode(task, cfg)

        if mode == "self-review":
            review_result = self._parse_self_review(execution_result)
        else:
            review_result = await self._api_review(task, execution_result, cfg)

        action = self._decide_action(task, review_result)

        logger.info(
            f"Architect review | task_id={task.get('id')} | "
            f"mode={review_result.mode} | verdict={review_result.verdict} | "
            f"confidence={review_result.confidence:.2f} | next={action.next_status}"
        )

        return review_result, action

    # ── Mode selection ────────────────────────────────────────────────────

    @staticmethod
    def _get_mode(task: dict[str, Any], config: ReviewConfig) -> str:
        if ArchitectReviewer._is_security_sensitive(task):
            return config.security_sensitive_mode
        if task.get("complexity") == "complex":
            return config.complex_task_mode
        return config.simple_task_mode

    @staticmethod
    def _is_security_sensitive(task: dict[str, Any]) -> bool:
        text = (
            (task.get("title") or "") + " " + (task.get("description") or "")
        ).lower()
        return any(kw in text for kw in _SECURITY_KEYWORDS)

    # ── Self-review parsing ───────────────────────────────────────────────

    @staticmethod
    def _parse_self_review(execution_result: dict[str, Any]) -> ArchitectReviewResult:
        """Parse structured self-review from CC stdout."""
        stdout = execution_result.get("stdout") or ""

        m = _SELF_VERDICT_RE.search(stdout)
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
        m_conf = _SELF_CONFIDENCE_RE.search(stdout)
        if m_conf:
            try:
                confidence = max(0.0, min(1.0, float(m_conf.group(1))))
            except ValueError:
                pass

        findings: list[dict[str, str]] = []
        m_find = _SELF_FINDINGS_RE.search(stdout)
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
    ) -> ReviewAction:
        """Map review verdict to lifecycle transition.

        Rules:
        - Critical security finding → ALWAYS escalated
        - verdict=escalate or error → escalated
        - verdict=approve, confidence >= 0.8 → review
        - verdict=approve, confidence < 0.8 → review with warning
        - verdict=changes-requested, retries left → assigned (auto-retry)
        - verdict=changes-requested, no retries → escalated
        """
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
        return ReviewAction(
            next_status="escalated",
            reason=f"Max retries ({max_retries}) reached. Last feedback: {review.feedback or review.summary}",
        )
