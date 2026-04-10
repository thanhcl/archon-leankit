"""
Error Classifier — Categorizes execution errors as FATAL or TRANSIENT.

Adopted from upstream Archon v0.3.2 error classification pattern.
FATAL errors are never retried (auth, permissions, budget).
TRANSIENT errors are retried with exponential backoff (network, rate-limit).

Usage:
    classifier = ErrorClassifier()
    category = classifier.classify(error_message, exit_code=1)
    if category == ErrorCategory.FATAL:
        # Escalate immediately, do not retry
    elif category == ErrorCategory.TRANSIENT:
        # Retry with backoff
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)


class ErrorCategory(str, Enum):
    """Error categories that determine retry behavior."""

    FATAL = "fatal"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


# Patterns checked against lowercased error text.
# Order matters: FATAL is checked first.

FATAL_PATTERNS: list[str] = [
    # Auth / permissions
    "unauthorized",
    "forbidden",
    "401 ",
    "403 ",
    "authentication failed",
    "permission denied",
    "access denied",
    "invalid api key",
    "invalid token",
    "expired token",
    # Budget / billing
    "credit balance",
    "insufficient credits",
    "billing",
    "quota exceeded",
    "usage limit",
    # Configuration
    "missing required",
    "invalid configuration",
    "schema validation",
    # Binary / runner
    "binary not found",
    "command not found",
    "no such file or directory",
    "exec format error",
]

TRANSIENT_PATTERNS: list[str] = [
    # Network
    "timeout",
    "timed out",
    "econnrefused",
    "econnreset",
    "enotfound",
    "epipe",
    "network error",
    "connection reset",
    "connection refused",
    "socket hang up",
    # Rate limiting
    "rate limit",
    "rate_limit",
    "429 ",
    "too many requests",
    "throttl",
    # Server errors (temporary)
    "502 ",
    "503 ",
    "504 ",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "internal server error",
    "overloaded",
    # Process / resource
    "exited with code",
    "killed",
    "oom",
    "out of memory",
    "resource temporarily unavailable",
    # API capacity
    "capacity",
    "overloaded_error",
]

# Named exception classes that are always FATAL regardless of message content.
FATAL_EXCEPTION_NAMES: set[str] = {
    "AuthenticationError",
    "PermissionError",
    "ConfigurationError",
    "EnvLeakError",
    "BudgetExceededError",
}

# Named exception classes that are always TRANSIENT.
TRANSIENT_EXCEPTION_NAMES: set[str] = {
    "TimeoutError",
    "ConnectionError",
    "RateLimitError",
}


class ErrorClassifier:
    """Classifies execution errors to determine retry behavior.

    Classification priority:
    1. Exception class name (highest specificity)
    2. FATAL pattern match (fail-fast, never retry)
    3. TRANSIENT pattern match (retry with backoff)
    4. UNKNOWN (fallback — defaults to retry per policy)
    """

    def __init__(
        self,
        extra_fatal_patterns: list[str] | None = None,
        extra_transient_patterns: list[str] | None = None,
    ) -> None:
        self._fatal_patterns = FATAL_PATTERNS + (extra_fatal_patterns or [])
        self._transient_patterns = TRANSIENT_PATTERNS + (extra_transient_patterns or [])

        # Pre-compile for performance
        self._fatal_re = re.compile(
            "|".join(re.escape(p) for p in self._fatal_patterns),
            re.IGNORECASE,
        )
        self._transient_re = re.compile(
            "|".join(re.escape(p) for p in self._transient_patterns),
            re.IGNORECASE,
        )

    def classify(
        self,
        error_text: str,
        exception_name: str | None = None,
        exit_code: int | None = None,
        stderr: str | None = None,
    ) -> ErrorCategory:
        """Classify an error as FATAL, TRANSIENT, or UNKNOWN.

        Args:
            error_text: The error message or reason string.
            exception_name: Python exception class name (e.g., "TimeoutError").
            exit_code: Process exit code (if applicable).
            stderr: Process stderr output (checked as additional signal).

        Returns:
            ErrorCategory indicating retry behavior.
        """
        # 1. Check exception class name first (highest priority)
        if exception_name:
            if exception_name in FATAL_EXCEPTION_NAMES:
                logger.debug(f"Error classified as FATAL by exception name: {exception_name}")
                return ErrorCategory.FATAL
            if exception_name in TRANSIENT_EXCEPTION_NAMES:
                logger.debug(f"Error classified as TRANSIENT by exception name: {exception_name}")
                return ErrorCategory.TRANSIENT

        # Combine error text and stderr for pattern matching
        combined = error_text
        if stderr:
            combined = f"{error_text}\n{stderr}"

        # 2. Check FATAL patterns (fail-fast, checked first)
        if self._fatal_re.search(combined):
            matched = self._fatal_re.search(combined)
            logger.debug(f"Error classified as FATAL by pattern: '{matched.group() if matched else '?'}'")
            return ErrorCategory.FATAL

        # 3. Check TRANSIENT patterns
        if self._transient_re.search(combined):
            matched = self._transient_re.search(combined)
            logger.debug(f"Error classified as TRANSIENT by pattern: '{matched.group() if matched else '?'}'")
            return ErrorCategory.TRANSIENT

        # 4. Fallback: UNKNOWN
        logger.debug(f"Error classified as UNKNOWN — no pattern matched: {error_text[:200]}")
        return ErrorCategory.UNKNOWN

    def should_retry(self, category: ErrorCategory) -> bool:
        """Whether this error category should trigger a retry."""
        if category == ErrorCategory.FATAL:
            return False
        # TRANSIENT and UNKNOWN both allow retry (UNKNOWN defers to task retry policy)
        return True

    def classify_and_decide(
        self,
        error_text: str,
        exception_name: str | None = None,
        exit_code: int | None = None,
        stderr: str | None = None,
    ) -> tuple[ErrorCategory, bool]:
        """Classify error and return (category, should_retry) tuple."""
        category = self.classify(error_text, exception_name, exit_code, stderr)
        return category, self.should_retry(category)


# Module-level singleton for convenience
_default_classifier: ErrorClassifier | None = None


def get_error_classifier() -> ErrorClassifier:
    """Get or create the module-level ErrorClassifier singleton."""
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = ErrorClassifier()
    return _default_classifier
