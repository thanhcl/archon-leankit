"""Tests for WorkflowSchema — Pydantic models for YAML workflow definitions."""

import pytest
from pydantic import ValidationError

from src.server.services.engine.workflow_schema import (
    ApprovalConfig,
    LoopConfig,
    RetryConfig,
    TriggerRule,
    WorkflowDefinition,
    WorkflowNode,
)


class TestWorkflowNode:
    """Test individual node validation."""

    def test_prompt_node(self) -> None:
        node = WorkflowNode(id="plan", prompt="Create an implementation plan")
        assert node.node_type == "prompt"
        assert node.is_ai_node is True

    def test_bash_node(self) -> None:
        node = WorkflowNode(id="test", bash="npm test")
        assert node.node_type == "bash"
        assert node.is_ai_node is False

    def test_loop_node(self) -> None:
        node = WorkflowNode(
            id="refine",
            loop=LoopConfig(prompt="Refine the code", until="DONE", max_iterations=3),
        )
        assert node.node_type == "loop"
        assert node.is_ai_node is True

    def test_approval_node(self) -> None:
        node = WorkflowNode(
            id="review",
            approval=ApprovalConfig(message="Please review"),
        )
        assert node.node_type == "approval"
        assert node.is_ai_node is False

    def test_command_node(self) -> None:
        node = WorkflowNode(id="exec", command="build-feature")
        assert node.node_type == "command"

    def test_no_type_raises(self) -> None:
        with pytest.raises(ValidationError, match="must set exactly one"):
            WorkflowNode(id="empty")

    def test_multiple_types_raises(self) -> None:
        with pytest.raises(ValidationError, match="only one of"):
            WorkflowNode(id="multi", prompt="hello", bash="echo hi")

    def test_depends_on(self) -> None:
        node = WorkflowNode(id="impl", prompt="Implement", depends_on=["plan"])
        assert node.depends_on == ["plan"]

    def test_when_condition(self) -> None:
        node = WorkflowNode(id="deploy", prompt="Deploy", when="$test.output == 'PASS'")
        assert node.when == "$test.output == 'PASS'"

    def test_retry_config(self) -> None:
        node = WorkflowNode(
            id="flaky",
            prompt="Run flaky task",
            retry=RetryConfig(max_attempts=3, delay_ms=5000, on_error="all"),
        )
        assert node.retry.max_attempts == 3
        assert node.retry.delay_ms == 5000

    def test_trigger_rule(self) -> None:
        node = WorkflowNode(
            id="report",
            prompt="Report",
            depends_on=["a", "b"],
            trigger_rule=TriggerRule.ONE_SUCCESS,
        )
        assert node.trigger_rule == TriggerRule.ONE_SUCCESS


class TestWorkflowDefinition:
    """Test workflow-level validation."""

    def test_valid_workflow(self) -> None:
        wf = WorkflowDefinition(
            name="build-feature",
            nodes=[
                WorkflowNode(id="plan", prompt="Create plan"),
                WorkflowNode(id="impl", prompt="Implement", depends_on=["plan"]),
                WorkflowNode(id="test", bash="npm test", depends_on=["impl"]),
            ],
        )
        assert wf.name == "build-feature"
        assert len(wf.nodes) == 3
        assert wf.node_ids == ["plan", "impl", "test"]

    def test_root_nodes(self) -> None:
        wf = WorkflowDefinition(
            name="test",
            nodes=[
                WorkflowNode(id="a", prompt="A"),
                WorkflowNode(id="b", prompt="B"),
                WorkflowNode(id="c", prompt="C", depends_on=["a", "b"]),
            ],
        )
        roots = wf.root_nodes
        assert len(roots) == 2
        assert {r.id for r in roots} == {"a", "b"}

    def test_get_node(self) -> None:
        wf = WorkflowDefinition(
            name="test",
            nodes=[WorkflowNode(id="x", prompt="X")],
        )
        assert wf.get_node("x") is not None
        assert wf.get_node("nonexistent") is None

    def test_duplicate_ids_raises(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate node ID"):
            WorkflowDefinition(
                name="dup",
                nodes=[
                    WorkflowNode(id="a", prompt="A"),
                    WorkflowNode(id="a", prompt="A again"),
                ],
            )

    def test_invalid_dependency_raises(self) -> None:
        with pytest.raises(ValidationError, match="does not exist"):
            WorkflowDefinition(
                name="bad-dep",
                nodes=[
                    WorkflowNode(id="a", prompt="A", depends_on=["nonexistent"]),
                ],
            )

    def test_cycle_raises(self) -> None:
        with pytest.raises(ValidationError, match="cycle"):
            WorkflowDefinition(
                name="cyclic",
                nodes=[
                    WorkflowNode(id="a", prompt="A", depends_on=["b"]),
                    WorkflowNode(id="b", prompt="B", depends_on=["a"]),
                ],
            )

    def test_three_node_cycle_raises(self) -> None:
        with pytest.raises(ValidationError, match="cycle"):
            WorkflowDefinition(
                name="cyclic3",
                nodes=[
                    WorkflowNode(id="a", prompt="A", depends_on=["c"]),
                    WorkflowNode(id="b", prompt="B", depends_on=["a"]),
                    WorkflowNode(id="c", prompt="C", depends_on=["b"]),
                ],
            )


class TestYAMLCompatibility:
    """Test that workflows can be loaded from dict (simulating YAML parse)."""

    def test_from_dict(self) -> None:
        data = {
            "name": "build-feature",
            "description": "Build a new feature",
            "nodes": [
                {"id": "plan", "prompt": "Create a plan"},
                {"id": "impl", "prompt": "Implement", "depends_on": ["plan"]},
                {
                    "id": "review",
                    "approval": {"message": "Review the code"},
                    "depends_on": ["impl"],
                },
                {"id": "test", "bash": "npm test", "depends_on": ["impl"]},
                {
                    "id": "report",
                    "prompt": "Generate report",
                    "depends_on": ["review", "test"],
                    "trigger_rule": "all_success",
                },
            ],
        }
        wf = WorkflowDefinition.model_validate(data)
        assert wf.name == "build-feature"
        assert len(wf.nodes) == 5
        assert wf.nodes[2].node_type == "approval"
        assert wf.nodes[4].trigger_rule == TriggerRule.ALL_SUCCESS

    def test_loop_from_dict(self) -> None:
        data = {
            "name": "refine-loop",
            "nodes": [
                {
                    "id": "refine",
                    "loop": {
                        "prompt": "Refine code until tests pass",
                        "until": "ALL_TESTS_PASS",
                        "max_iterations": 5,
                        "fresh_context": False,
                    },
                },
            ],
        }
        wf = WorkflowDefinition.model_validate(data)
        assert wf.nodes[0].loop.until == "ALL_TESTS_PASS"
        assert wf.nodes[0].loop.max_iterations == 5
