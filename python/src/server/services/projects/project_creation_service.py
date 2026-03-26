"""
Project Creation Service Module for Archon

This module handles the complex project creation workflow including
AI-assisted documentation generation and progress tracking.
"""

# Removed direct logging import - using unified config
from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger
from .bootstrap_plan_service import BootstrapPlanService
from .bootstrap_task_template import create_project_bootstrap_task_pack_async
from .project_service import ProjectService
from .project_template_service import project_template_service

logger = get_logger(__name__)


class ProjectCreationService:
    """Service class for advanced project creation with AI assistance"""

    def __init__(self, supabase_client=None):
        """Initialize with optional supabase client"""
        self.supabase_client = supabase_client or get_supabase_client()

    async def create_project_with_ai(
        self,
        progress_id: str,
        title: str,
        description: str | None = None,
        github_repo: str | None = None,
        **kwargs,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Create a project with AI-assisted documentation generation.

        Args:
            progress_id: Progress tracking identifier
            title: Project title
            description: Project description
            github_repo: GitHub repository URL
            **kwargs: Additional project data

        Returns:
            Tuple of (success, result_dict)
        """
        logger.info(
            f"🏗️ [PROJECT-CREATION] Starting create_project_with_ai for progress_id: {progress_id}, title: {title}"
        )
        try:
            project_service = ProjectService(self.supabase_client)
            create_kwargs = dict(kwargs)
            create_kwargs["description"] = description or ""
            # Apply project template defaults before resolving bootstrap parameters.
            bootstrap_template = create_kwargs.get("bootstrap_template")
            if bootstrap_template:
                create_kwargs = project_template_service.apply_to_create_kwargs(bootstrap_template, create_kwargs)
            create_bootstrap_task = create_kwargs.get("create_bootstrap_task", True)
            if create_bootstrap_task:
                # Use the async architect-provider bootstrap path in this workflow.
                create_kwargs["create_bootstrap_task"] = False
            success, create_result = project_service.create_project(
                title=title,
                github_repo=github_repo,
                **create_kwargs,
            )
            if not success:
                raise RuntimeError(create_result.get("error", f"Failed to create project '{title}'"))

            created_project = create_result["project"]
            project_id = created_project["id"]
            logger.info(f"Created project {project_id} in database")

            if create_bootstrap_task:
                bootstrap_tasks = await create_project_bootstrap_task_pack_async(
                    supabase_client=self.supabase_client,
                    project_id=project_id,
                    project_title=created_project["title"],
                    project_description=created_project.get("description"),
                    github_repo=created_project.get("github_repo"),
                    source_app=create_kwargs.get("source_app"),
                    bootstrap_template=create_kwargs.get("bootstrap_template"),
                    project_type=create_kwargs.get("project_type"),
                    bootstrap_policy=create_kwargs.get("bootstrap_policy"),
                    bootstrap_architect_provider=create_kwargs.get("bootstrap_architect_provider"),
                    bootstrap_architect_model=create_kwargs.get("bootstrap_architect_model"),
                )
                created_project["bootstrap_task"] = bootstrap_tasks[0] if bootstrap_tasks else None
                created_project["bootstrap_tasks"] = bootstrap_tasks
                created_project["bootstrap_template"] = create_kwargs.get("bootstrap_template")
                created_project["project_type"] = create_kwargs.get("project_type")
                created_project["bootstrap_policy"] = create_kwargs.get("bootstrap_policy")
                created_project["bootstrap_architect_provider"] = create_kwargs.get("bootstrap_architect_provider")
                created_project["bootstrap_architect_model"] = create_kwargs.get("bootstrap_architect_model")
                ok, plan_result = BootstrapPlanService(self.supabase_client).list_plans(
                    project_id=project_id,
                    limit=1,
                )
                if ok and plan_result.get("plans"):
                    created_project["bootstrap_plan"] = plan_result["plans"][0]
                    created_project["bootstrap_architect_provider"] = (
                        created_project["bootstrap_plan"].get("resolved_provider")
                        or created_project["bootstrap_plan"].get("requested_provider")
                        or created_project.get("bootstrap_architect_provider")
                    )
                    created_project["bootstrap_architect_model"] = (
                        created_project["bootstrap_plan"].get("model")
                        or created_project.get("bootstrap_architect_model")
                    )

            # AI processing step

            # Generate AI documentation if API key is available
            ai_success = await self._generate_ai_documentation(
                progress_id, project_id, title, description, github_repo
            )

            # Final success - fetch complete project data
            final_project_response = (
                self.supabase_client.table("archon_projects")
                .select("*")
                .eq("id", project_id)
                .execute()
            )
            if final_project_response.data:
                final_project = final_project_response.data[0]

                # Prepare project data for frontend
                project_data_for_frontend = {
                    "id": final_project["id"],
                    "title": final_project["title"],
                    "description": final_project.get("description", ""),
                    "github_repo": final_project.get("github_repo"),
                    "created_at": final_project["created_at"],
                    "updated_at": final_project["updated_at"],
                    "docs": final_project.get("docs", []),  # PRD documents will be here
                    "features": final_project.get("features", {}),
                    "data": final_project.get("data", {}),
                    "pinned": final_project.get("pinned", False),
                    "technical_sources": [],  # Empty initially
                    "business_sources": [],  # Empty initially
                    "bootstrap_task": created_project.get("bootstrap_task"),
                    "bootstrap_tasks": created_project.get("bootstrap_tasks"),
                    "bootstrap_template": created_project.get("bootstrap_template"),
                    "project_type": created_project.get("project_type"),
                    "bootstrap_policy": created_project.get("bootstrap_policy"),
                    "bootstrap_architect_provider": created_project.get("bootstrap_architect_provider"),
                    "bootstrap_architect_model": created_project.get("bootstrap_architect_model"),
                    "bootstrap_plan": created_project.get("bootstrap_plan"),
                }


                return True, {
                    "project_id": project_id,
                    "project": project_data_for_frontend,
                    "ai_documentation_generated": ai_success,
                }
            else:
                # Fallback if we can't fetch the project

                return True, {
                    "project_id": project_id,
                    "project": created_project,
                    "ai_documentation_generated": ai_success,
                }

        except Exception as e:
            logger.error(
                f"🚨 [PROJECT-CREATION] Project creation failed for progress_id={progress_id}, title={title}: {e}",
                exc_info=True,
            )
            return False, {"error": str(e)}

    async def _generate_ai_documentation(
        self,
        progress_id: str,
        project_id: str,
        title: str,
        description: str | None,
        github_repo: str | None,
    ) -> bool:
        """
        Generate AI documentation for the project.

        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if LLM provider is configured
            from ..credential_service import credential_service
            provider_config = await credential_service.get_active_provider("llm")

            if not provider_config:
                # No LLM provider configured, skip AI documentation
                return False

            # Import DocumentAgent (lazy import to avoid startup issues)
            from ...agents.document_agent import DocumentAgent



            # Initialize DocumentAgent
            document_agent = DocumentAgent()

            # Generate comprehensive PRD using conversation
            prd_request = f"Create a PRD document titled '{title} - Product Requirements Document' for a project called '{title}'"
            if description:
                prd_request += f" with the following description: {description}"
            if github_repo:
                prd_request += f" (GitHub repo: {github_repo})"

            # Create a progress callback for the document agent
            async def agent_progress_callback(update_data):
                pass  # Progress tracking removed

            # Run the document agent to create PRD
            agent_result = await document_agent.run_conversation(
                user_message=prd_request,
                project_id=project_id,
                user_id="system",
                progress_callback=agent_progress_callback,
            )

            if agent_result.success:

                return True
            else:
                return False

        except Exception as ai_error:
            logger.warning(f"AI generation failed, continuing with basic project: {ai_error}")

            return False
