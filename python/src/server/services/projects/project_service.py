"""
Project Service Module for Archon

This module provides core business logic for project operations that can be
shared between MCP tools and FastAPI endpoints. It follows the pattern of
separating business logic from transport-specific code.
"""

# Removed direct logging import - using unified config
from datetime import datetime
from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger
from .bootstrap_plan_service import BootstrapPlanService
from .bootstrap_task_template import create_project_bootstrap_task_pack
from .engine_policy_service import EnginePolicyService

logger = get_logger(__name__)

# Virtual Office defaults — applied when creating a project without explicit team_config
DEFAULT_TEAM_CONFIG: list[dict[str, Any]] = [
    {"slotIndex": 0, "agentId": "coder-a", "name": "Coder A", "role": "backend-dev", "color": "#FF6B00", "decorations": []},
    {"slotIndex": 1, "agentId": "coder-b", "name": "Coder B", "role": "backend-dev", "color": "#2563EB", "decorations": []},
    {"slotIndex": 2, "agentId": "tester", "name": "Tester", "role": "qa", "color": "#16A34A", "decorations": []},
    {"slotIndex": 3, "agentId": "reviewer", "name": "Reviewer", "role": "senior-dev", "color": "#7C3AED", "decorations": []},
    {"slotIndex": 4, "agentId": "db-admin", "name": "DB Admin", "role": "dba", "color": "#92400E", "decorations": []},
]
DEFAULT_DIRECTOR_CONFIG: dict[str, str] = {"name": "Director", "color": "#1B3A5C"}
DEFAULT_TEAM_LEAD_CONFIG: dict[str, str] = {"name": "Team Lead", "color": "#4F46E5"}

# Fields that belong to office configuration
OFFICE_FIELDS = [
    "source_app",
    "layout_id",
    "team_config",
    "director_config",
    "team_lead_config",
    "office_settings",
]


class ProjectService:
    """Service class for project operations"""

    def __init__(self, supabase_client=None):
        """Initialize with optional supabase client"""
        self.supabase_client = supabase_client or get_supabase_client()

    def create_project(
        self,
        title: str,
        github_repo: str = None,
        **kwargs: Any,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Create a new project with optional PRD, GitHub repo, and office config.

        Accepts Virtual Office fields via kwargs:
            source_app, layout_id, team_config, director_config,
            team_lead_config, office_settings

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            # Validate inputs
            if not title or not isinstance(title, str) or len(title.strip()) == 0:
                return False, {"error": "Project title is required and must be a non-empty string"}

            # Create project data
            project_data = {
                "title": title.strip(),
                "description": kwargs.get("description", "") or "",
                "docs": [],  # Will add PRD document after creation
                "features": kwargs.get("features", []),
                "data": kwargs.get("data", []),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }

            if "pinned" in kwargs and kwargs["pinned"] is not None:
                project_data["pinned"] = kwargs["pinned"]

            if github_repo and isinstance(github_repo, str) and len(github_repo.strip()) > 0:
                project_data["github_repo"] = github_repo.strip()

            # Apply Virtual Office fields from kwargs
            for field in OFFICE_FIELDS:
                if field in kwargs and kwargs[field] is not None:
                    project_data[field] = kwargs[field]

            # Auto-generate default team config if not provided
            if "team_config" not in project_data:
                project_data["team_config"] = DEFAULT_TEAM_CONFIG
            if "director_config" not in project_data:
                project_data["director_config"] = DEFAULT_DIRECTOR_CONFIG
            if "team_lead_config" not in project_data:
                project_data["team_lead_config"] = DEFAULT_TEAM_LEAD_CONFIG

            # Insert project
            response = self.supabase_client.table("archon_projects").insert(project_data).execute()

            if not response.data:
                logger.error("Supabase returned empty data for project creation")
                return False, {"error": "Failed to create project - database returned no data"}

            project = response.data[0]
            project_id = project["id"]
            logger.info(f"Project created successfully with ID: {project_id}")

            self._mirror_deprecated_policy_fields({**project_data, **project})

            bootstrap_task = None
            bootstrap_tasks: list[dict[str, Any]] | None = None
            bootstrap_plan: dict[str, Any] | None = None
            if kwargs.get("create_bootstrap_task", True):
                bootstrap_tasks = create_project_bootstrap_task_pack(
                    supabase_client=self.supabase_client,
                    project_id=project_id,
                    project_title=project["title"],
                    project_description=project_data.get("description"),
                    github_repo=project.get("github_repo"),
                    source_app=project_data.get("source_app"),
                    bootstrap_template=kwargs.get("bootstrap_template"),
                    project_type=kwargs.get("project_type"),
                    bootstrap_policy=kwargs.get("bootstrap_policy"),
                    bootstrap_architect_provider=kwargs.get("bootstrap_architect_provider"),
                    bootstrap_architect_model=kwargs.get("bootstrap_architect_model"),
                )
                if bootstrap_tasks:
                    bootstrap_task = bootstrap_tasks[0]
                ok, plan_result = BootstrapPlanService(self.supabase_client).list_plans(
                    project_id=project_id,
                    limit=1,
                )
                if ok and plan_result.get("plans"):
                    bootstrap_plan = plan_result["plans"][0]
                    kwargs["bootstrap_architect_provider"] = (
                        bootstrap_plan.get("resolved_provider")
                        or bootstrap_plan.get("requested_provider")
                        or kwargs.get("bootstrap_architect_provider")
                    )
                    kwargs["bootstrap_architect_model"] = (
                        bootstrap_plan.get("model")
                        or kwargs.get("bootstrap_architect_model")
                    )

            return True, {
                "project": {
                    "id": project_id,
                    "title": project["title"],
                    "description": project_data.get("description", ""),
                    "github_repo": project.get("github_repo"),
                    "created_at": project["created_at"],
                    "bootstrap_task": bootstrap_task,
                    "bootstrap_tasks": bootstrap_tasks,
                    "bootstrap_plan": bootstrap_plan,
                    "bootstrap_template": kwargs.get("bootstrap_template"),
                    "project_type": kwargs.get("project_type"),
                    "bootstrap_policy": kwargs.get("bootstrap_policy"),
                    "bootstrap_architect_provider": kwargs.get("bootstrap_architect_provider"),
                    "bootstrap_architect_model": kwargs.get("bootstrap_architect_model"),
                }
            }

        except Exception as e:
            logger.error(f"Error creating project: {e}")
            return False, {"error": f"Database error: {str(e)}"}

    def list_projects(self, include_content: bool = True) -> tuple[bool, dict[str, Any]]:
        """
        List all projects.

        Args:
            include_content: If True (default), includes docs, features, data fields.
                           If False, returns lightweight metadata only with counts.

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            if include_content:
                # Current behavior - maintain backward compatibility
                response = (
                    self.supabase_client.table("archon_projects")
                    .select("*")
                    .order("created_at", desc=True)
                    .execute()
                )

                projects = []
                for project in response.data:
                    projects.append({
                        "id": project["id"],
                        "title": project["title"],
                        "github_repo": project.get("github_repo"),
                        "created_at": project["created_at"],
                        "updated_at": project["updated_at"],
                        "pinned": project.get("pinned", False),
                        "description": project.get("description", ""),
                        "docs": project.get("docs", []),
                        "features": project.get("features", []),
                        "data": project.get("data", []),
                        **self._extract_office_fields(project),
                    })
            else:
                # Lightweight response for MCP - fetch all data but only return metadata + stats
                # FIXED: N+1 query problem - now using single query
                response = (
                    self.supabase_client.table("archon_projects")
                    .select("*")  # Fetch all fields in single query
                    .order("created_at", desc=True)
                    .execute()
                )

                projects = []
                for project in response.data:
                    # Calculate counts from fetched data (no additional queries)
                    docs_count = len(project.get("docs", []))
                    features_count = len(project.get("features", []))
                    has_data = bool(project.get("data", []))

                    # Return only metadata + stats, excluding large JSONB fields
                    projects.append({
                        "id": project["id"],
                        "title": project["title"],
                        "github_repo": project.get("github_repo"),
                        "created_at": project["created_at"],
                        "updated_at": project["updated_at"],
                        "pinned": project.get("pinned", False),
                        "description": project.get("description", ""),
                        "stats": {
                            "docs_count": docs_count,
                            "features_count": features_count,
                            "has_data": has_data
                        },
                        **self._extract_office_fields(project),
                    })

            return True, {"projects": projects, "total_count": len(projects)}

        except Exception as e:
            logger.error(f"Error listing projects: {e}")
            return False, {"error": f"Error listing projects: {str(e)}"}

    def get_project(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """
        Get a specific project by ID.

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            response = (
                self.supabase_client.table("archon_projects")
                .select("*")
                .eq("id", project_id)
                .execute()
            )

            if response.data:
                project = response.data[0]

                # Get linked sources
                technical_sources = []
                business_sources = []

                try:
                    # Get source IDs from project_sources table
                    sources_response = (
                        self.supabase_client.table("archon_project_sources")
                        .select("source_id, notes")
                        .eq("project_id", project["id"])
                        .execute()
                    )

                    # Collect source IDs by type
                    technical_source_ids = []
                    business_source_ids = []

                    for source_link in sources_response.data:
                        if source_link.get("notes") == "technical":
                            technical_source_ids.append(source_link["source_id"])
                        elif source_link.get("notes") == "business":
                            business_source_ids.append(source_link["source_id"])

                    # Fetch full source objects
                    if technical_source_ids:
                        tech_sources_response = (
                            self.supabase_client.table("archon_sources")
                            .select("*")
                            .in_("source_id", technical_source_ids)
                            .execute()
                        )
                        technical_sources = tech_sources_response.data

                    if business_source_ids:
                        biz_sources_response = (
                            self.supabase_client.table("archon_sources")
                            .select("*")
                            .in_("source_id", business_source_ids)
                            .execute()
                        )
                        business_sources = biz_sources_response.data

                except Exception as e:
                    logger.warning(
                        f"Failed to retrieve linked sources for project {project['id']}: {e}"
                    )

                # Add sources to project data
                project["technical_sources"] = technical_sources
                project["business_sources"] = business_sources

                return True, {"project": project}
            else:
                return False, {"error": f"Project with ID {project_id} not found"}

        except Exception as e:
            logger.error(f"Error getting project: {e}")
            return False, {"error": f"Error getting project: {str(e)}"}

    def delete_project(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """
        Delete a project and all its associated tasks.

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            # First, check if project exists
            check_response = (
                self.supabase_client.table("archon_projects")
                .select("id")
                .eq("id", project_id)
                .execute()
            )
            if not check_response.data:
                return False, {"error": f"Project with ID {project_id} not found"}

            # Get task count for reporting
            tasks_response = (
                self.supabase_client.table("archon_tasks")
                .select("id")
                .eq("project_id", project_id)
                .execute()
            )
            tasks_count = len(tasks_response.data) if tasks_response.data else 0

            # Delete the project (tasks will be deleted by cascade)
            response = (
                self.supabase_client.table("archon_projects")
                .delete()
                .eq("id", project_id)
                .execute()
            )

            # For DELETE operations, success is indicated by no error, not by response.data content
            # response.data will be empty list [] even on successful deletion
            return True, {
                "project_id": project_id,
                "deleted_tasks": tasks_count,
                "message": "Project deleted successfully",
            }

        except Exception as e:
            logger.error(f"Error deleting project: {e}")
            return False, {"error": f"Error deleting project: {str(e)}"}

    def get_project_features(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """
        Get features from a project's features JSONB field.

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            response = (
                self.supabase_client.table("archon_projects")
                .select("features")
                .eq("id", project_id)
                .single()
                .execute()
            )

            if not response.data:
                return False, {"error": "Project not found"}

            features = response.data.get("features", [])

            # Extract feature labels for dropdown options
            feature_options = []
            for feature in features:
                if isinstance(feature, dict) and "data" in feature and "label" in feature["data"]:
                    feature_options.append({
                        "id": feature.get("id", ""),
                        "label": feature["data"]["label"],
                        "type": feature["data"].get("type", ""),
                        "feature_type": feature.get("type", "page"),
                    })

            return True, {"features": feature_options, "count": len(feature_options)}

        except Exception as e:
            # Check if it's a "no rows found" error from PostgREST
            error_message = str(e)
            if "The result contains 0 rows" in error_message or "PGRST116" in error_message:
                return False, {"error": "Project not found"}

            logger.error(f"Error getting project features: {e}")
            return False, {"error": f"Error getting project features: {str(e)}"}

    @staticmethod
    def _extract_office_fields(project: dict[str, Any]) -> dict[str, Any]:
        """Extract Virtual Office fields from a project row."""
        return {
            "source_app": project.get("source_app"),
            "layout_id": project.get("layout_id", "classic"),
            "team_config": project.get("team_config", []),
            "director_config": project.get("director_config", DEFAULT_DIRECTOR_CONFIG),
            "team_lead_config": project.get("team_lead_config", DEFAULT_TEAM_LEAD_CONFIG),
            "office_settings": project.get("office_settings", {}),
        }

    def list_office_configs(self) -> tuple[bool, dict[str, Any]]:
        """Return all projects with their Virtual Office configuration.

        Optimized endpoint: only fetches the columns Virtual Office needs.
        """
        try:
            response = (
                self.supabase_client.table("archon_projects")
                .select(
                    "id, title, description, source_app, layout_id, "
                    "team_config, director_config, team_lead_config, office_settings"
                )
                .order("created_at", desc=True)
                .execute()
            )

            projects = []
            for p in response.data or []:
                projects.append({
                    "id": p["id"],
                    "title": p.get("title", ""),
                    "description": p.get("description", ""),
                    **self._extract_office_fields(p),
                })

            return True, {"projects": projects, "count": len(projects)}
        except Exception as e:
            logger.error(f"Error listing office configs: {e}")
            return False, {"error": str(e)}

    def update_project(
        self, project_id: str, update_fields: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """
        Update a project with specified fields.

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            # Build update data
            update_data = {"updated_at": datetime.now().isoformat()}

            # Add allowed fields
            allowed_fields = [
                "title",
                "description",
                "github_repo",
                "docs",
                "features",
                "data",
                "technical_sources",
                "business_sources",
                "pinned",
                *OFFICE_FIELDS,
            ]

            for field in allowed_fields:
                if field in update_fields:
                    update_data[field] = update_fields[field]

            # Handle pinning logic - only one project can be pinned at a time
            if update_fields.get("pinned") is True:
                # Unpin any other pinned projects first
                unpin_response = (
                    self.supabase_client.table("archon_projects")
                    .update({"pinned": False})
                    .neq("id", project_id)
                    .eq("pinned", True)
                    .execute()
                )
                logger.debug(f"Unpinned {len(unpin_response.data or [])} other projects before pinning {project_id}")

            # Update the target project
            response = (
                self.supabase_client.table("archon_projects")
                .update(update_data)
                .eq("id", project_id)
                .execute()
            )

            if response.data and len(response.data) > 0:
                project = response.data[0]
                self._mirror_deprecated_policy_fields(project)
                return True, {"project": project, "message": "Project updated successfully"}
            else:
                # If update didn't return data, fetch the project to ensure it exists and get current state
                get_response = (
                    self.supabase_client.table("archon_projects")
                    .select("*")
                    .eq("id", project_id)
                    .execute()
                )
                if get_response.data and len(get_response.data) > 0:
                    project = get_response.data[0]
                    self._mirror_deprecated_policy_fields(project)
                    return True, {"project": project, "message": "Project updated successfully"}
                else:
                    return False, {"error": f"Project with ID {project_id} not found"}

        except Exception as e:
            logger.error(f"Error updating project: {e}")
            return False, {"error": f"Error updating project: {str(e)}"}

    def _mirror_deprecated_policy_fields(self, project_row: dict[str, Any]) -> None:
        """Mirror deprecated project policy metadata into engine_policies."""
        ok, result = EnginePolicyService(self.supabase_client).sync_project_policy_sources(project_row)
        if not ok:
            project_id = project_row.get("id", "unknown")
            logger.warning(
                "Failed to mirror deprecated project policy fields into engine_policies "
                f"| project_id={project_id} | error={result.get('error', 'unknown error')}",
                exc_info=True,
            )
