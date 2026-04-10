"""
Condition Evaluator — `when` expression parser for DAG node execution.

Adopted from upstream Archon v0.3.2 condition-evaluator pattern.
Evaluates expressions like `$nodeId.output == 'VALUE'` to determine
whether a DAG node should execute.

Supports:
- Comparison: ==, !=, <, >, <=, >=
- Logical: && (AND), || (OR)  — AND has higher precedence
- Variable references: $nodeId.output, $nodeId.output.field
- String and numeric comparisons
- Fail-closed: unparseable expressions default to False (skip node)

Usage:
    evaluator = ConditionEvaluator()
    result = evaluator.evaluate(
        expression="$plan.output == 'APPROVED'",
        context={"plan": {"output": "APPROVED"}},
    )
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Variable reference pattern: $nodeId.output or $nodeId.output.field
_VAR_PATTERN = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_-]*)\.output(?:\.([a-zA-Z_][a-zA-Z0-9_.]*))?")

# Comparison operators
_COMPARISON_OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: _numeric_compare(a, b, lambda x, y: x < y),
    ">": lambda a, b: _numeric_compare(a, b, lambda x, y: x > y),
    "<=": lambda a, b: _numeric_compare(a, b, lambda x, y: x <= y),
    ">=": lambda a, b: _numeric_compare(a, b, lambda x, y: x >= y),
}

# Regex to split on comparison operators (longest first to avoid partial matches)
_OP_PATTERN = re.compile(r"\s*(==|!=|<=|>=|<|>)\s*")


def _numeric_compare(a: str, b: str, op) -> bool:
    """Attempt numeric comparison, fail-closed to False."""
    try:
        return op(float(a), float(b))
    except (ValueError, TypeError):
        return False


def _resolve_variable(var_match: re.Match, context: dict[str, Any]) -> str:
    """Resolve a $nodeId.output[.field] reference from context.

    Context format:
        {
            "nodeId": {"output": "text output"},
            "otherNode": {"output": {"field": "value", "nested": {"deep": 42}}},
        }
    """
    node_id = var_match.group(1)
    field_path = var_match.group(2)

    node_data = context.get(node_id)
    if node_data is None:
        return ""

    output = node_data.get("output", "")

    if field_path is None:
        # $nodeId.output — return the raw output as string
        if isinstance(output, dict):
            return json.dumps(output)
        return str(output)

    # $nodeId.output.field.nested — traverse dot-separated path
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return ""

    if not isinstance(output, dict):
        return ""

    parts = field_path.split(".")
    current = output
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return ""

    return str(current)


def _substitute_variables(expression: str, context: dict[str, Any]) -> str:
    """Replace all $nodeId.output[.field] references with their values."""

    def replacer(match: re.Match) -> str:
        return _resolve_variable(match, context)

    return _VAR_PATTERN.sub(replacer, expression)


def _evaluate_comparison(expression: str) -> bool | None:
    """Evaluate a single comparison expression like "value1 == value2".

    Returns None if the expression cannot be parsed (fail-closed).
    """
    match = _OP_PATTERN.search(expression)
    if not match:
        return None

    left = expression[:match.start()].strip().strip("'\"")
    op = match.group(1)
    right = expression[match.end():].strip().strip("'\"")

    op_fn = _COMPARISON_OPS.get(op)
    if not op_fn:
        return None

    return op_fn(left, right)


class ConditionEvaluator:
    """Evaluates `when` expressions for DAG node conditional execution.

    Fail-closed: any unparseable expression returns False (node is skipped).
    """

    def evaluate(
        self,
        expression: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Evaluate a when expression.

        Args:
            expression: The condition string (e.g., "$plan.output == 'APPROVED'").
            context: Node output context for variable substitution.

        Returns:
            True if the condition is met, False otherwise (fail-closed).
        """
        if not expression or not expression.strip():
            return True  # Empty condition = always execute

        ctx = context or {}

        try:
            # Substitute variables
            resolved = _substitute_variables(expression, ctx)

            # Parse compound expressions (AND/OR)
            result = self._evaluate_compound(resolved)

            if result is None:
                logger.warning(f"Condition eval fail-closed (unparseable): {expression!r}")
                return False

            return result

        except Exception as e:
            logger.warning(f"Condition eval error (fail-closed): {expression!r} — {e}")
            return False

    def _evaluate_compound(self, expression: str) -> bool | None:
        """Evaluate compound expression with && and || operators.

        AND (&&) has higher precedence than OR (||).
        """
        # Split on OR first (lower precedence)
        or_parts = self._split_respecting_quotes(expression, "||")
        if len(or_parts) > 1:
            for part in or_parts:
                result = self._evaluate_compound(part.strip())
                if result is True:
                    return True
                if result is None:
                    return None  # Fail-closed on parse error
            return False

        # Split on AND (higher precedence)
        and_parts = self._split_respecting_quotes(expression, "&&")
        if len(and_parts) > 1:
            for part in and_parts:
                result = self._evaluate_compound(part.strip())
                if result is False:
                    return False
                if result is None:
                    return None
            return True

        # Single comparison
        return _evaluate_comparison(expression)

    @staticmethod
    def _split_respecting_quotes(text: str, delimiter: str) -> list[str]:
        """Split text on delimiter, respecting quoted strings."""
        parts: list[str] = []
        current: list[str] = []
        in_quote = False
        quote_char = ""
        i = 0

        while i < len(text):
            ch = text[i]

            if ch in ("'", '"') and not in_quote:
                in_quote = True
                quote_char = ch
                current.append(ch)
            elif ch == quote_char and in_quote:
                in_quote = False
                current.append(ch)
            elif not in_quote and text[i:i + len(delimiter)] == delimiter:
                parts.append("".join(current))
                current = []
                i += len(delimiter)
                continue
            else:
                current.append(ch)
            i += 1

        parts.append("".join(current))
        return parts
