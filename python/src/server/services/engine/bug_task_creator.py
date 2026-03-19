"""
Bug Task Creator — auto-creates bug tasks from architect review findings.

Severity routing:
  - critical → approved bug task, high priority
  - warning  → draft bug task, medium priority (Owner decides)
  - suggestion → log only, no task created

Each bug task references the parent task and is blocked_by it.
"""

from typing import Any

from ...config.logfire_config import get_logger
from ..projects.task_service import TaskService

logger = get_logger(__name__)


class BugTaskCreator:
    """Creates bug tasks from code review findings based on severity."""

    def __init__(self, task_service: TaskService | None = None) -> None:
        self.task_service = task_service or TaskService()

    async def create_bug_tasks_from_findings(
        self,
        parent_task: dict[str, Any],
        findings: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Process review findings and create bug tasks for critical/warning severity.

        Args:
            parent_task: The task that was reviewed (must have id, project_id, title).
            findings: List of finding dicts with severity, category, description.

        Returns:
            List of created bug task dicts.
        """
        if not findings:
            return []

        parent_id = parent_task.get("id", "")
        project_id = parent_task.get("project_id", "")
        parent_title = parent_task.get("title", "Unknown task")

        if not parent_id or not project_id:
            logger.error("Cannot create bug tasks: parent task missing id or project_id")
            return []

        created_tasks: list[dict[str, Any]] = []
        suggestion_count = 0

        for finding in findings:
            severity = finding.get("severity", "").lower()
            category = finding.get("category", "unknown")
            description = finding.get("description", "No description")
            file_info = finding.get("file", "")
            line_info = finding.get("line", "")

            if severity == "suggestion":
                suggestion_count += 1
                continue

            if severity not in ("critical", "warning"):
                logger.warning(
                    f"Unknown finding severity '{severity}' — skipping | "
                    f"parent_task={parent_id}"
                )
                continue

            bug_task = await self._create_bug_task(
                project_id=project_id,
                parent_id=parent_id,
                parent_title=parent_title,
                severity=severity,
                category=category,
                description=description,
                file_info=file_info,
                line_info=line_info,
            )
            if bug_task:
                created_tasks.append(bug_task)

        if suggestion_count > 0:
            logger.info(
                f"Skipped {suggestion_count} suggestion-level findings | "
                f"parent_task={parent_id}"
            )

        if created_tasks:
            logger.info(
                f"Created {len(created_tasks)} bug tasks from review | "
                f"parent_task={parent_id}"
            )

        return created_tasks

    async def _create_bug_task(
        self,
        project_id: str,
        parent_id: str,
        parent_title: str,
        severity: str,
        category: str,
        description: str,
        file_info: str,
        line_info: str,
    ) -> dict[str, Any] | None:
        """Create a single bug task for a finding."""
        is_critical = severity == "critical"

        title = f"[Bug/{severity.upper()}] {category}: {_truncate(description, 80)}"

        body_parts = [
            f"**Source:** Auto-created from code review of task `{parent_id}`",
            f"**Parent Task:** {parent_title}",
            f"**Severity:** {severity}",
            f"**Category:** {category}",
            "",
            "## Finding",
            description,
        ]
        if file_info:
            location = f"**File:** `{file_info}`"
            if line_info:
                location += f" (line {line_info})"
            body_parts.insert(5, location)

        task_description = "\n".join(body_parts)

        status = "approved" if is_critical else "draft"
        priority = "high" if is_critical else "medium"
        source_app = "architect-review"

        ok, result = await self.task_service.create_task(
            project_id=project_id,
            title=title,
            description=task_description,
            assignee="User",
            priority=priority,
            status=status,
            blocked_by=[parent_id],
            source_app=source_app,
            created_by="code-review",
            created_from="auto",
        )

        if not ok:
            logger.error(
                f"Failed to create bug task | parent={parent_id} | "
                f"severity={severity} | error={result.get('error', 'unknown')}"
            )
            return None

        bug_task = result.get("task", {})
        logger.info(
            f"Bug task created | id={bug_task.get('id')} | severity={severity} | "
            f"status={status} | parent={parent_id}"
        )
        return bug_task


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, adding ellipsis if needed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
