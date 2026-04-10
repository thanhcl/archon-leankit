"""Tests for DAGExecutor — parallel layer-by-layer workflow execution."""

import asyncio

import pytest

from src.server.services.engine.dag_executor import (
    DAGExecutor,
    NodeStatus,
    WorkflowStatus,
)
from src.server.services.engine.workflow_schema import (
    RetryConfig,
    TriggerRule,
    WorkflowDefinition,
    WorkflowNode,
)


def _make_workflow(nodes: list[WorkflowNode], name: str = "test") -> WorkflowDefinition:
    return WorkflowDefinition(name=name, nodes=nodes)


class TestTopologicalLayers:
    """Test that nodes are correctly sorted into execution layers."""

    def test_single_node(self) -> None:
        wf = _make_workflow([WorkflowNode(id="a", prompt="A")])
        executor = DAGExecutor(wf)
        layers = executor._topological_layers()
        assert len(layers) == 1
        assert layers[0][0].id == "a"

    def test_two_independent_nodes(self) -> None:
        wf = _make_workflow([
            WorkflowNode(id="a", prompt="A"),
            WorkflowNode(id="b", prompt="B"),
        ])
        executor = DAGExecutor(wf)
        layers = executor._topological_layers()
        assert len(layers) == 1
        assert len(layers[0]) == 2  # Both in same layer (parallel)

    def test_sequential_chain(self) -> None:
        wf = _make_workflow([
            WorkflowNode(id="a", prompt="A"),
            WorkflowNode(id="b", prompt="B", depends_on=["a"]),
            WorkflowNode(id="c", prompt="C", depends_on=["b"]),
        ])
        executor = DAGExecutor(wf)
        layers = executor._topological_layers()
        assert len(layers) == 3
        assert layers[0][0].id == "a"
        assert layers[1][0].id == "b"
        assert layers[2][0].id == "c"

    def test_diamond_dag(self) -> None:
        # a → b, a → c, b+c → d
        wf = _make_workflow([
            WorkflowNode(id="a", prompt="A"),
            WorkflowNode(id="b", prompt="B", depends_on=["a"]),
            WorkflowNode(id="c", prompt="C", depends_on=["a"]),
            WorkflowNode(id="d", prompt="D", depends_on=["b", "c"]),
        ])
        executor = DAGExecutor(wf)
        layers = executor._topological_layers()
        assert len(layers) == 3
        assert layers[0][0].id == "a"
        assert {n.id for n in layers[1]} == {"b", "c"}  # Parallel
        assert layers[2][0].id == "d"


class TestExecution:
    """Test workflow execution flow."""

    @pytest.mark.asyncio
    async def test_simple_execution(self) -> None:
        wf = _make_workflow([
            WorkflowNode(id="a", prompt="A"),
            WorkflowNode(id="b", prompt="B", depends_on=["a"]),
        ])

        async def execute_fn(node, ctx):
            return f"output-{node.id}"

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert result.status == WorkflowStatus.COMPLETED
        assert result.node_results["a"].status == NodeStatus.COMPLETED
        assert result.node_results["a"].output == "output-a"
        assert result.node_results["b"].status == NodeStatus.COMPLETED
        assert result.node_results["b"].output == "output-b"

    @pytest.mark.asyncio
    async def test_parallel_execution(self) -> None:
        """Verify that independent nodes execute concurrently."""
        execution_order: list[str] = []
        start_times: dict[str, float] = {}

        async def execute_fn(node, ctx):
            import time
            start_times[node.id] = time.time()
            execution_order.append(f"start-{node.id}")
            await asyncio.sleep(0.05)
            execution_order.append(f"end-{node.id}")
            return f"output-{node.id}"

        wf = _make_workflow([
            WorkflowNode(id="a", prompt="A"),
            WorkflowNode(id="b", prompt="B"),
            WorkflowNode(id="c", prompt="C", depends_on=["a", "b"]),
        ])

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert result.status == WorkflowStatus.COMPLETED
        # a and b should start before c
        a_start_idx = execution_order.index("start-a")
        b_start_idx = execution_order.index("start-b")
        c_start_idx = execution_order.index("start-c")
        assert c_start_idx > a_start_idx
        assert c_start_idx > b_start_idx

    @pytest.mark.asyncio
    async def test_failed_node(self) -> None:
        async def execute_fn(node, ctx):
            if node.id == "b":
                raise RuntimeError("b failed")
            return f"output-{node.id}"

        wf = _make_workflow([
            WorkflowNode(id="a", prompt="A"),
            WorkflowNode(id="b", prompt="B", depends_on=["a"]),
        ])

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert result.status == WorkflowStatus.FAILED
        assert result.node_results["a"].status == NodeStatus.COMPLETED
        assert result.node_results["b"].status == NodeStatus.FAILED
        assert "b failed" in result.node_results["b"].error


class TestConditionalExecution:
    """Test when condition evaluation."""

    @pytest.mark.asyncio
    async def test_condition_met(self) -> None:
        wf = _make_workflow([
            WorkflowNode(id="check", prompt="Check"),
            WorkflowNode(
                id="deploy",
                prompt="Deploy",
                depends_on=["check"],
                when="$check.output == 'PASS'",
            ),
        ])

        async def execute_fn(node, ctx):
            if node.id == "check":
                return "PASS"
            return "deployed"

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert result.node_results["deploy"].status == NodeStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_condition_not_met(self) -> None:
        wf = _make_workflow([
            WorkflowNode(id="check", prompt="Check"),
            WorkflowNode(
                id="deploy",
                prompt="Deploy",
                depends_on=["check"],
                when="$check.output == 'PASS'",
            ),
        ])

        async def execute_fn(node, ctx):
            if node.id == "check":
                return "FAIL"
            return "deployed"

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert result.node_results["deploy"].status == NodeStatus.SKIPPED


class TestTriggerRules:
    """Test trigger rule evaluation."""

    @pytest.mark.asyncio
    async def test_all_success_with_failure(self) -> None:
        """all_success should skip downstream when a dep fails."""
        wf = _make_workflow([
            WorkflowNode(id="a", prompt="A"),
            WorkflowNode(id="b", prompt="B"),
            WorkflowNode(
                id="c",
                prompt="C",
                depends_on=["a", "b"],
                trigger_rule=TriggerRule.ALL_SUCCESS,
            ),
        ])

        async def execute_fn(node, ctx):
            if node.id == "b":
                raise RuntimeError("b failed")
            return f"output-{node.id}"

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert result.node_results["c"].status == NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_one_success_with_failure(self) -> None:
        """one_success should proceed if at least one dep succeeded."""
        wf = _make_workflow([
            WorkflowNode(id="a", prompt="A"),
            WorkflowNode(id="b", prompt="B"),
            WorkflowNode(
                id="c",
                prompt="C",
                depends_on=["a", "b"],
                trigger_rule=TriggerRule.ONE_SUCCESS,
            ),
        ])

        async def execute_fn(node, ctx):
            if node.id == "b":
                raise RuntimeError("b failed")
            return f"output-{node.id}"

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert result.node_results["c"].status == NodeStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_all_done(self) -> None:
        """all_done should proceed regardless of success/failure."""
        wf = _make_workflow([
            WorkflowNode(id="a", prompt="A"),
            WorkflowNode(
                id="report",
                prompt="Report",
                depends_on=["a"],
                trigger_rule=TriggerRule.ALL_DONE,
            ),
        ])

        async def execute_fn(node, ctx):
            if node.id == "a":
                raise RuntimeError("a failed")
            return f"output-{node.id}"

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert result.node_results["report"].status == NodeStatus.COMPLETED


class TestRetry:
    """Test per-node retry with backoff."""

    @pytest.mark.asyncio
    async def test_retry_on_failure(self) -> None:
        attempt_counts: dict[str, int] = {}

        async def execute_fn(node, ctx):
            attempt_counts[node.id] = attempt_counts.get(node.id, 0) + 1
            if attempt_counts[node.id] < 3:
                raise RuntimeError(f"Attempt {attempt_counts[node.id]} failed")
            return "success"

        wf = _make_workflow([
            WorkflowNode(
                id="flaky",
                prompt="Flaky task",
                retry=RetryConfig(max_attempts=3, delay_ms=10),  # Fast for tests
            ),
        ])

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert result.node_results["flaky"].status == NodeStatus.COMPLETED
        assert result.node_results["flaky"].retries_used == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted(self) -> None:
        async def execute_fn(node, ctx):
            raise RuntimeError("Always fails")

        wf = _make_workflow([
            WorkflowNode(
                id="broken",
                prompt="Broken",
                retry=RetryConfig(max_attempts=2, delay_ms=10),
            ),
        ])

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert result.node_results["broken"].status == NodeStatus.FAILED
        assert result.node_results["broken"].retries_used == 2


class TestCancel:
    """Test workflow cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_node(self) -> None:
        from src.server.services.engine.workflow_schema import CancelConfig

        wf = _make_workflow([
            WorkflowNode(id="check", prompt="Check"),
            WorkflowNode(
                id="abort",
                cancel=CancelConfig(message="Aborting workflow"),
                depends_on=["check"],
            ),
            WorkflowNode(id="never", prompt="Never runs", depends_on=["abort"]),
        ])

        async def execute_fn(node, ctx):
            return f"output-{node.id}"

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert result.status == WorkflowStatus.CANCELLED
        assert "never" not in result.node_results or result.node_results.get("never", None) is None


class TestEventCallbacks:
    """Test event emission during execution."""

    @pytest.mark.asyncio
    async def test_events_emitted(self) -> None:
        events: list[tuple[str, dict]] = []

        async def on_event(event_type, data):
            events.append((event_type, data))

        wf = _make_workflow([WorkflowNode(id="a", prompt="A")])

        async def execute_fn(node, ctx):
            return "done"

        executor = DAGExecutor(wf, on_event=on_event)
        await executor.execute(execute_fn)

        event_types = [e[0] for e in events]
        assert "workflow_started" in event_types
        assert "node_started" in event_types
        assert "node_completed" in event_types
        assert "workflow_completed" in event_types


class TestWorkflowResult:
    """Test WorkflowResult properties."""

    @pytest.mark.asyncio
    async def test_result_properties(self) -> None:
        wf = _make_workflow([
            WorkflowNode(id="a", prompt="A"),
            WorkflowNode(id="b", prompt="B"),
            WorkflowNode(id="c", prompt="C", depends_on=["a"], when="$a.output == 'SKIP_ME'"),
        ])

        async def execute_fn(node, ctx):
            if node.id == "b":
                raise RuntimeError("fail")
            return "ok"

        executor = DAGExecutor(wf)
        result = await executor.execute(execute_fn)

        assert "a" in result.completed_nodes
        assert "b" in result.failed_nodes
        assert "c" in result.skipped_nodes
        assert result.total_duration_seconds > 0
        assert result.layers_executed >= 1
