"""
Rules API — CRUD endpoints and CLAUDE.md generation for archon_rules.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config.logfire_config import get_logger
from ..services.rules.rule_service import RuleService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/rules", tags=["rules"])


class CreateRuleRequest(BaseModel):
    section: str
    rule_text: str
    project_id: str | None = None
    priority: int = 100
    source: str = "manual"


class UpdateRuleRequest(BaseModel):
    section: str | None = None
    rule_text: str | None = None
    project_id: str | None = None
    priority: int | None = None
    source: str | None = None
    enabled: bool | None = None


def _get_service() -> RuleService:
    return RuleService()


@router.get("")
async def list_rules(
    project_id: str | None = None,
    section: str | None = None,
    enabled_only: bool = True,
    include_global: bool = True,
):
    """List rules, optionally filtered by project, section, and enabled status."""
    service = _get_service()
    ok, result = service.list_rules(
        project_id=project_id,
        section=section,
        enabled_only=enabled_only,
        include_global=include_global,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=result)
    return result


@router.post("")
async def create_rule(request: CreateRuleRequest):
    """Create a new rule."""
    service = _get_service()
    ok, result = service.create_rule(
        section=request.section,
        rule_text=request.rule_text,
        project_id=request.project_id,
        priority=request.priority,
        source=request.source,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/generate-claude-md/{project_id}")
async def generate_claude_md(project_id: str):
    """Generate assembled CLAUDE.md markdown for a project (global + project rules)."""
    service = _get_service()
    ok, result = service.generate_claude_md(project_id=project_id)
    if not ok:
        raise HTTPException(status_code=500, detail=result)
    return result


@router.get("/{rule_id}")
async def get_rule(rule_id: str):
    """Get a single rule by ID."""
    service = _get_service()
    ok, result = service.get_rule(rule_id=rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail=result)
    return result


@router.put("/{rule_id}")
async def update_rule(rule_id: str, request: UpdateRuleRequest):
    """Update a rule by ID."""
    service = _get_service()
    update_fields: dict[str, Any] = {}
    for field in ["section", "rule_text", "project_id", "priority", "source", "enabled"]:
        value = getattr(request, field)
        if value is not None:
            update_fields[field] = value

    if not update_fields:
        raise HTTPException(status_code=422, detail={"error": "No fields to update"})

    ok, result = service.update_rule(rule_id=rule_id, update_fields=update_fields)
    if not ok:
        raise HTTPException(status_code=404, detail=result)
    return result


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str):
    """Delete a rule by ID."""
    service = _get_service()
    ok, result = service.delete_rule(rule_id=rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail=result)
    return result
