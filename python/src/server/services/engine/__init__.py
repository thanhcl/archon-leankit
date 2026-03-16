"""
Engine package — orchestrates task execution lifecycle.

Modules:
- prompt_builder: Generates execution prompts from task + KB context
- cc_spawner: Spawns Claude Code CLI sessions in subprocess
- task_engine: Main daemon loop — polls, spawns, monitors
"""

from .architect_reviewer import ArchitectReviewer, ArchitectReviewResult, ReviewAction, ReviewConfig
from .cc_spawner import CCExecutionResult, CCSpawner, ProjectConfig
from .prompt_builder import PromptBuilder
from .task_engine import TaskEngine

__all__ = [
    "ArchitectReviewer",
    "ArchitectReviewResult",
    "CCExecutionResult",
    "CCSpawner",
    "ProjectConfig",
    "PromptBuilder",
    "ReviewAction",
    "ReviewConfig",
    "TaskEngine",
]
