"""
DAG Executor — Parallel layer-by-layer workflow execution engine.

Adopted from upstream Archon v0.3.2 dag-executor pattern.
Executes a WorkflowDefinition by topologically sorting nodes into layers,
running independent nodes within each layer concurrently via asyncio.gather.

Features:
- Topological sort into execution layers
- Parallel execution of independent nodes per layer
- Variable substitution: $nodeId.output, $nodeId.output.field
- Conditional execution via `when` expressions (fail-closed)
- Trigger rules: all_success, one_success, none_failed_min_one_success, all_done
- Retry with exponential backoff per node
- Event callbacks for monitoring/SSE integration

Usage:
    executor = DAGExecutor(workflow_def)
    result = await executor.execute(execute_fn=my_node_executor)
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from ...config.logfire_config import get_logger
from .condition_evaluator import ConditionEvaluator
from .workflow_schema import TriggerRule, WorkflowDefinition, WorkflowNode

logger = get_logger(__name__)


class NodeStatus(str, Enum):
    """Execution status of a single DAG node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class WorkflowStatus(str, Enum):
    """Overall workflow execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class NodeResult:
    """Result of executing a single node."""

    node_id: str
    status: NodeStatus
    output: Any = None
    error: str = ""
    duration_seconds: float = 0.0
    retries_used: int = 0


@dataclass
class WorkflowResult:
    """Result of executing the entire workflow."""

    workflow_name: str
    status: WorkflowStatus
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    total_duration_seconds: float = 0.0
    layers_executed: int = 0

    @property
    def failed_nodes(self) -> list[str]:
        return [nid for nid, r in self.node_results.items() if r.status == NodeStatus.FAILED]

    @property
    def completed_nodes(self) -> list[str]:
        return [nid for nid, r in self.node_results.items() if r.status == NodeStatus.COMPLETED]

    @property
    def skipped_nodes(self) -> list[str]:
        return [nid for nid, r in self.node_results.items() if r.status == NodeStatus.SKIPPED]


# Type for the node execution function provided by the caller
NodeExecuteFn = Callable[[WorkflowNode, dict[str, Any]], Awaitable[Any]]

# Type for event callback: (event_type, data) -> None
EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]] | None

# Variable substitution pattern: $nodeId.output or $nodeId.output.field
_VAR_PATTERN = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_-]*)\.output(?:\.([a-zA-Z_][a-zA-Z0-9_.]*))?")


class DAGExecutor:
    """Executes a DAG workflow with parallel layer-by-layer traversal.

    Nodes are topologically sorted into layers. Within each layer,
    all nodes execute concurrently. Nodes in later layers depend on
    nodes in earlier layers.
    """

    def __init__(
        self,
        workflow: WorkflowDefinition,
        on_event: EventCallback = None,
    ) -> None:
        self.workflow = workflow
        self._on_event = on_event
        self._condition_evaluator = ConditionEvaluator()
        self._node_map: dict[str, WorkflowNode] = {n.id: n for n in workflow.nodes}
        self._node_results: dict[str, NodeResult] = {}
        self._output_context: dict[str, dict[str, Any]] = {}
        self._cancelled = False

    async def execute(
        self,
        execute_fn: NodeExecuteFn,
        initial_context: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute the workflow DAG.

        Args:
            execute_fn: Async function that executes a single node.
                Signature: async def execute(node, context) -> output
                The output is stored and available to downstream nodes.
            initial_context: Optional initial variable context.

        Returns:
            WorkflowResult with per-node results and overall status.
        """
        start_time = time.time()
        self._output_context = dict(initial_context or {})

        await self._emit("workflow_started", {
            "workflow": self.workflow.name,
            "node_count": len(self.workflow.nodes),
        })

        layers = self._topological_layers()
        result = WorkflowResult(workflow_name=self.workflow.name, status=WorkflowStatus.RUNNING)

        try:
            for layer_idx, layer in enumerate(layers):
                if self._cancelled:
                    result.status = WorkflowStatus.CANCELLED
                    break

                await self._emit("layer_started", {
                    "layer": layer_idx,
                    "nodes": [n.id for n in layer],
                })

                # Execute all nodes in this layer concurrently
                layer_results = await asyncio.gather(
                    *(self._execute_node(node, execute_fn) for node in layer),
                    return_exceptions=True,
                )

                # Process results
                for node, node_result in zip(layer, layer_results):
                    if isinstance(node_result, Exception):
                        nr = NodeResult(
                            node_id=node.id,
                            status=NodeStatus.FAILED,
                            error=str(node_result),
                        )
                    else:
                        nr = node_result

                    self._node_results[node.id] = nr
                    result.node_results[node.id] = nr

                    if nr.status == NodeStatus.COMPLETED and nr.output is not None:
                        self._output_context[node.id] = {"output": nr.output}

                result.layers_executed = layer_idx + 1

                # Check if any critical node failed
                layer_failures = [
                    nid for nid in [n.id for n in layer]
                    if self._node_results.get(nid, NodeResult(node_id=nid, status=NodeStatus.PENDING)).status == NodeStatus.FAILED
                ]
                if layer_failures:
                    # Check if downstream nodes can still proceed via trigger rules
                    has_downstream = any(
                        dep in layer_failures
                        for n in self.workflow.nodes
                        for dep in n.depends_on
                        if n.id not in self._node_results
                    )
                    if has_downstream:
                        logger.warning(
                            f"Layer {layer_idx} has failures: {layer_failures}. "
                            "Downstream nodes will evaluate trigger rules."
                        )

            # Determine overall status
            if self._cancelled:
                result.status = WorkflowStatus.CANCELLED
            elif any(r.status == NodeStatus.FAILED for r in result.node_results.values()):
                result.status = WorkflowStatus.FAILED
            else:
                result.status = WorkflowStatus.COMPLETED

        except Exception as e:
            result.status = WorkflowStatus.FAILED
            logger.error(f"Workflow execution error: {e}", exc_info=True)

        result.total_duration_seconds = time.time() - start_time

        await self._emit("workflow_completed", {
            "workflow": self.workflow.name,
            "status": result.status.value,
            "duration": round(result.total_duration_seconds, 2),
            "completed": len(result.completed_nodes),
            "failed": len(result.failed_nodes),
            "skipped": len(result.skipped_nodes),
        })

        return result

    async def _execute_node(
        self,
        node: WorkflowNode,
        execute_fn: NodeExecuteFn,
    ) -> NodeResult:
        """Execute a single node with condition checking, trigger rules, and retry."""
        node_id = node.id
        start_time = time.time()

        # Check trigger rule against predecessor outcomes
        if not self._check_trigger_rule(node):
            logger.info(f"Node '{node_id}' skipped — trigger rule not met")
            await self._emit("node_skipped", {"node_id": node_id, "reason": "trigger_rule"})
            return NodeResult(node_id=node_id, status=NodeStatus.SKIPPED)

        # Evaluate `when` condition
        if node.when:
            condition_met = self._condition_evaluator.evaluate(node.when, self._output_context)
            if not condition_met:
                logger.info(f"Node '{node_id}' skipped — condition not met: {node.when}")
                await self._emit("node_skipped", {"node_id": node_id, "reason": "condition", "when": node.when})
                return NodeResult(node_id=node_id, status=NodeStatus.SKIPPED)

        # Handle cancel nodes
        if node.cancel is not None:
            self._cancelled = True
            msg = node.cancel.message
            logger.info(f"Node '{node_id}' triggered cancellation: {msg}")
            await self._emit("workflow_cancelled", {"node_id": node_id, "message": msg})
            return NodeResult(node_id=node_id, status=NodeStatus.COMPLETED, output=msg)

        # Execute with retry
        max_attempts = 1
        delay_ms = 3000
        if node.retry:
            max_attempts = node.retry.max_attempts + 1  # +1 because first attempt is not a retry
            delay_ms = node.retry.delay_ms

        last_error = ""
        for attempt in range(max_attempts):
            if attempt > 0:
                backoff = (delay_ms / 1000.0) * (2 ** (attempt - 1))
                logger.info(f"Node '{node_id}' retry {attempt}/{max_attempts - 1} after {backoff:.1f}s")
                await asyncio.sleep(backoff)

            try:
                await self._emit("node_started", {"node_id": node_id, "attempt": attempt + 1})

                # Substitute variables in prompt/bash content
                context_for_node = dict(self._output_context)
                output = await execute_fn(node, context_for_node)

                duration = time.time() - start_time
                await self._emit("node_completed", {
                    "node_id": node_id,
                    "duration": round(duration, 2),
                    "attempt": attempt + 1,
                })

                return NodeResult(
                    node_id=node_id,
                    status=NodeStatus.COMPLETED,
                    output=output,
                    duration_seconds=duration,
                    retries_used=attempt,
                )

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Node '{node_id}' attempt {attempt + 1} failed: {last_error[:200]}"
                )

        # All attempts exhausted
        duration = time.time() - start_time
        await self._emit("node_failed", {
            "node_id": node_id,
            "error": last_error[:300],
            "attempts": max_attempts,
        })

        return NodeResult(
            node_id=node_id,
            status=NodeStatus.FAILED,
            error=last_error,
            duration_seconds=duration,
            retries_used=max_attempts - 1,
        )

    def _check_trigger_rule(self, node: WorkflowNode) -> bool:
        """Check if predecessor outcomes satisfy the node's trigger rule."""
        if not node.depends_on:
            return True

        dep_statuses = []
        for dep_id in node.depends_on:
            result = self._node_results.get(dep_id)
            if result is None:
                # Dependency hasn't executed yet — should not happen in correct layer ordering
                return False
            dep_statuses.append(result.status)

        rule = node.trigger_rule
        successes = sum(1 for s in dep_statuses if s == NodeStatus.COMPLETED)
        failures = sum(1 for s in dep_statuses if s == NodeStatus.FAILED)
        all_done = all(s not in (NodeStatus.PENDING, NodeStatus.RUNNING) for s in dep_statuses)

        if rule == TriggerRule.ALL_SUCCESS:
            return successes == len(dep_statuses)
        elif rule == TriggerRule.ONE_SUCCESS:
            return successes >= 1
        elif rule == TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS:
            return failures == 0 and successes >= 1
        elif rule == TriggerRule.ALL_DONE:
            return all_done
        else:
            return successes == len(dep_statuses)  # Default: all_success

    def _topological_layers(self) -> list[list[WorkflowNode]]:
        """Sort nodes into layers using Kahn's algorithm.

        Nodes with no unresolved dependencies form a layer.
        All nodes in the same layer can execute in parallel.
        """
        in_degree: dict[str, int] = {n.id: 0 for n in self.workflow.nodes}
        dependents: dict[str, list[str]] = {n.id: [] for n in self.workflow.nodes}

        for node in self.workflow.nodes:
            for dep in node.depends_on:
                in_degree[node.id] += 1
                dependents[dep].append(node.id)

        layers: list[list[WorkflowNode]] = []
        ready = [nid for nid, deg in in_degree.items() if deg == 0]

        while ready:
            layer = [self._node_map[nid] for nid in sorted(ready)]
            layers.append(layer)

            next_ready: list[str] = []
            for node in layer:
                for dep_id in dependents[node.id]:
                    in_degree[dep_id] -= 1
                    if in_degree[dep_id] == 0:
                        next_ready.append(dep_id)

            ready = next_ready

        # Verify all nodes were placed
        placed = sum(len(layer) for layer in layers)
        if placed != len(self.workflow.nodes):
            logger.error(
                f"Topological sort incomplete: placed {placed}/{len(self.workflow.nodes)} nodes. "
                "Possible cycle in DAG."
            )

        return layers

    def cancel(self) -> None:
        """Cancel the workflow execution."""
        self._cancelled = True

    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit an event via the callback (if registered)."""
        if self._on_event:
            try:
                await self._on_event(event_type, data)
            except Exception as e:
                logger.debug(f"Event callback error (non-fatal): {e}")
