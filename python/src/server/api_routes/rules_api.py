"""
Rules API — CRUD endpoints, CLAUDE.md generation, and auto-generated rule suggestions.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config.logfire_config import get_logger
from ..services.rules.rule_optimizer_service import RuleOptimizerService
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


@router.post("/optimize/{project_id}")
async def optimize_rules(project_id: str):
    """Analyze task metrics and suggest rule optimizations for a project.

    Returns suggestions with action (add/modify/remove), section, rule_text,
    confidence score, reason, and supporting evidence.
    Owner must approve suggestions before they are applied.
    """
    optimizer = RuleOptimizerService()
    ok, result = await optimizer.optimize_rules(project_id=project_id)
    if not ok:
        raise HTTPException(status_code=500, detail=result)
    return result


# ── Auto-generated rule suggestions ────────────────────────────────────────


def _get_supabase():
    from ..utils import get_supabase_client
    return get_supabase_client()


@router.get("/suggestions/{project_id}")
async def list_rule_suggestions(project_id: str, status: str = "pending"):
    """List auto-generated rule suggestions for a project.

    These are created automatically when a learning recurs >= 3 times and has a suggested_rule.
    """
    client = _get_supabase()
    try:
        resp = (
            client.table("archon_rule_suggestions")
            .select("*")
            .eq("project_id", project_id)
            .eq("status", status)
            .order("created_at", desc=True)
            .execute()
        )
        suggestions = resp.data or []
        return {"suggestions": suggestions, "count": len(suggestions)}
    except Exception as e:
        logger.error(f"Failed to list rule suggestions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


@router.post("/suggestions/{suggestion_id}/approve")
async def approve_rule_suggestion(suggestion_id: str):
    """Approve a rule suggestion: creates a rule in archon_rules and optionally writes to CLAUDE.md.

    Writes the assembled CLAUDE.md to the path in CLAUDE_MD_PATH env var (defaults to ./CLAUDE.md).
    """
    client = _get_supabase()
    try:
        resp = client.table("archon_rule_suggestions").select("*").eq("id", suggestion_id).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail={"error": f"Suggestion {suggestion_id} not found"})
        suggestion = resp.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e

    if suggestion.get("status") != "pending":
        raise HTTPException(status_code=400, detail={"error": f"Suggestion is already {suggestion['status']}"})

    service = _get_service()
    ok, rule_result = service.create_rule(
        section=suggestion["section"],
        rule_text=suggestion["rule_text"],
        project_id=suggestion.get("project_id"),
        priority=100,
        source="learning-promotion",
    )
    if not ok:
        raise HTTPException(status_code=400, detail=rule_result)

    try:
        client.table("archon_rule_suggestions").update({
            "status": "approved",
            "updated_at": datetime.now().isoformat(),
        }).eq("id", suggestion_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark suggestion as approved: {e}")

    # Write updated CLAUDE.md to disk if a path is configured
    claude_md_written = False
    project_id = suggestion.get("project_id")
    if project_id:
        claude_md_path = os.environ.get("CLAUDE_MD_PATH", "./CLAUDE.md")
        try:
            ok_md, md_result = service.generate_claude_md(project_id=project_id)
            if ok_md:
                target = Path(claude_md_path)
                target.write_text(md_result["markdown"], encoding="utf-8")
                claude_md_written = True
                logger.info(f"CLAUDE.md written to {target.resolve()} after rule approval")
        except Exception as e:
            logger.warning(f"Failed to write CLAUDE.md: {e}")

    return {
        "rule": rule_result.get("rule"),
        "suggestion_id": suggestion_id,
        "claude_md_written": claude_md_written,
    }


@router.post("/suggestions/{suggestion_id}/reject")
async def reject_rule_suggestion(suggestion_id: str):
    """Reject a rule suggestion, marking it dismissed."""
    client = _get_supabase()
    try:
        resp = client.table("archon_rule_suggestions").select("id", "status").eq("id", suggestion_id).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail={"error": f"Suggestion {suggestion_id} not found"})
        if resp.data[0].get("status") != "pending":
            raise HTTPException(status_code=400, detail={"error": f"Suggestion is already {resp.data[0]['status']}"})

        client.table("archon_rule_suggestions").update({
            "status": "rejected",
            "updated_at": datetime.now().isoformat(),
        }).eq("id", suggestion_id).execute()

        return {"message": f"Suggestion {suggestion_id} rejected"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reject suggestion: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e
