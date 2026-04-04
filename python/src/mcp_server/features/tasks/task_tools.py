"""
Consolidated task management tools for Archon MCP Server.

Supports 15-state lifecycle: draft, proposed, approved, planning, owner-qa,
assigned, executing, architect-review, code-review, review, done, failed,
escalated, on-hold, cancelled.
"""

import json
import logging
from typing import Any
from urllib.parse import urljoin

import httpx
from mcp.server.fastmcp import Context, FastMCP

from src.mcp_server.utils.error_handling import MCPErrorFormatter
from src.mcp_server.utils.timeout_config import get_default_timeout
from src.server.config.service_discovery import get_api_url

logger = logging.getLogger(__name__)

# Optimization constants
MAX_DESCRIPTION_LENGTH = 1000
DEFAULT_PAGE_SIZE = 10

VALID_STATUSES = [
    "draft", "proposed", "approved", "planning", "owner-qa",
    "assigned", "executing", "architect-review", "code-review", "review",
    "done", "failed", "escalated", "on-hold", "cancelled",
]


def truncate_text(text: str, max_length: int = MAX_DESCRIPTION_LENGTH) -> str:
    """Truncate text to maximum length with ellipsis."""
    if text and len(text) > max_length:
        return text[:max_length - 3] + "..."
    return text


def optimize_task_response(task: dict) -> dict:
    """Optimize task object for MCP response."""
    task = task.copy()

    # Truncate description if present
    if "description" in task and task["description"]:
        task["description"] = truncate_text(task["description"])

    # Truncate execution_prompt if present
    if "execution_prompt" in task and task["execution_prompt"]:
        task["execution_prompt"] = truncate_text(task["execution_prompt"])

    # Replace arrays with counts
    if "sources" in task and isinstance(task["sources"], list):
        task["sources_count"] = len(task["sources"])
        del task["sources"]

    if "code_examples" in task and isinstance(task["code_examples"], list):
        task["code_examples_count"] = len(task["code_examples"])
        del task["code_examples"]

    if "acceptance_criteria" in task and isinstance(task["acceptance_criteria"], list):
        task["acceptance_criteria_count"] = len(task["acceptance_criteria"])
        del task["acceptance_criteria"]

    # Remove large JSONB fields from list responses
    if "state_history" in task and isinstance(task["state_history"], list):
        task["transition_count"] = len(task["state_history"])
        del task["state_history"]

    if "execution_result" in task and task["execution_result"]:
        task["has_execution_result"] = True
        del task["execution_result"]

    if "architect_review" in task and task["architect_review"]:
        task["has_architect_review"] = True
        del task["architect_review"]

    return task


def register_task_tools(mcp: FastMCP):
    """Register consolidated task management tools with the MCP server."""

    @mcp.tool()
    async def find_tasks(
        ctx: Context,
        query: str | None = None,
        task_id: str | None = None,
        filter_by: str | None = None,
        filter_value: str | None = None,
        project_id: str | None = None,
        include_closed: bool = True,
        page: int = 1,
        per_page: int = DEFAULT_PAGE_SIZE,
        task_type: str | None = None,
        phase: str | None = None,
        module: str | None = None,
        sprint: str | None = None,
        feature: str | None = None,
        parent_task_id: str | None = None,
    ) -> str:
        """
        Find and search tasks (consolidated: list + search + get).

        Supports 15-state lifecycle: draft, proposed, approved, planning, owner-qa,
        assigned, executing, architect-review, code-review, review, done, failed,
        escalated, on-hold, cancelled.

        Args:
            query: Keyword search in title, description, feature (optional)
            task_id: Get specific task by ID (returns full details)
            filter_by: "status" | "project" | "assignee" (optional)
            filter_value: Filter value (e.g., "draft", "executing", "review", "done")
            project_id: Project UUID (optional, for additional filtering)
            include_closed: Include done/cancelled tasks in results
            page: Page number for pagination
            per_page: Items per page (default: 10)
            task_type: Filter by type: bug, feature, improvement, docs, refactor, test
            phase: Filter by roadmap phase: phase-1, phase-2, etc.
            module: Filter by module: engine, virtual-office, archon-api, observability, toolkit
            sprint: Filter by sprint: S1, S2, S3, etc.
            feature: Filter by feature/epic grouping
            parent_task_id: Filter subtasks of a parent task

        Returns:
            JSON array of tasks or single task (optimized payloads for lists)

        Examples:
            find_tasks() # All tasks
            find_tasks(query="auth") # Search for "auth"
            find_tasks(task_id="task-123") # Get specific task (full details)
            find_tasks(filter_by="status", filter_value="draft") # Only draft tasks
            find_tasks(filter_by="status", filter_value="executing") # Tasks being executed
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            # Single task get mode
            if task_id:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(urljoin(api_url, f"/api/tasks/{task_id}"))

                    if response.status_code == 200:
                        task = response.json()
                        return json.dumps({"success": True, "task": task})
                    elif response.status_code == 404:
                        return MCPErrorFormatter.format_error(
                            error_type="not_found",
                            message=f"Task {task_id} not found",
                            suggestion="Verify the task ID is correct",
                            http_status=404,
                        )
                    else:
                        return MCPErrorFormatter.from_http_error(response, "get task")

            # List mode with search and filters
            params: dict[str, Any] = {
                "page": page,
                "per_page": per_page,
                "exclude_large_fields": True,
            }

            if query:
                params["q"] = query
            if task_type:
                params["task_type"] = task_type
            if phase:
                params["phase"] = phase
            if module:
                params["module"] = module
            if sprint:
                params["sprint"] = sprint
            if feature:
                params["feature"] = feature
            if parent_task_id:
                params["parent_task_id"] = parent_task_id

            if filter_by == "project" and filter_value:
                url = urljoin(api_url, f"/api/projects/{filter_value}/tasks")
                params["include_archived"] = False
            elif filter_by == "status" and filter_value:
                url = urljoin(api_url, "/api/tasks")
                params["status"] = filter_value
                params["include_closed"] = include_closed
                if project_id:
                    params["project_id"] = project_id
            elif filter_by == "assignee" and filter_value:
                url = urljoin(api_url, "/api/tasks")
                params["assignee"] = filter_value
                params["include_closed"] = include_closed
                if project_id:
                    params["project_id"] = project_id
            elif project_id:
                url = urljoin(api_url, "/api/tasks")
                params["project_id"] = project_id
                params["include_closed"] = include_closed
            else:
                url = urljoin(api_url, "/api/tasks")
                params["include_closed"] = include_closed

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()

                result = response.json()

                if isinstance(result, list):
                    tasks = result
                    total_count = len(result)
                elif isinstance(result, dict):
                    if "tasks" in result:
                        tasks = result["tasks"]
                        total_count = result.get("total_count", len(tasks))
                    elif "data" in result:
                        tasks = result["data"]
                        total_count = result.get("total", len(tasks))
                    else:
                        return MCPErrorFormatter.format_error(
                            error_type="invalid_response",
                            message="Unexpected response format from API",
                            details={"response_keys": list(result.keys())},
                        )
                else:
                    return MCPErrorFormatter.format_error(
                        error_type="invalid_response",
                        message="Invalid response type from API",
                        details={"response_type": type(result).__name__},
                    )

                optimized_tasks = [optimize_task_response(task) for task in tasks]

                return json.dumps({
                    "success": True,
                    "tasks": optimized_tasks,
                    "total_count": total_count,
                    "count": len(optimized_tasks),
                    "query": query,
                })

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(
                e, "list tasks", {"filter_by": filter_by, "filter_value": filter_value}
            )
        except Exception as e:
            logger.error(f"Error listing tasks: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "list tasks")

    @mcp.tool()
    async def manage_task(
        ctx: Context,
        action: str,  # "create" | "update" | "delete"
        task_id: str | None = None,
        project_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        task_order: int | None = None,
        feature: str | None = None,
        owner: str | None = None,
        acceptance_criteria: list[dict[str, Any]] | None = None,
        execution_prompt: str | None = None,
        source_app: str | None = None,
        complexity: str | None = None,
        max_retries: int | None = None,
        blocked_by: list[str] | None = None,
        task_type: str | None = None,
        phase: str | None = None,
        module: str | None = None,
        sprint: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """
        Manage tasks (consolidated: create/update/delete).

        Supports 15-state lifecycle. New tasks default to "draft" status.

        TASK GRANULARITY GUIDANCE:
        - For feature-specific projects: Create detailed implementation tasks
        - For codebase-wide projects: Create feature-level tasks
        - Each task should represent 30 minutes to 4 hours of work

        Args:
            action: "create" | "update" | "delete"
            task_id: Task UUID for update/delete
            project_id: Project UUID for create
            title: Task title text
            description: Detailed task description with clear completion criteria
            status: Lifecycle state (default: "draft" for new tasks)
            assignee: Agent/user executing the task (default: "User")
            task_order: Priority 0-100 (higher = more priority)
            feature: Feature/epic label for grouping
            owner: Task owner (human who owns the outcome)
            acceptance_criteria: JSON array of acceptance criteria
            execution_prompt: Instructions for the executing agent
            source_app: Application that created this task
            complexity: "simple" or "complex" (affects routing after approval)
            max_retries: Maximum retry attempts (default: 3)
            blocked_by: List of task UUIDs that must be done before this task can execute
            task_type: Task type: bug, feature, improvement, docs, refactor, test
            phase: Roadmap phase: phase-1, phase-2, etc.
            module: Module/area: engine, virtual-office, archon-api, observability, toolkit
            sprint: Sprint assignment: S1, S2, S3, etc.
            tags: Controlled labels for categorization

        Examples:
          manage_task("create", project_id="p-1", title="Research patterns", complexity="simple")
          manage_task("create", project_id="p-1", title="Design API", complexity="complex", owner="User")
          manage_task("update", task_id="t-1", execution_prompt="Implement the auth middleware")
          manage_task("delete", task_id="t-1")

        Returns: {success: bool, task?: object, message: string}
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            async with httpx.AsyncClient(timeout=timeout) as client:
                if action == "create":
                    if not project_id or not title:
                        return MCPErrorFormatter.format_error(
                            "validation_error",
                            "project_id and title required for create",
                            suggestion="Provide both project_id and title"
                        )

                    create_data: dict[str, Any] = {
                        "project_id": project_id,
                        "title": title,
                        "description": description or "",
                        "assignee": assignee or "User",
                        "task_order": task_order or 0,
                        "feature": feature,
                        "sources": [],
                        "code_examples": [],
                    }

                    if owner is not None:
                        create_data["owner"] = owner
                    if acceptance_criteria is not None:
                        create_data["acceptance_criteria"] = acceptance_criteria
                    if execution_prompt is not None:
                        create_data["execution_prompt"] = execution_prompt
                    if source_app is not None:
                        create_data["source_app"] = source_app
                    if complexity is not None:
                        create_data["complexity"] = complexity
                    if max_retries is not None:
                        create_data["max_retries"] = max_retries
                    if status is not None:
                        create_data["status"] = status
                    if blocked_by is not None:
                        create_data["blocked_by"] = blocked_by
                    if task_type is not None:
                        create_data["task_type"] = task_type
                    if phase is not None:
                        create_data["phase"] = phase
                    if module is not None:
                        create_data["module"] = module
                    if sprint is not None:
                        create_data["sprint"] = sprint
                    if tags is not None:
                        create_data["tags"] = tags

                    # Auto-set ownership tracking for MCP-created tasks
                    create_data["created_by"] = "owner"
                    create_data["created_from"] = "mcp"

                    response = await client.post(
                        urljoin(api_url, "/api/tasks"),
                        json=create_data,
                    )

                    if response.status_code == 200:
                        result = response.json()
                        task = result.get("task")

                        if task:
                            task = optimize_task_response(task)

                        return json.dumps({
                            "success": True,
                            "task": task,
                            "task_id": task.get("id") if task else None,
                            "message": result.get("message", "Task created successfully"),
                        })
                    else:
                        return MCPErrorFormatter.from_http_error(response, "create task")

                elif action == "update":
                    if not task_id:
                        return MCPErrorFormatter.format_error(
                            "validation_error",
                            "task_id required for update",
                            suggestion="Provide task_id to update"
                        )

                    # Status changes must go through the lifecycle transition endpoint
                    if status is not None:
                        transition_response = await client.post(
                            urljoin(api_url, f"/api/tasks/{task_id}/transition"),
                            json={"new_status": status, "changed_by": "MCP-agent"},
                        )
                        if transition_response.status_code == 200:
                            result = transition_response.json()
                            task = result.get("task")
                            if task:
                                task = optimize_task_response(task)
                            return json.dumps({
                                "success": True,
                                "task": task,
                                "transition": result.get("transition"),
                                "message": result.get("message", "Task status updated via transition"),
                            })
                        elif transition_response.status_code == 404:
                            return MCPErrorFormatter.format_error(
                                error_type="not_found",
                                message=f"Task {task_id} not found",
                                suggestion="Verify the task ID is correct",
                                http_status=404,
                            )
                        elif transition_response.status_code == 400:
                            error_detail = transition_response.json().get("detail", "Invalid transition")
                            return MCPErrorFormatter.format_error(
                                error_type="invalid_transition",
                                message=error_detail,
                                suggestion="Use find_tasks(task_id=...) to check current status, "
                                           "then check valid transitions",
                            )
                        else:
                            return MCPErrorFormatter.from_http_error(transition_response, "transition task status")

                    update_fields: dict[str, Any] = {}
                    if title is not None:
                        update_fields["title"] = title
                    if description is not None:
                        update_fields["description"] = description
                    if assignee is not None:
                        update_fields["assignee"] = assignee
                    if task_order is not None:
                        update_fields["task_order"] = task_order
                    if feature is not None:
                        update_fields["feature"] = feature
                    if owner is not None:
                        update_fields["owner"] = owner
                    if acceptance_criteria is not None:
                        update_fields["acceptance_criteria"] = acceptance_criteria
                    if execution_prompt is not None:
                        update_fields["execution_prompt"] = execution_prompt
                    if source_app is not None:
                        update_fields["source_app"] = source_app
                    if complexity is not None:
                        update_fields["complexity"] = complexity
                    if max_retries is not None:
                        update_fields["max_retries"] = max_retries
                    if blocked_by is not None:
                        update_fields["blocked_by"] = blocked_by
                    if task_type is not None:
                        update_fields["task_type"] = task_type
                    if phase is not None:
                        update_fields["phase"] = phase
                    if module is not None:
                        update_fields["module"] = module
                    if sprint is not None:
                        update_fields["sprint"] = sprint
                    if tags is not None:
                        update_fields["tags"] = tags

                    if not update_fields:
                        return MCPErrorFormatter.format_error(
                            error_type="validation_error",
                            message="No fields to update",
                            suggestion="Provide at least one field to update",
                        )

                    response = await client.put(
                        urljoin(api_url, f"/api/tasks/{task_id}"),
                        json=update_fields
                    )

                    if response.status_code == 200:
                        result = response.json()
                        task = result.get("task")

                        if task:
                            task = optimize_task_response(task)

                        return json.dumps({
                            "success": True,
                            "task": task,
                            "message": result.get("message", "Task updated successfully"),
                        })
                    else:
                        return MCPErrorFormatter.from_http_error(response, "update task")

                elif action == "delete":
                    if not task_id:
                        return MCPErrorFormatter.format_error(
                            "validation_error",
                            "task_id required for delete",
                            suggestion="Provide task_id to delete"
                        )

                    response = await client.delete(
                        urljoin(api_url, f"/api/tasks/{task_id}")
                    )

                    if response.status_code == 200:
                        result = response.json()
                        return json.dumps({
                            "success": True,
                            "message": result.get("message", "Task deleted successfully"),
                        })
                    else:
                        return MCPErrorFormatter.from_http_error(response, "delete task")

                else:
                    return MCPErrorFormatter.format_error(
                        "invalid_action",
                        f"Unknown action: {action}",
                        suggestion="Use 'create', 'update', or 'delete'"
                    )

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(
                e, f"{action} task", {"task_id": task_id, "project_id": project_id}
            )
        except Exception as e:
            logger.error(f"Error managing task ({action}): {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, f"{action} task")

    @mcp.tool()
    async def generate_tasks(
        ctx: Context,
        feature_description: str,
        project_id: str | None = None,
        auto_create: bool = False,
    ) -> str:
        """
        Generate a task breakdown from a feature description using LLM.

        Analyzes the feature description to identify components, layers, and concerns,
        then produces structured subtasks with acceptance criteria and dependencies.

        Args:
            feature_description: The feature description text to analyze (min 10 chars).
                Be detailed — include technical context, target architecture, and constraints.
            project_id: Project UUID to associate tasks with (optional for preview, required for auto_create).
            auto_create: If True, automatically create the generated tasks in the project.
                If False (default), returns the generated tasks for review without creating them.

        Returns:
            JSON with generated tasks array. Each task includes:
            - title, description, feature, complexity, priority, task_order
            - acceptance_criteria: [{description, completed}]
            - dependencies: [prerequisite task titles]

        Examples:
            generate_tasks(feature_description="Add user authentication with JWT tokens, \
login/register endpoints, password hashing, and role-based access control")

            generate_tasks(feature_description="...", project_id="p-1", auto_create=True)
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            # Call the generate endpoint
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    urljoin(api_url, "/api/tasks/generate"),
                    json={
                        "feature_description": feature_description,
                        "project_id": project_id,
                    },
                )

                if response.status_code != 200:
                    return MCPErrorFormatter.from_http_error(response, "generate tasks")

                result = response.json()
                tasks = result.get("tasks", [])

                if not tasks:
                    return json.dumps({
                        "success": False,
                        "error": "No tasks were generated",
                    })

                # If auto_create, create each task via the API
                created_tasks = []
                create_errors = []
                if auto_create and project_id:
                    for task in tasks:
                        create_data: dict[str, Any] = {
                            "project_id": project_id,
                            "title": task["title"],
                            "description": task.get("description", ""),
                            "feature": task.get("feature"),
                            "complexity": task.get("complexity", "simple"),
                            "priority": task.get("priority", "medium"),
                            "task_order": task.get("task_order", 0),
                            "acceptance_criteria": task.get("acceptance_criteria"),
                            "status": "draft",
                            "assignee": "User",
                            "source_app": "task-generator",
                            "sources": [],
                            "code_examples": [],
                        }

                        create_response = await client.post(
                            urljoin(api_url, "/api/tasks"),
                            json=create_data,
                        )

                        if create_response.status_code == 200:
                            created = create_response.json().get("task", {})
                            created_tasks.append(optimize_task_response(created))
                        else:
                            create_errors.append({
                                "title": task["title"],
                                "error": create_response.text[:200],
                            })

                    response_data: dict[str, Any] = {
                        "success": True,
                        "created_count": len(created_tasks),
                        "tasks": created_tasks,
                        "message": f"Created {len(created_tasks)} tasks in project {project_id}",
                    }
                    if create_errors:
                        response_data["create_errors"] = create_errors
                    return json.dumps(response_data)

                # Preview mode: return generated tasks without creating
                return json.dumps({
                    "success": True,
                    "generated_count": len(tasks),
                    "tasks": tasks,
                    "message": (
                        f"Generated {len(tasks)} tasks. "
                        "Set auto_create=True with a project_id to create them."
                    ),
                    **({"validation_warnings": result["validation_warnings"]}
                       if "validation_warnings" in result else {}),
                })

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "generate tasks")
        except Exception as e:
            logger.error(f"Error generating tasks: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "generate tasks")

    @mcp.tool()
    async def transition_task(
        ctx: Context,
        task_id: str,
        new_status: str,
        changed_by: str = "mcp",
        reason: str | None = None,
    ) -> str:
        """
        Transition a task to a new lifecycle state with validation and audit trail.

        Valid lifecycle states: draft, proposed, approved, planning, owner-qa,
        assigned, executing, architect-review, code-review, review, done, failed,
        escalated, on-hold, cancelled.

        Transition rules:
        - draft → proposed, approved, cancelled
        - proposed → approved, cancelled
        - approved → planning (complex), assigned (simple), on-hold, cancelled
        - planning → owner-qa, assigned, cancelled
        - owner-qa → assigned, cancelled
        - assigned → executing, on-hold, cancelled
        - executing → architect-review, failed, on-hold, cancelled
        - architect-review → code-review, assigned (retry), escalated, on-hold, cancelled
        - code-review → review, assigned (retry), escalated, on-hold, cancelled
        - review → done, assigned (owner reject), on-hold, cancelled
        - failed → assigned (retry), escalated, on-hold, cancelled
        - escalated → assigned, on-hold, cancelled
        - on-hold → approved (resume), assigned (resume), cancelled
        - done, cancelled → terminal (no transitions)

        Some transitions require a reason (rejections, failures, escalations, holds, cancellations).

        Args:
            task_id: Task UUID to transition
            new_status: Target lifecycle state
            changed_by: Who is making this transition (default: "mcp")
            reason: Required for rejections, failures, escalations, holds, cancellations

        Examples:
            transition_task(task_id="t-1", new_status="proposed", changed_by="User")
            transition_task(task_id="t-1", new_status="approved", changed_by="Owner")
            transition_task(task_id="t-1", new_status="failed", changed_by="Agent", reason="Build failed")
            transition_task(task_id="t-1", new_status="assigned", changed_by="Architect", reason="Fix auth logic")

        Returns: {success: bool, task: object, transition: {from, to, changed_by, reason}}
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    urljoin(api_url, f"/api/tasks/{task_id}/transition"),
                    json={
                        "new_status": new_status,
                        "changed_by": changed_by,
                        "reason": reason,
                    },
                )

                if response.status_code == 200:
                    result = response.json()
                    task = result.get("task")

                    if task:
                        task = optimize_task_response(task)

                    return json.dumps({
                        "success": True,
                        "task": task,
                        "transition": result.get("transition"),
                        "message": result.get("message", "Task transitioned successfully"),
                    })
                elif response.status_code == 404:
                    return MCPErrorFormatter.format_error(
                        error_type="not_found",
                        message=f"Task {task_id} not found",
                        suggestion="Verify the task ID is correct",
                        http_status=404,
                    )
                elif response.status_code == 400:
                    error_detail = response.json().get("detail", "Invalid transition")
                    return MCPErrorFormatter.format_error(
                        error_type="invalid_transition",
                        message=error_detail,
                        suggestion="Use find_tasks(task_id=...) to check current status, "
                                   "then check valid transitions",
                    )
                else:
                    return MCPErrorFormatter.from_http_error(response, "transition task")

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(
                e, "transition task", {"task_id": task_id, "new_status": new_status}
            )
        except Exception as e:
            logger.error(f"Error transitioning task: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "transition task")

    @mcp.tool()
    async def re_plan_task(
        ctx: Context,
        task_id: str,
        updated_description: str | None = None,
        changed_by: str = "mcp",
        reason: str | None = None,
    ) -> str:
        """
        Reset a task to 'planning' state, preserving task ID, lineage, and history.

        Use when a task needs replanning due to changed requirements, failed execution,
        or new information. The optional updated_description replaces the current
        description with fresh context for the planning cycle.

        Args:
            task_id: Task UUID to re-plan
            updated_description: Optional new description to replace current one
            changed_by: Who is triggering re-plan (default: "mcp")
            reason: Optional note about why re-planning is needed

        Returns: {success: bool, task: object, transition: {from, to, action, reason}}
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            payload: dict = {"changed_by": changed_by}
            if updated_description is not None:
                payload["updated_description"] = updated_description
            if reason is not None:
                payload["reason"] = reason

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    urljoin(api_url, f"/api/tasks/{task_id}/re-plan"),
                    json=payload,
                )

                if response.status_code == 200:
                    result = response.json()
                    task = result.get("task")
                    if task:
                        task = optimize_task_response(task)
                    return json.dumps({
                        "success": True,
                        "task": task,
                        "transition": result.get("transition"),
                        "message": result.get("message", "Task re-planned successfully"),
                    })
                elif response.status_code == 404:
                    return MCPErrorFormatter.format_error(
                        error_type="not_found",
                        message=f"Task {task_id} not found",
                        suggestion="Verify the task ID is correct",
                        http_status=404,
                    )
                elif response.status_code == 400:
                    error_detail = response.json().get("detail", "Cannot re-plan task")
                    return MCPErrorFormatter.format_error(
                        error_type="invalid_operation",
                        message=error_detail,
                        suggestion="Check current task status — terminal tasks (done/cancelled) cannot be re-planned",
                    )
                else:
                    return MCPErrorFormatter.from_http_error(response, "re-plan task")

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(
                e, "re-plan task", {"task_id": task_id}
            )
        except Exception as e:
            logger.error(f"Error re-planning task: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "re-plan task")

    @mcp.tool()
    async def continue_task(
        ctx: Context,
        task_id: str,
        guidance: str,
        changed_by: str = "mcp",
    ) -> str:
        """
        Resume a paused or blocked task by providing operator guidance.

        Appends the guidance text to the task description and transitions the task to
        'assigned' so the engine can pick up execution again. Preserves task ID,
        plan_item_id, and full state history.

        Valid from: on-hold, failed, escalated, planning, owner-qa, assigned,
                    draft, proposed, approved.

        Args:
            task_id: Task UUID to continue
            guidance: Clarification or additional instructions for the agent
            changed_by: Who is providing the guidance (default: "mcp")

        Returns: {success: bool, task: object, transition: {from, to, action, guidance}}
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    urljoin(api_url, f"/api/tasks/{task_id}/continue"),
                    json={"guidance": guidance, "changed_by": changed_by},
                )

                if response.status_code == 200:
                    result = response.json()
                    task = result.get("task")
                    if task:
                        task = optimize_task_response(task)
                    return json.dumps({
                        "success": True,
                        "task": task,
                        "transition": result.get("transition"),
                        "message": result.get("message", "Task continued successfully"),
                    })
                elif response.status_code == 404:
                    return MCPErrorFormatter.format_error(
                        error_type="not_found",
                        message=f"Task {task_id} not found",
                        suggestion="Verify the task ID is correct",
                        http_status=404,
                    )
                elif response.status_code == 400:
                    error_detail = response.json().get("detail", "Cannot continue task")
                    return MCPErrorFormatter.format_error(
                        error_type="invalid_operation",
                        message=error_detail,
                        suggestion="Check current task status. Continue is valid from: "
                                   "on-hold, failed, escalated, planning, owner-qa, assigned, "
                                   "draft, proposed, approved",
                    )
                else:
                    return MCPErrorFormatter.from_http_error(response, "continue task")

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(
                e, "continue task", {"task_id": task_id}
            )
        except Exception as e:
            logger.error(f"Error continuing task: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "continue task")
