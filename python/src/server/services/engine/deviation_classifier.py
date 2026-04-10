"""
Deviation Classifier for LeanKit V3 Task Engine.

Classifies execution failure types to enable smarter retry strategies.
Instead of treating all failures identically, the classifier determines
the root cause category so the engine can select the appropriate
recovery strategy.

Adopted from GSD's structured deviation handling pattern:
- bug_fix: Code doesn't compile/pass tests -> retry with same model + error context
- missing_requirement: Acceptance criteria gap -> escalate model + emphasize criteria
- dependency_blocker: External blocker -> pause task and notify
- architectural_issue: Design flaw -> route to architect-review
- test_failure: Tests fail but code compiles -> retry with test focus
- boundary_violation: Edited forbidden files -> retry with reinforced boundaries

Usage:
    classifier = DeviationClassifier()
    deviation_type = classifier.classify(
        error_summary="ImportError: No module named 'xyz'",
        result_summary="FAILURE",
        review_feedback="Missing authentication middleware",
    )
"""

import re
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Canonical deviation types
DEVIATION_BUG_FIX = "bug_fix"
DEVIATION_MISSING_REQUIREMENT = "missing_requirement"
DEVIATION_DEPENDENCY_BLOCKER = "dependency_blocker"
DEVIATION_ARCHITECTURAL_ISSUE = "architectural_issue"
DEVIATION_TEST_FAILURE = "test_failure"
DEVIATION_BOUNDARY_VIOLATION = "boundary_violation"

VALID_DEVIATION_TYPES = frozenset({
    DEVIATION_BUG_FIX,
    DEVIATION_MISSING_REQUIREMENT,
    DEVIATION_DEPENDENCY_BLOCKER,
    DEVIATION_ARCHITECTURAL_ISSUE,
    DEVIATION_TEST_FAILURE,
    DEVIATION_BOUNDARY_VIOLATION,
})

# Pattern matchers for each deviation type (order matters -- first match wins)
_BOUNDARY_PATTERNS = re.compile(
    r"forbidden.path|boundary.violat|editing.boundary|outside.allowed|"
    r"file.not.in.allowed|path.scope.violat",
    re.IGNORECASE,
)

_DEPENDENCY_PATTERNS = re.compile(
    r"module.not.found|cannot.find.module|no.module.named|"
    r"package.not.found|dependency.not.found|import.error|"
    r"connection.refused|timeout|ECONNREFUSED|ETIMEDOUT|"
    r"blocked.by|waiting.for|prerequisite|"
    r"network.error|dns.resolution|service.unavailable|503",
    re.IGNORECASE,
)

_EXTERNAL_BLOCKER_PATTERNS = re.compile(
    r"connection.refused|timeout|ECONNREFUSED|service.unavailable|blocked.by",
    re.IGNORECASE,
)

_ARCHITECTURAL_PATTERNS = re.compile(
    r"architect|redesign|refactor.needed|structural.change|"
    r"wrong.approach|design.flaw|pattern.violation|"
    r"circular.dependency|coupling|separation.of.concerns|"
    r"schema.migration|breaking.change|api.contract",
    re.IGNORECASE,
)

_TEST_FAILURE_PATTERNS = re.compile(
    r"test.fail|assertion.error|expect.*received|"
    r"AssertionError|assert.*fail|test.suite.*fail|"
    r"FAIL\s+.*\.test\.|jest.*fail|pytest.*FAILED|"
    r"vitest.*fail|mocha.*fail|spec.*fail",
    re.IGNORECASE,
)

_MISSING_REQUIREMENT_PATTERNS = re.compile(
    r"acceptance.criteria|requirement.not.met|missing.*feature|"
    r"not.implement|incomplete|criteria.*unmet|"
    r"missing.*validation|missing.*error.handling|missing.*auth|"
    r"missing.*endpoint|missing.*field|not.covered|"
    r"specification.*gap|required.*missing",
    re.IGNORECASE,
)

_BUG_PATTERNS = re.compile(
    r"syntax.error|type.error|reference.error|name.error|"
    r"compile.*error|build.*fail|lint.*error|"
    r"undefined.*variable|null.*pointer|segfault|"
    r"stack.overflow|memory.error|runtime.error|"
    r"unexpected.token|parse.error|invalid.syntax",
    re.IGNORECASE,
)


class DeviationClassifier:
    """Classifies execution failures into deviation types for smart retry routing."""

    def classify(
        self,
        error_summary: str | None = None,
        result_summary: str | None = None,
        review_feedback: str | None = None,
        boundary_validation: dict[str, Any] | None = None,
    ) -> str:
        """Classify the deviation type from execution failure context.

        Args:
            error_summary: stderr/error output from the runner.
            result_summary: parsed result summary from runner output.
            review_feedback: feedback from code-review or architect-review rejection.
            boundary_validation: boundary validation result dict with 'status' key.

        Returns:
            One of the VALID_DEVIATION_TYPES strings.
        """
        # Priority 1: Explicit boundary violation from validation
        if boundary_validation:
            status = boundary_validation.get("status")
            if status in ("violation", "unavailable"):
                return DEVIATION_BOUNDARY_VIOLATION

        # Combine all text signals for pattern matching
        combined = " ".join(filter(None, [
            error_summary or "",
            result_summary or "",
            review_feedback or "",
        ]))

        if not combined.strip():
            return DEVIATION_BUG_FIX  # Safe default

        # Priority 2: Boundary patterns in text
        if _BOUNDARY_PATTERNS.search(combined):
            return DEVIATION_BOUNDARY_VIOLATION

        # Priority 3: Dependency/external blockers
        if _DEPENDENCY_PATTERNS.search(combined):
            # Distinguish true external blockers from simple import errors
            if _EXTERNAL_BLOCKER_PATTERNS.search(combined):
                return DEVIATION_DEPENDENCY_BLOCKER
            # Import/module errors are bug fixes, not blockers
            return DEVIATION_BUG_FIX

        # Priority 4: Architectural issues (usually from review feedback)
        if _ARCHITECTURAL_PATTERNS.search(combined):
            return DEVIATION_ARCHITECTURAL_ISSUE

        # Priority 5: Missing requirements (usually from review feedback)
        if _MISSING_REQUIREMENT_PATTERNS.search(combined):
            return DEVIATION_MISSING_REQUIREMENT

        # Priority 6: Test failures (code compiles but tests don't pass)
        if _TEST_FAILURE_PATTERNS.search(combined):
            return DEVIATION_TEST_FAILURE

        # Priority 7: Build/compile errors
        if _BUG_PATTERNS.search(combined):
            return DEVIATION_BUG_FIX

        # Default: treat as bug fix (safest, most common)
        return DEVIATION_BUG_FIX

    def get_retry_strategy(self, deviation_type: str) -> dict[str, Any]:
        """Get the recommended retry strategy for a deviation type.

        Returns:
            Dict with keys:
            - should_retry: bool
            - model_action: "same" | "escalate" | "architect"
            - prompt_emphasis: str | None -- section to emphasize in retry prompt
            - task_action: str | None -- "pause" | "escalate" | None
        """
        strategies = {
            DEVIATION_BUG_FIX: {
                "should_retry": True,
                "model_action": "same",
                "prompt_emphasis": "error_context",
                "task_action": None,
            },
            DEVIATION_MISSING_REQUIREMENT: {
                "should_retry": True,
                "model_action": "escalate",
                "prompt_emphasis": "acceptance_criteria",
                "task_action": None,
            },
            DEVIATION_DEPENDENCY_BLOCKER: {
                "should_retry": False,
                "model_action": "same",
                "prompt_emphasis": None,
                "task_action": "pause",
            },
            DEVIATION_ARCHITECTURAL_ISSUE: {
                "should_retry": False,
                "model_action": "architect",
                "prompt_emphasis": None,
                "task_action": "escalate",
            },
            DEVIATION_TEST_FAILURE: {
                "should_retry": True,
                "model_action": "same",
                "prompt_emphasis": "test_output",
                "task_action": None,
            },
            DEVIATION_BOUNDARY_VIOLATION: {
                "should_retry": True,
                "model_action": "same",
                "prompt_emphasis": "boundary_constraints",
                "task_action": None,
            },
        }
        return strategies.get(deviation_type, strategies[DEVIATION_BUG_FIX])
