"""Project template API endpoints."""

from fastapi import APIRouter, HTTPException

from ..models.api_contracts import (
    ProjectTemplateListResponse,
    ProjectTemplateResponse,
    validate_response,
)
from ..services.projects.project_template_service import project_template_service

router = APIRouter(prefix="/api", tags=["project-templates"])


def _serialize_template(template) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "project_type": template.project_type,
        "bootstrap_policy": template.bootstrap_policy,
        "bootstrap_architect_provider": template.bootstrap_architect_provider,
        "default_model_routing": template.default_model_routing,
        "default_review_policy": template.default_review_policy,
        "task_pack": [
            {
                "key": t.key,
                "title": t.title,
                "description": t.description,
                "task_type": t.task_type,
                "priority": t.priority,
                "complexity": t.complexity,
                "tags": list(t.tags),
                "blocked_on_key": t.blocked_on_key,
            }
            for t in template.task_pack
        ],
        "icon": template.icon,
        "color": template.color,
    }


@router.get("/project-templates", response_model=ProjectTemplateListResponse)
async def list_project_templates():
    """List all available project templates."""
    templates = project_template_service.list_templates()
    return validate_response(
        {"templates": [_serialize_template(t) for t in templates]},
        ProjectTemplateListResponse,
    )


@router.get("/project-templates/{template_id}", response_model=ProjectTemplateResponse)
async def get_project_template(template_id: str):
    """Get a single project template by ID."""
    template = project_template_service.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return validate_response(_serialize_template(template), ProjectTemplateResponse)
