"""
Workflow Schema — Pydantic models for YAML workflow definitions.

Adopted from upstream Archon v0.3.2 DAG workflow schema (Zod → Pydantic).
Defines the structure of YAML workflow files with discriminated union
node types and validation.

Supported node types:
- PromptNode: Inline AI prompt execution
- CommandNode: Execute a command file (.md)
- BashNode: Shell script execution
- LoopNode: Iterative AI execution with completion signal
- ApprovalNode: Human gate with rejection retry
- CancelNode: Workflow termination

Usage:
    from .workflow_schema import WorkflowDefinition
    workflow = WorkflowDefinition.model_validate(yaml_data)
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class TriggerRule(str, Enum):
    """When to trigger a node based on predecessor outcomes."""

    ALL_SUCCESS = "all_success"
    ONE_SUCCESS = "one_success"
    NONE_FAILED_MIN_ONE_SUCCESS = "none_failed_min_one_success"
    ALL_DONE = "all_done"


class ContextMode(str, Enum):
    """Session context management for node execution."""

    FRESH = "fresh"
    SHARED = "shared"


class RetryConfig(BaseModel):
    """Per-node retry configuration."""

    max_attempts: int = Field(default=2, ge=1, le=5)
    delay_ms: int = Field(default=3000, ge=0)
    on_error: Literal["transient", "all"] = "transient"


class HookMatcher(BaseModel):
    """Per-node hook configuration for tool interception."""

    matcher: str = Field(description="Regex pattern for tool name matching")
    response: dict[str, Any] = Field(default_factory=dict)
    timeout: int = Field(default=30, ge=1)


class NodeHooks(BaseModel):
    """Hooks that fire before/after tool execution within a node."""

    PreToolUse: list[HookMatcher] = Field(default_factory=list)
    PostToolUse: list[HookMatcher] = Field(default_factory=list)


class OnRejectConfig(BaseModel):
    """Configuration for handling approval rejections."""

    prompt: str = Field(description="Prompt to execute with $REJECTION_REASON")
    max_attempts: int = Field(default=3, ge=1, le=10)


class LoopConfig(BaseModel):
    """Configuration for loop node execution."""

    prompt: str = Field(description="AI prompt for each iteration")
    until: str = Field(default="COMPLETE", description="Completion signal to detect")
    until_bash: str | None = Field(default=None, description="Bash script for completion check")
    max_iterations: int = Field(default=5, ge=1, le=50)
    fresh_context: bool = Field(default=False, description="Start fresh session each iteration")
    interactive: bool = Field(default=False, description="Pause between iterations")
    gate_message: str | None = Field(default=None, description="Message for interactive gate")


class ApprovalConfig(BaseModel):
    """Configuration for approval gate nodes."""

    message: str = Field(description="Message shown to reviewer")
    capture_response: bool = Field(default=True)
    on_reject: OnRejectConfig | None = Field(default=None)


class CancelConfig(BaseModel):
    """Configuration for cancel nodes."""

    message: str = Field(default="Workflow cancelled")


# ── Base node with common fields ──


class WorkflowNode(BaseModel):
    """A single node in a DAG workflow.

    Exactly one of: prompt, command, bash, loop, approval, cancel must be set.
    """

    id: str = Field(description="Unique node identifier within the workflow")
    depends_on: list[str] = Field(default_factory=list, description="Node IDs that must complete first")
    when: str | None = Field(default=None, description="Conditional expression for execution")
    trigger_rule: TriggerRule = Field(default=TriggerRule.ALL_SUCCESS)
    context: ContextMode | None = Field(default=None, description="Session context mode")
    retry: RetryConfig | None = Field(default=None)
    hooks: NodeHooks | None = Field(default=None)
    allowed_tools: list[str] | None = Field(default=None, description="Whitelist of allowed tools")
    denied_tools: list[str] | None = Field(default=None, description="Blacklist of denied tools")

    # Mutually exclusive node types — exactly one must be set
    prompt: str | None = Field(default=None, description="Inline AI prompt")
    command: str | None = Field(default=None, description="Command file name from .archon/commands/")
    bash: str | None = Field(default=None, description="Shell script to execute")
    loop: LoopConfig | None = Field(default=None, description="Loop execution config")
    approval: ApprovalConfig | None = Field(default=None, description="Approval gate config")
    cancel: CancelConfig | None = Field(default=None, description="Cancel workflow config")

    @model_validator(mode="after")
    def validate_exactly_one_type(self) -> "WorkflowNode":
        """Ensure exactly one node type is set (mutual exclusivity)."""
        type_fields = [
            self.prompt is not None,
            self.command is not None,
            self.bash is not None,
            self.loop is not None,
            self.approval is not None,
            self.cancel is not None,
        ]
        set_count = sum(type_fields)
        if set_count == 0:
            raise ValueError(
                f"Node '{self.id}': must set exactly one of: prompt, command, bash, loop, approval, cancel"
            )
        if set_count > 1:
            raise ValueError(
                f"Node '{self.id}': only one of prompt, command, bash, loop, approval, cancel can be set"
            )
        return self

    @property
    def node_type(self) -> str:
        """Return the active node type as a string."""
        if self.prompt is not None:
            return "prompt"
        if self.command is not None:
            return "command"
        if self.bash is not None:
            return "bash"
        if self.loop is not None:
            return "loop"
        if self.approval is not None:
            return "approval"
        if self.cancel is not None:
            return "cancel"
        return "unknown"

    @property
    def is_ai_node(self) -> bool:
        """Whether this node runs AI (prompt, command, loop)."""
        return self.node_type in {"prompt", "command", "loop"}


class WorkflowDefinition(BaseModel):
    """A complete DAG workflow definition (loaded from YAML)."""

    name: str = Field(description="Workflow name (unique identifier)")
    description: str = Field(default="", description="Human-readable description")
    nodes: list[WorkflowNode] = Field(description="Ordered list of workflow nodes")
    version: str = Field(default="1.0")

    @model_validator(mode="after")
    def validate_dag_structure(self) -> "WorkflowDefinition":
        """Validate DAG structure: unique IDs, valid dependencies, no cycles."""
        node_ids = {node.id for node in self.nodes}

        # Check unique IDs
        if len(node_ids) != len(self.nodes):
            seen: set[str] = set()
            for node in self.nodes:
                if node.id in seen:
                    raise ValueError(f"Duplicate node ID: '{node.id}'")
                seen.add(node.id)

        # Check dependency references
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in node_ids:
                    raise ValueError(
                        f"Node '{node.id}' depends on '{dep}' which does not exist"
                    )

        # Check for cycles using DFS
        if self._has_cycle():
            raise ValueError("Workflow contains a dependency cycle")

        return self

    def _has_cycle(self) -> bool:
        """Detect cycles in the DAG using DFS."""
        adj: dict[str, list[str]] = {node.id: node.depends_on for node in self.nodes}
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {nid: WHITE for nid in adj}

        def dfs(node_id: str) -> bool:
            color[node_id] = GRAY
            for dep in adj.get(node_id, []):
                if color.get(dep) == GRAY:
                    return True  # Back edge = cycle
                if color.get(dep) == WHITE and dfs(dep):
                    return True
            color[node_id] = BLACK
            return False

        return any(dfs(nid) for nid, c in color.items() if c == WHITE)

    def get_node(self, node_id: str) -> WorkflowNode | None:
        """Get a node by ID."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    @property
    def node_ids(self) -> list[str]:
        """All node IDs in definition order."""
        return [node.id for node in self.nodes]

    @property
    def root_nodes(self) -> list[WorkflowNode]:
        """Nodes with no dependencies (entry points)."""
        return [node for node in self.nodes if not node.depends_on]
