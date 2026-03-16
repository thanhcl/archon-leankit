"""
Engine package — orchestrates task execution lifecycle.

Modules:
- prompt_builder: Generates unified execution prompt from task + KB context
- cc_spawner: Spawns Claude Code CLI sessions in subprocess
- architect_reviewer: Hybrid reviewer (self-review / multi-provider API)
- task_engine: Main daemon loop — polls, spawns, monitors
- notifier: Pushes task lifecycle events to channels
- health_monitor: Computes metrics and fires health alerts
"""

from .architect_reviewer import ArchitectReviewer, ArchitectReviewResult, ReviewAction, ReviewConfig
from .cc_spawner import CCExecutionResult, CCSpawner, ProjectConfig
from .health_monitor import EngineMetrics, HealthAlert, HealthMonitor, HealthThresholds
from .notifier import Notifier, NotifierConfig, TaskEvent
from .prompt_builder import PromptBuilder
from .task_engine import TaskEngine

__all__ = [
    "ArchitectReviewer",
    "ArchitectReviewResult",
    "CCExecutionResult",
    "CCSpawner",
    "EngineMetrics",
    "HealthAlert",
    "HealthMonitor",
    "HealthThresholds",
    "Notifier",
    "NotifierConfig",
    "ProjectConfig",
    "PromptBuilder",
    "ReviewAction",
    "ReviewConfig",
    "TaskEngine",
    "TaskEvent",
]
