"""Tests for ErrorClassifier — FATAL vs TRANSIENT error classification."""

import pytest

from src.server.services.engine.error_classifier import (
    ErrorCategory,
    ErrorClassifier,
    get_error_classifier,
)


@pytest.fixture
def classifier() -> ErrorClassifier:
    return ErrorClassifier()


class TestErrorClassification:
    """Test that errors are correctly classified as FATAL, TRANSIENT, or UNKNOWN."""

    # ── FATAL patterns ──

    @pytest.mark.parametrize(
        "error_text",
        [
            "unauthorized access to resource",
            "403 Forbidden",
            "authentication failed for API key",
            "permission denied: /etc/secret",
            "access denied to database",
            "invalid api key provided",
            "credit balance insufficient",
            "quota exceeded for project",
            "binary not found: claude",
            "command not found: codex",
            "missing required configuration: SUPABASE_URL",
        ],
    )
    def test_fatal_patterns(self, classifier: ErrorClassifier, error_text: str) -> None:
        category = classifier.classify(error_text)
        assert category == ErrorCategory.FATAL, f"Expected FATAL for: {error_text}"

    @pytest.mark.parametrize(
        "exception_name",
        [
            "AuthenticationError",
            "PermissionError",
            "ConfigurationError",
            "EnvLeakError",
            "BudgetExceededError",
        ],
    )
    def test_fatal_exception_names(self, classifier: ErrorClassifier, exception_name: str) -> None:
        category = classifier.classify("some error", exception_name=exception_name)
        assert category == ErrorCategory.FATAL

    # ── TRANSIENT patterns ──

    @pytest.mark.parametrize(
        "error_text",
        [
            "connection timeout after 30s",
            "ECONNREFUSED on port 8181",
            "rate limit exceeded, retry after 60s",
            "429 Too Many Requests",
            "502 Bad Gateway",
            "503 Service Unavailable",
            "socket hang up during request",
            "network error: DNS resolution failed",
            "process exited with code 137",
            "API overloaded_error",
            "connection reset by peer",
            "out of memory",
        ],
    )
    def test_transient_patterns(self, classifier: ErrorClassifier, error_text: str) -> None:
        category = classifier.classify(error_text)
        assert category == ErrorCategory.TRANSIENT, f"Expected TRANSIENT for: {error_text}"

    @pytest.mark.parametrize(
        "exception_name",
        [
            "TimeoutError",
            "ConnectionError",
            "RateLimitError",
        ],
    )
    def test_transient_exception_names(self, classifier: ErrorClassifier, exception_name: str) -> None:
        category = classifier.classify("some error", exception_name=exception_name)
        assert category == ErrorCategory.TRANSIENT

    # ── UNKNOWN (no match) ──

    def test_unknown_for_generic_error(self, classifier: ErrorClassifier) -> None:
        category = classifier.classify("Task failed: could not parse JSON output")
        assert category == ErrorCategory.UNKNOWN

    def test_unknown_for_empty_string(self, classifier: ErrorClassifier) -> None:
        category = classifier.classify("")
        assert category == ErrorCategory.UNKNOWN


class TestShouldRetry:
    """Test retry decisions based on error category."""

    def test_fatal_should_not_retry(self, classifier: ErrorClassifier) -> None:
        assert classifier.should_retry(ErrorCategory.FATAL) is False

    def test_transient_should_retry(self, classifier: ErrorClassifier) -> None:
        assert classifier.should_retry(ErrorCategory.TRANSIENT) is True

    def test_unknown_should_retry(self, classifier: ErrorClassifier) -> None:
        assert classifier.should_retry(ErrorCategory.UNKNOWN) is True


class TestClassifyAndDecide:
    """Test the combined classify + decide method."""

    def test_fatal_returns_no_retry(self, classifier: ErrorClassifier) -> None:
        category, should_retry = classifier.classify_and_decide("401 unauthorized")
        assert category == ErrorCategory.FATAL
        assert should_retry is False

    def test_transient_returns_retry(self, classifier: ErrorClassifier) -> None:
        category, should_retry = classifier.classify_and_decide("connection timeout")
        assert category == ErrorCategory.TRANSIENT
        assert should_retry is True

    def test_unknown_returns_retry(self, classifier: ErrorClassifier) -> None:
        category, should_retry = classifier.classify_and_decide("unexpected error in module X")
        assert category == ErrorCategory.UNKNOWN
        assert should_retry is True


class TestStderrCombination:
    """Test that stderr is combined with error_text for classification."""

    def test_stderr_provides_fatal_signal(self, classifier: ErrorClassifier) -> None:
        category = classifier.classify(
            "task execution failed",
            stderr="Error: authentication failed for user",
        )
        assert category == ErrorCategory.FATAL

    def test_stderr_provides_transient_signal(self, classifier: ErrorClassifier) -> None:
        category = classifier.classify(
            "task execution failed",
            stderr="Error: ECONNREFUSED on localhost:8181",
        )
        assert category == ErrorCategory.TRANSIENT


class TestExceptionNamePriority:
    """Test that exception name takes priority over pattern matching."""

    def test_exception_name_overrides_text_pattern(self, classifier: ErrorClassifier) -> None:
        # Text looks transient ("timeout") but exception is FATAL
        category = classifier.classify(
            "timeout during authentication",
            exception_name="AuthenticationError",
        )
        assert category == ErrorCategory.FATAL


class TestCustomPatterns:
    """Test custom pattern extension."""

    def test_extra_fatal_pattern(self) -> None:
        classifier = ErrorClassifier(extra_fatal_patterns=["custom_fatal_error"])
        category = classifier.classify("custom_fatal_error occurred")
        assert category == ErrorCategory.FATAL

    def test_extra_transient_pattern(self) -> None:
        classifier = ErrorClassifier(extra_transient_patterns=["flaky_service_down"])
        category = classifier.classify("flaky_service_down detected")
        assert category == ErrorCategory.TRANSIENT


class TestSingleton:
    """Test module-level singleton."""

    def test_singleton_returns_same_instance(self) -> None:
        c1 = get_error_classifier()
        c2 = get_error_classifier()
        assert c1 is c2
