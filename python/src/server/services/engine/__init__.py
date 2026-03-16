"""
Engine package — orchestrates task execution lifecycle.

Modules:
- prompt_builder: Generates execution prompts from task + KB context
"""

from .prompt_builder import PromptBuilder

__all__ = ["PromptBuilder"]
