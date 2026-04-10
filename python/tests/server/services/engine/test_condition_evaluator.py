"""Tests for ConditionEvaluator — when expression parser for DAG nodes."""

import pytest

from src.server.services.engine.condition_evaluator import ConditionEvaluator


@pytest.fixture
def evaluator() -> ConditionEvaluator:
    return ConditionEvaluator()


class TestSimpleComparisons:
    """Test basic comparison operators."""

    def test_equal(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"plan": {"output": "APPROVED"}}
        assert evaluator.evaluate("$plan.output == 'APPROVED'", ctx) is True

    def test_not_equal(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"plan": {"output": "REJECTED"}}
        assert evaluator.evaluate("$plan.output != 'APPROVED'", ctx) is True

    def test_less_than(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"score": {"output": "75"}}
        assert evaluator.evaluate("$score.output < '80'", ctx) is True

    def test_greater_than(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"score": {"output": "95"}}
        assert evaluator.evaluate("$score.output > '80'", ctx) is True

    def test_less_equal(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"v": {"output": "80"}}
        assert evaluator.evaluate("$v.output <= '80'", ctx) is True

    def test_greater_equal(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"v": {"output": "80"}}
        assert evaluator.evaluate("$v.output >= '80'", ctx) is True

    def test_numeric_comparison_with_non_numeric(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"v": {"output": "abc"}}
        assert evaluator.evaluate("$v.output > '50'", ctx) is False  # Fail-closed


class TestDotNotation:
    """Test $nodeId.output.field access."""

    def test_json_field(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"review": {"output": {"verdict": "approve", "score": 85}}}
        assert evaluator.evaluate("$review.output.verdict == 'approve'", ctx) is True

    def test_nested_field(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"data": {"output": {"result": {"status": "ok"}}}}
        assert evaluator.evaluate("$data.output.result.status == 'ok'", ctx) is True

    def test_missing_field(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"data": {"output": {"x": 1}}}
        assert evaluator.evaluate("$data.output.y == '1'", ctx) is False

    def test_string_output_as_json(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"data": {"output": '{"status": "done"}'}}
        assert evaluator.evaluate("$data.output.status == 'done'", ctx) is True


class TestCompoundExpressions:
    """Test AND (&&) and OR (||) operators."""

    def test_and_both_true(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"a": {"output": "yes"}, "b": {"output": "yes"}}
        assert evaluator.evaluate("$a.output == 'yes' && $b.output == 'yes'", ctx) is True

    def test_and_one_false(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"a": {"output": "yes"}, "b": {"output": "no"}}
        assert evaluator.evaluate("$a.output == 'yes' && $b.output == 'yes'", ctx) is False

    def test_or_one_true(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"a": {"output": "yes"}, "b": {"output": "no"}}
        assert evaluator.evaluate("$a.output == 'yes' || $b.output == 'yes'", ctx) is True

    def test_or_both_false(self, evaluator: ConditionEvaluator) -> None:
        ctx = {"a": {"output": "no"}, "b": {"output": "no"}}
        assert evaluator.evaluate("$a.output == 'yes' || $b.output == 'yes'", ctx) is False

    def test_and_precedence_over_or(self, evaluator: ConditionEvaluator) -> None:
        # A || B && C → A || (B && C)
        ctx = {"a": {"output": "yes"}, "b": {"output": "no"}, "c": {"output": "no"}}
        # "yes == yes" || ("no == yes" && "no == yes") → True || False → True
        assert evaluator.evaluate(
            "$a.output == 'yes' || $b.output == 'yes' && $c.output == 'yes'", ctx
        ) is True


class TestFailClosed:
    """Test fail-closed behavior for unparseable expressions."""

    def test_unparseable_returns_false(self, evaluator: ConditionEvaluator) -> None:
        assert evaluator.evaluate("this is not a valid expression", {}) is False

    def test_missing_variable_returns_false(self, evaluator: ConditionEvaluator) -> None:
        assert evaluator.evaluate("$nonexistent.output == 'value'", {}) is False

    def test_empty_expression_returns_true(self, evaluator: ConditionEvaluator) -> None:
        assert evaluator.evaluate("", {}) is True

    def test_none_expression_returns_true(self, evaluator: ConditionEvaluator) -> None:
        assert evaluator.evaluate("   ", {}) is True
