"""
Task Generator Service

Uses LLM to analyze a feature description and auto-generate subtasks
with acceptance criteria, dependencies, and complexity estimates.

Inspired by EvoAgentX WorkFlowGenerator pattern:
- Analyze description → identify components → create task graph with dependencies.
"""

import json
from typing import Any

from ...config.logfire_config import get_logger
from ..credential_service import credential_service
from ..llm_provider_service import get_llm_client

logger = get_logger(__name__)

SYSTEM_PROMPT = """\
You are a senior technical project manager who breaks down feature descriptions \
into well-structured, implementable tasks.

Given a feature description, analyze it and produce a JSON array of task objects. \
Each task should represent 30 minutes to 4 hours of focused work.

Rules:
1. Identify distinct components, layers, and concerns (backend, frontend, tests, docs).
2. Order tasks by dependency — tasks that must come first get higher task_order values.
3. Each task MUST have clear acceptance criteria as a JSON array of objects \
   with "description" (string) and "completed" (false) keys.
4. Assign realistic complexity: "simple" for straightforward changes, "complex" for \
   multi-file or cross-layer work.
5. Assign priority: "low", "medium", "high", or "critical".
6. Group related tasks with the same "feature" label when they belong to a logical component.
7. Do NOT include project setup or environment tasks unless explicitly needed.
8. Keep titles concise (<80 chars) and descriptions actionable.

Return ONLY a JSON array (no markdown, no explanation). Each element must have:
{
  "title": "string",
  "description": "string — detailed implementation notes",
  "feature": "string — component/area label",
  "complexity": "simple" | "complex",
  "priority": "low" | "medium" | "high" | "critical",
  "task_order": integer (higher = do first, range 10-100, step by 10),
  "acceptance_criteria": [{"description": "string", "completed": false}],
  "dependencies": ["title of prerequisite task"] // empty if none
}
"""


async def _get_model_for_generation() -> str:
    """Get the configured LLM model for task generation."""
    try:
        provider_config = await credential_service.get_active_provider("llm")
        model = provider_config.get("chat_model")
        if not model or model.strip() == "":
            provider = provider_config.get("provider", "openai")
            defaults = {
                "openai": "gpt-4o-mini",
                "openrouter": "anthropic/claude-3.5-sonnet",
                "google": "gemini-1.5-flash",
                "ollama": "llama3.2:latest",
                "anthropic": "claude-3-5-haiku-20241022",
                "grok": "grok-3-mini",
            }
            model = defaults.get(provider, "gpt-4o-mini")
        return model
    except Exception as e:
        logger.warning(f"Error getting model for task generation: {e}, using default")
        return "gpt-4o-mini"


def _parse_llm_response(raw: str) -> list[dict[str, Any]]:
    """Parse LLM response into a list of task dicts, handling markdown fences."""
    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = text.index("\n")
        text = text[first_newline + 1:]
        # Remove closing fence
        if text.endswith("```"):
            text = text[:-3].rstrip()

    tasks = json.loads(text)
    if not isinstance(tasks, list):
        raise ValueError("LLM response is not a JSON array")
    return tasks


def _validate_generated_task(task: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a single generated task dict."""
    if not isinstance(task, dict):
        raise ValueError(f"Task must be a dict, got {type(task).__name__}")

    title = task.get("title")
    if not title or not isinstance(title, str) or len(title.strip()) == 0:
        raise ValueError("Task must have a non-empty title")

    valid_complexities = {"simple", "complex"}
    valid_priorities = {"low", "medium", "high", "critical"}

    complexity = task.get("complexity", "simple")
    if complexity not in valid_complexities:
        complexity = "simple"

    priority = task.get("priority", "medium")
    if priority not in valid_priorities:
        priority = "medium"

    task_order = task.get("task_order", 0)
    if not isinstance(task_order, int) or task_order < 0:
        task_order = 0

    # Normalize acceptance criteria
    ac = task.get("acceptance_criteria")
    if ac and isinstance(ac, list):
        normalized_ac = []
        for item in ac:
            if isinstance(item, dict) and "description" in item:
                normalized_ac.append({
                    "description": str(item["description"]),
                    "completed": False,
                })
            elif isinstance(item, str):
                normalized_ac.append({"description": item, "completed": False})
        ac = normalized_ac if normalized_ac else None
    else:
        ac = None

    return {
        "title": title.strip()[:200],
        "description": str(task.get("description", "")).strip(),
        "feature": str(task.get("feature", "")).strip() or None,
        "complexity": complexity,
        "priority": priority,
        "task_order": task_order,
        "acceptance_criteria": ac,
        "dependencies": task.get("dependencies", []),
    }


async def generate_tasks_from_description(
    feature_description: str,
    project_id: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """
    Use LLM to analyze a feature description and generate subtasks.

    Args:
        feature_description: The feature description text to analyze.
        project_id: Optional project ID (included in output for convenience).

    Returns:
        Tuple of (success, result_dict).
        On success: {"tasks": [...], "count": int, "project_id": str|None}
        On failure: {"error": str}
    """
    if not feature_description or len(feature_description.strip()) < 10:
        return False, {"error": "Feature description must be at least 10 characters"}

    try:
        model = await _get_model_for_generation()

        async with get_llm_client() as client:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": feature_description},
                ],
                temperature=0.3,
                max_tokens=4096,
            )

        raw_content = response.choices[0].message.content
        if not raw_content:
            return False, {"error": "LLM returned empty response"}

        raw_tasks = _parse_llm_response(raw_content)

        validated_tasks = []
        errors = []
        for i, raw_task in enumerate(raw_tasks):
            try:
                validated = _validate_generated_task(raw_task)
                if project_id:
                    validated["project_id"] = project_id
                validated_tasks.append(validated)
            except ValueError as e:
                errors.append(f"Task {i}: {e}")

        if not validated_tasks:
            error_detail = "; ".join(errors) if errors else "No valid tasks generated"
            return False, {"error": f"Failed to generate valid tasks: {error_detail}"}

        logger.info(
            f"Generated {len(validated_tasks)} tasks from feature description "
            f"({len(errors)} validation errors)"
        )

        result: dict[str, Any] = {
            "tasks": validated_tasks,
            "count": len(validated_tasks),
        }
        if project_id:
            result["project_id"] = project_id
        if errors:
            result["validation_warnings"] = errors

        return True, result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}")
        return False, {"error": f"LLM returned invalid JSON: {e}"}
    except Exception as e:
        logger.error(f"Task generation failed: {e}", exc_info=True)
        return False, {"error": f"Task generation failed: {str(e)}"}
