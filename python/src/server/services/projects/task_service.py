"""
Task Service Module for Archon

This module provides core business logic for task operations that can be
shared between MCP tools and FastAPI endpoints.
"""

# Removed direct logging import - using unified config
from datetime import datetime
from typing import Any

from src.server.models.api_contracts import TaskComplexity, TaskPriority, TaskStatus, TaskType
from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Task updates are handled via polling - no broadcasting needed


class TaskService:
    """Service class for task operations"""

    VALID_STATUSES = [s.value for s in TaskStatus]

    def __init__(self, supabase_client=None):
        """Initialize with optional supabase client"""
        self.supabase_client = supabase_client or get_supabase_client()

    def validate_status(self, status: str) -> tuple[bool, str]:
        """Validate task status"""
        if status not in self.VALID_STATUSES:
            return (
                False,
                f"Invalid status '{status}'. Must be one of: {', '.join(self.VALID_STATUSES)}",
            )
        return True, ""

    def validate_assignee(self, assignee: str) -> tuple[bool, str]:
        """Validate task assignee"""
        if not assignee or not isinstance(assignee, str) or len(assignee.strip()) == 0:
            return False, "Assignee must be a non-empty string"
        return True, ""

    def validate_priority(self, priority: str) -> tuple[bool, str]:
        """Validate task priority against allowed enum values"""
        valid = [p.value for p in TaskPriority]
        if priority not in valid:
            return (
                False,
                f"Invalid priority '{priority}'. Must be one of: {', '.join(valid)}",
            )
        return True, ""

    def validate_complexity(self, complexity: str) -> tuple[bool, str]:
        """Validate task complexity"""
        valid = [c.value for c in TaskComplexity]
        if complexity not in valid:
            return (
                False,
                f"Invalid complexity '{complexity}'. Must be one of: {', '.join(valid)}",
            )
        return True, ""

    VALID_TASK_TYPES = [t.value for t in TaskType]

    def validate_task_type(self, task_type: str) -> tuple[bool, str]:
        """Validate task type"""
        if task_type not in self.VALID_TASK_TYPES:
            return (
                False,
                f"Invalid task_type '{task_type}'. Must be one of: {', '.join(self.VALID_TASK_TYPES)}",
            )
        return True, ""

    async def create_task(
        self,
        project_id: str,
        title: str,
        description: str = "",
        assignee: str = "User",
        task_order: int = 0,
        priority: str = "medium",
        feature: str | None = None,
        sources: list[dict[str, Any]] = None,
        code_examples: list[dict[str, Any]] = None,
        owner: str | None = None,
        acceptance_criteria: list[dict[str, Any]] | None = None,
        execution_prompt: str | None = None,
        source_app: str | None = None,
        complexity: str = "simple",
        max_retries: int = 3,
        status: str = "draft",
        blocked_by: list[str] | None = None,
        created_by: str | None = None,
        created_from: str | None = None,
        task_type: str = "feature",
        phase: str | None = None,
        module: str | None = None,
        sprint: str | None = None,
        tags: list[str] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Create a new task under a project with automatic reordering.

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            # Validate inputs
            if not title or not isinstance(title, str) or len(title.strip()) == 0:
                return False, {"error": "Task title is required and must be a non-empty string"}

            if not project_id or not isinstance(project_id, str):
                return False, {"error": "Project ID is required and must be a string"}

            # Validate assignee
            is_valid, error_msg = self.validate_assignee(assignee)
            if not is_valid:
                return False, {"error": error_msg}

            # Validate priority
            is_valid, error_msg = self.validate_priority(priority)
            if not is_valid:
                return False, {"error": error_msg}

            # Validate status
            is_valid, error_msg = self.validate_status(status)
            if not is_valid:
                return False, {"error": error_msg}

            # Validate complexity
            is_valid, error_msg = self.validate_complexity(complexity)
            if not is_valid:
                return False, {"error": error_msg}

            # Validate task_type
            is_valid, error_msg = self.validate_task_type(task_type)
            if not is_valid:
                return False, {"error": error_msg}

            task_status = status

            # REORDERING LOGIC: If inserting at a specific position, increment existing tasks
            if task_order > 0:
                existing_tasks_response = (
                    self.supabase_client.table("archon_tasks")
                    .select("id, task_order")
                    .eq("project_id", project_id)
                    .eq("status", task_status)
                    .gte("task_order", task_order)
                    .execute()
                )

                if existing_tasks_response.data:
                    logger.info(f"Reordering {len(existing_tasks_response.data)} existing tasks")

                    for existing_task in existing_tasks_response.data:
                        new_order = existing_task["task_order"] + 1
                        self.supabase_client.table("archon_tasks").update({
                            "task_order": new_order,
                            "updated_at": datetime.now().isoformat(),
                        }).eq("id", existing_task["id"]).execute()

            now = datetime.now().isoformat()
            task_data: dict[str, Any] = {
                "project_id": project_id,
                "title": title,
                "description": description,
                "status": task_status,
                "assignee": assignee,
                "task_order": task_order,
                "priority": priority,
                "sources": sources or [],
                "code_examples": code_examples or [],
                "complexity": complexity,
                "max_retries": max_retries,
                "retry_count": 0,
                "state_history": [],
                "state_changed_at": now,
                "created_at": now,
                "updated_at": now,
                "task_type": task_type,
                "tags": tags or [],
            }

            if feature:
                task_data["feature"] = feature
            if owner:
                task_data["owner"] = owner
            if acceptance_criteria:
                task_data["acceptance_criteria"] = acceptance_criteria
            if execution_prompt:
                task_data["execution_prompt"] = execution_prompt
            if source_app:
                task_data["source_app"] = source_app
            if blocked_by is not None:
                task_data["blocked_by"] = blocked_by
            if created_by:
                task_data["created_by"] = created_by
            if created_from:
                task_data["created_from"] = created_from
            if phase:
                task_data["phase"] = phase
            if module:
                task_data["module"] = module
            if sprint:
                task_data["sprint"] = sprint

            # Initialize ownership tracking fields
            task_data["executed_by"] = None
            task_data["reviewed_by"] = []

            response = self.supabase_client.table("archon_tasks").insert(task_data).execute()

            if response.data:
                task = response.data[0]

                return True, {
                    "task": {
                        "id": task["id"],
                        "project_id": task["project_id"],
                        "title": task["title"],
                        "description": task["description"],
                        "status": task["status"],
                        "assignee": task["assignee"],
                        "task_order": task["task_order"],
                        "priority": task["priority"],
                        "complexity": task.get("complexity", "simple"),
                        "owner": task.get("owner"),
                        "source_app": task.get("source_app"),
                        "blocked_by": task.get("blocked_by", []),
                        "created_by": task.get("created_by"),
                        "created_from": task.get("created_from"),
                        "executed_by": task.get("executed_by"),
                        "reviewed_by": task.get("reviewed_by", []),
                        "created_at": task["created_at"],
                    }
                }
            else:
                return False, {"error": "Failed to create task"}

        except Exception as e:
            logger.error(f"Error creating task: {e}")
            return False, {"error": f"Error creating task: {str(e)}"}

    def list_tasks(
        self,
        project_id: str = None,
        status: str = None,
        include_closed: bool = False,
        exclude_large_fields: bool = False,
        include_archived: bool = False,
        search_query: str = None,
        task_type: str = None,
        phase: str = None,
        module: str = None,
        sprint: str = None,
        feature: str = None,
        parent_task_id: str = None,
    ) -> tuple[bool, dict[str, Any]]:
        """
        List tasks with various filters.

        Args:
            project_id: Filter by project
            status: Filter by status
            include_closed: Include done tasks
            exclude_large_fields: If True, excludes sources and code_examples fields
            include_archived: If True, includes archived tasks
            search_query: Keyword search in title, description, and feature fields

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            # Start with base query
            if exclude_large_fields:
                # Select all fields except large JSONB ones
                query = self.supabase_client.table("archon_tasks").select(
                    "id, project_id, parent_task_id, title, description, "
                    "status, assignee, task_order, priority, feature, archived, "
                    "archived_at, archived_by, created_at, updated_at, "
                    "sources, code_examples"  # Still fetch for counting, but will process differently
                )
            else:
                query = self.supabase_client.table("archon_tasks").select("*")

            # Track filters for debugging
            filters_applied = []

            # Apply filters
            if project_id:
                query = query.eq("project_id", project_id)
                filters_applied.append(f"project_id={project_id}")

            if status:
                # Validate status
                is_valid, error_msg = self.validate_status(status)
                if not is_valid:
                    return False, {"error": error_msg}
                query = query.eq("status", status)
                filters_applied.append(f"status={status}")
                # When filtering by specific status, don't apply include_closed filter
                # as it would be redundant or potentially conflicting
            elif not include_closed:
                # Exclude terminal states if no specific status filter is applied
                query = query.not_.in_("status", ["done", "cancelled"])
                filters_applied.append("exclude terminal tasks (done, cancelled)")

            # Apply keyword search if provided
            if search_query:
                # Split search query into terms
                search_terms = search_query.lower().split()
                
                # Build the filter expression for AND-of-ORs
                # Each term must match in at least one field (OR), and all terms must match (AND)
                if len(search_terms) == 1:
                    # Single term: simple OR across fields
                    term = search_terms[0]
                    query = query.or_(
                        f"title.ilike.%{term}%,"
                        f"description.ilike.%{term}%,"
                        f"feature.ilike.%{term}%"
                    )
                else:
                    # Multiple terms: use text search for proper AND logic
                    # Note: This requires full-text search columns to be set up in the database
                    # For now, we'll search for the full phrase in any field
                    full_query = search_query.lower()
                    query = query.or_(
                        f"title.ilike.%{full_query}%,"
                        f"description.ilike.%{full_query}%,"
                        f"feature.ilike.%{full_query}%"
                    )
                filters_applied.append(f"search={search_query}")

            # Filter out archived tasks only if not including them
            if not include_archived:
                query = query.or_("archived.is.null,archived.is.false")
                filters_applied.append("exclude archived tasks (null or false)")
            else:
                filters_applied.append("include all tasks (including archived)")

            # New categorical filters
            if task_type:
                is_valid, error_msg = self.validate_task_type(task_type)
                if not is_valid:
                    return False, {"error": error_msg}
                query = query.eq("task_type", task_type)
                filters_applied.append(f"task_type={task_type}")

            if phase:
                query = query.eq("phase", phase)
                filters_applied.append(f"phase={phase}")

            if module:
                query = query.eq("module", module)
                filters_applied.append(f"module={module}")

            if sprint:
                query = query.eq("sprint", sprint)
                filters_applied.append(f"sprint={sprint}")

            if feature:
                query = query.eq("feature", feature)
                filters_applied.append(f"feature={feature}")

            if parent_task_id:
                query = query.eq("parent_task_id", parent_task_id)
                filters_applied.append(f"parent_task_id={parent_task_id}")

            logger.debug(f"Listing tasks with filters: {', '.join(filters_applied)}")

            # Execute query and get raw response
            response = (
                query.order("task_order", desc=False).order("created_at", desc=False).execute()
            )

            # Debug: Log task status distribution and filter effectiveness
            if response.data:
                status_counts = {}
                archived_counts = {"null": 0, "true": 0, "false": 0}

                for task in response.data:
                    task_status = task.get("status", "unknown")
                    status_counts[task_status] = status_counts.get(task_status, 0) + 1

                    # Check archived field
                    archived_value = task.get("archived")
                    if archived_value is None:
                        archived_counts["null"] += 1
                    elif archived_value is True:
                        archived_counts["true"] += 1
                    else:
                        archived_counts["false"] += 1

                logger.debug(
                    f"Retrieved {len(response.data)} tasks. Status distribution: {status_counts}"
                )
                logger.debug(f"Archived field distribution: {archived_counts}")

                # If we're filtering by status and getting wrong results, log sample
                if status and len(response.data) > 0:
                    first_task = response.data[0]
                    logger.warning(
                        f"Status filter: {status}, First task status: {first_task.get('status')}, archived: {first_task.get('archived')}"
                    )
            else:
                logger.debug("No tasks found with current filters")

            tasks = []
            for task in response.data:
                task_data = {
                    "id": task["id"],
                    "project_id": task["project_id"],
                    "title": task["title"],
                    "description": task["description"],
                    "status": task["status"],
                    "assignee": task.get("assignee", "User"),
                    "task_order": task.get("task_order", 0),
                    "priority": task.get("priority", "medium"),
                    "feature": task.get("feature"),
                    "complexity": task.get("complexity", "simple"),
                    "owner": task.get("owner"),
                    "source_app": task.get("source_app"),
                    "retry_count": task.get("retry_count", 0),
                    "max_retries": task.get("max_retries", 3),
                    "state_changed_at": task.get("state_changed_at"),
                    "blocked_by": task.get("blocked_by", []),
                    "created_by": task.get("created_by"),
                    "created_from": task.get("created_from"),
                    "executed_by": task.get("executed_by"),
                    "reviewed_by": task.get("reviewed_by", []),
                    "created_at": task["created_at"],
                    "updated_at": task["updated_at"],
                    "archived": task.get("archived", False),
                    "task_type": task.get("task_type", "feature"),
                    "phase": task.get("phase"),
                    "module": task.get("module"),
                    "sprint": task.get("sprint"),
                    "tags": task.get("tags", []),
                    "parent_task_id": task.get("parent_task_id"),
                }

                if not exclude_large_fields:
                    task_data["sources"] = task.get("sources", [])
                    task_data["code_examples"] = task.get("code_examples", [])
                    task_data["acceptance_criteria"] = task.get("acceptance_criteria", [])
                    task_data["execution_result"] = task.get("execution_result")
                    task_data["architect_review"] = task.get("architect_review")
                    task_data["execution_prompt"] = task.get("execution_prompt")
                    task_data["state_history"] = task.get("state_history", [])
                    task_data["review_history"] = task.get("review_history", [])
                else:
                    task_data["stats"] = {
                        "sources_count": len(task.get("sources", [])),
                        "code_examples_count": len(task.get("code_examples", []))
                    }

                tasks.append(task_data)

            filter_info = []
            if project_id:
                filter_info.append(f"project_id={project_id}")
            if status:
                filter_info.append(f"status={status}")
            if not include_closed:
                filter_info.append("excluding closed tasks")

            return True, {
                "tasks": tasks,
                "total_count": len(tasks),
                "filters_applied": ", ".join(filter_info) if filter_info else "none",
                "include_closed": include_closed,
            }

        except Exception as e:
            logger.error(f"Error listing tasks: {e}")
            return False, {"error": f"Error listing tasks: {str(e)}"}

    def get_task(self, task_id: str) -> tuple[bool, dict[str, Any]]:
        """
        Get a specific task by ID.

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            response = (
                self.supabase_client.table("archon_tasks").select("*").eq("id", task_id).execute()
            )

            if response.data:
                task = response.data[0]
                return True, {"task": task}
            else:
                return False, {"error": f"Task with ID {task_id} not found"}

        except Exception as e:
            logger.error(f"Error getting task: {e}")
            return False, {"error": f"Error getting task: {str(e)}"}

    async def update_task(
        self, task_id: str, update_fields: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """
        Update task with specified fields.
        NOTE: For status changes, prefer TaskLifecycleService.execute_transition()
        which validates transitions and records audit trail.

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            # Build update data
            update_data: dict[str, Any] = {"updated_at": datetime.now().isoformat()}

            # Validate and add fields
            if "title" in update_fields:
                update_data["title"] = update_fields["title"]

            if "description" in update_fields:
                update_data["description"] = update_fields["description"]

            if "status" in update_fields:
                is_valid, error_msg = self.validate_status(update_fields["status"])
                if not is_valid:
                    return False, {"error": error_msg}
                update_data["status"] = update_fields["status"]

            if "assignee" in update_fields:
                is_valid, error_msg = self.validate_assignee(update_fields["assignee"])
                if not is_valid:
                    return False, {"error": error_msg}
                update_data["assignee"] = update_fields["assignee"]

            if "priority" in update_fields:
                is_valid, error_msg = self.validate_priority(update_fields["priority"])
                if not is_valid:
                    return False, {"error": error_msg}
                update_data["priority"] = update_fields["priority"]

            if "task_order" in update_fields:
                update_data["task_order"] = update_fields["task_order"]

            if "feature" in update_fields:
                update_data["feature"] = update_fields["feature"]

            if "complexity" in update_fields:
                is_valid, error_msg = self.validate_complexity(update_fields["complexity"])
                if not is_valid:
                    return False, {"error": error_msg}
                update_data["complexity"] = update_fields["complexity"]

            if "task_type" in update_fields:
                is_valid, error_msg = self.validate_task_type(update_fields["task_type"])
                if not is_valid:
                    return False, {"error": error_msg}
                update_data["task_type"] = update_fields["task_type"]

            # New lifecycle fields (no validation needed, just pass through)
            for field in [
                "owner", "execution_prompt", "source_app",
                "rejection_reason", "hold_reason", "max_retries",
                "phase", "module", "sprint", "tags",
            ]:
                if field in update_fields:
                    update_data[field] = update_fields[field]

            # JSONB fields
            for field in [
                "acceptance_criteria", "execution_result", "architect_review",
                "blocked_by", "executed_by", "reviewed_by", "review_history",
            ]:
                if field in update_fields:
                    update_data[field] = update_fields[field]

            # Update task
            response = (
                self.supabase_client.table("archon_tasks")
                .update(update_data)
                .eq("id", task_id)
                .execute()
            )

            if response.data:
                task = response.data[0]

                return True, {"task": task, "message": "Task updated successfully"}
            else:
                return False, {"error": f"Task with ID {task_id} not found"}

        except Exception as e:
            logger.error(f"Error updating task: {e}")
            return False, {"error": f"Error updating task: {str(e)}"}

    async def archive_task(
        self, task_id: str, archived_by: str = "mcp"
    ) -> tuple[bool, dict[str, Any]]:
        """
        Archive a task and all its subtasks (soft delete).

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            # First, check if task exists and is not already archived
            task_response = (
                self.supabase_client.table("archon_tasks").select("*").eq("id", task_id).execute()
            )
            if not task_response.data:
                return False, {"error": f"Task with ID {task_id} not found"}

            task = task_response.data[0]
            if task.get("archived") is True:
                return False, {"error": f"Task with ID {task_id} is already archived"}

            # Archive the task
            archive_data = {
                "archived": True,
                "archived_at": datetime.now().isoformat(),
                "archived_by": archived_by,
                "updated_at": datetime.now().isoformat(),
            }

            # Archive the main task
            response = (
                self.supabase_client.table("archon_tasks")
                .update(archive_data)
                .eq("id", task_id)
                .execute()
            )

            if response.data:

                return True, {"task_id": task_id, "message": "Task archived successfully"}
            else:
                return False, {"error": f"Failed to archive task {task_id}"}

        except Exception as e:
            logger.error(f"Error archiving task: {e}")
            return False, {"error": f"Error archiving task: {str(e)}"}

    def get_subtasks(self, parent_task_id: str) -> tuple[bool, dict[str, Any]]:
        """
        Get all direct subtasks of a given parent task.

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            response = (
                self.supabase_client.table("archon_tasks")
                .select("*")
                .eq("parent_task_id", parent_task_id)
                .or_("archived.is.null,archived.is.false")
                .order("task_order", desc=False)
                .execute()
            )

            subtasks = response.data or []
            return True, {"subtasks": subtasks, "count": len(subtasks)}

        except Exception as e:
            logger.error(f"Error getting subtasks for {parent_task_id}: {e}")
            return False, {"error": f"Error getting subtasks: {str(e)}"}

    def get_all_project_task_counts(self) -> tuple[bool, dict[str, dict[str, int]]]:
        """
        Get task counts for all projects in a single optimized query.
        
        Returns task counts grouped by project_id and status.
        
        Returns:
            Tuple of (success, counts_dict) where counts_dict is:
            {"project-id": {"todo": 5, "doing": 2, "review": 3, "done": 10}}
        """
        try:
            logger.debug("Fetching task counts for all projects in batch")

            # Query all non-archived tasks grouped by project_id and status
            response = (
                self.supabase_client.table("archon_tasks")
                .select("project_id, status")
                .or_("archived.is.null,archived.is.false")
                .execute()
            )

            if not response.data:
                logger.debug("No tasks found")
                return True, {}

            # Process results into counts by project and status
            counts_by_project = {}

            for task in response.data:
                project_id = task.get("project_id")
                status = task.get("status")

                if not project_id or not status:
                    continue

                # Initialize project counts if not exists
                if project_id not in counts_by_project:
                    counts_by_project[project_id] = dict.fromkeys(self.VALID_STATUSES, 0)

                # Count all statuses
                if status in self.VALID_STATUSES:
                    counts_by_project[project_id][status] += 1

            logger.debug(f"Task counts fetched for {len(counts_by_project)} projects")

            return True, counts_by_project

        except Exception as e:
            logger.error(f"Error fetching task counts: {e}")
            return False, {"error": f"Error fetching task counts: {str(e)}"}
