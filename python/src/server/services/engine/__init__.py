"""
Engine package — orchestrates task execution lifecycle.

Modules:
- prompt_builder: Generates unified execution prompt from task + KB context
- cc_spawner: Spawns Claude Code CLI sessions in subprocess
- codex_runner: Runs Codex CLI in non-interactive JSON mode
- codex_hook_publisher: Bridges Codex JSONL events into Observability
- runner_adapter: Runtime adapter boundary for Claude Code, Codex, and future runners
- sandbox_provider: Provider interface for execution environment isolation
- architect_reviewer: Hybrid reviewer (self-review / multi-provider API)
- task_engine: Main daemon loop — polls, spawns, monitors
- notifier: Pushes task lifecycle events to channels
- health_monitor: Computes metrics and fires health alerts

Package boundary rules
======================
The engine package is the task-execution runtime and must remain decoupled
from the service-layer wrappers so it can be tested and ported independently.

Forbidden imports for ALL engine modules
-----------------------------------------
- ``server.api_routes``   — HTTP request-handling layer (control-plane)
- ``mcp_server``          — MCP IDE-integration layer

Forbidden imports for core engine modules (all except ``notifier.py``)
-----------------------------------------------------------------------
- ``server.services.channels`` — Notification channel adapters

``notifier.py`` is the sole permitted bridge between engine lifecycle events
and outbound notification channels.  Every other module must stay isolated.

These rules are enforced by CI import-boundary tests in:
    python/tests/server/services/engine/test_import_boundaries.py
"""

from .architect_reviewer import ArchitectReviewer, ArchitectReviewResult, ReviewAction, ReviewConfig
from .bug_task_creator import BugTaskCreator
from .capacity_tracker import GlobalCapacityTracker, SharedAgentPool
from .cc_spawner import (
    MODEL_DEFAULT,
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
    CCExecutionResult,
    CCSpawner,
    ProjectConfig,
)
from .codex_hook_publisher import CodexHookPublisher
from .codex_runner import CODEX_DEFAULT_MODEL, CODEX_RUNNER_KEY, CodexRunnerAdapter
from .health_monitor import EngineMetrics, HealthAlert, HealthMonitor, HealthThresholds
from .learning_processor import LearningProcessor
from .notifier import Notifier, NotifierConfig, TaskEvent
from .prompt_builder import PromptBuilder
from .runner_adapter import DEFAULT_RUNNER_KEY, ClaudeCodeRunnerAdapter, ExecutionRunner
from .runner_routing import (
    RUNNER_CAPABILITY_MATRIX,
    RunnerCapability,
    RunnerSelection,
    get_runner_capabilities,
    resolve_runner_selection,
)
from .sandbox_provider import (
    GitWorktreeProvider,
    LocalDirectoryProvider,
    SandboxContext,
    SandboxProvider,
    get_provider_for_isolation,
)
from .task_engine import TaskEngine

__all__ = [
    "ArchitectReviewer",
    "ArchitectReviewResult",
    "BugTaskCreator",
    "CCExecutionResult",
    "CCSpawner",
    "CODEX_DEFAULT_MODEL",
    "CODEX_RUNNER_KEY",
    "CodexHookPublisher",
    "CodexRunnerAdapter",
    "MODEL_DEFAULT",
    "MODEL_HAIKU",
    "MODEL_OPUS",
    "MODEL_SONNET",
    "EngineMetrics",
    "HealthAlert",
    "HealthMonitor",
    "HealthThresholds",
    "GitWorktreeProvider",
    "LocalDirectoryProvider",
    "LearningProcessor",
    "Notifier",
    "NotifierConfig",
    "ProjectConfig",
    "PromptBuilder",
    "ClaudeCodeRunnerAdapter",
    "ReviewAction",
    "ReviewConfig",
    "DEFAULT_RUNNER_KEY",
    "ExecutionRunner",
    "RUNNER_CAPABILITY_MATRIX",
    "RunnerCapability",
    "RunnerSelection",
    "SandboxContext",
    "SandboxProvider",
    "GlobalCapacityTracker",
    "SharedAgentPool",
    "TaskEngine",
    "TaskEvent",
    "get_provider_for_isolation",
    "get_runner_capabilities",
    "resolve_runner_selection",
]
