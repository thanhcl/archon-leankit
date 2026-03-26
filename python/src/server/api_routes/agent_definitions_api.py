"""
Agent Definitions API — control-plane CRUD for specialized agent roles.

GET    /api/agent-definitions            — list all definitions (active only by default)
POST   /api/agent-definitions            — create a new definition
GET    /api/agent-definitions/{id}       — retrieve single definition by UUID
PUT    /api/agent-definitions/{id}       — full or partial update
DELETE /api/agent-definitions/{id}       — permanently delete
GET    /api/agent-definitions/by-slug/{slug} — retrieve by slug
"""

from fastapi import APIRouter, HTTPException, Query

from ..config.logfire_config import get_logger
from ..models.api_contracts import (
    CreateAgentDefinitionRequest,
    UpdateAgentDefinitionRequest,
)
from ..services.projects.agent_definition_service import AgentDefinitionService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/agent-definitions", tags=["agent-definitions"])


# ---------------------------------------------------------------------------
# GET — list
# ---------------------------------------------------------------------------


@router.get("")
async def list_agent_definitions(include_inactive: bool = Query(False)):
    """Return all agent definitions.

    Set ``include_inactive=true`` to include definitions with ``is_active=false``.
    """
    try:
        service = AgentDefinitionService()
        ok, result = service.list_definitions(include_inactive=include_inactive)
        if not ok:
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to fetch definitions"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GET agent definitions failed | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# POST — create
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_agent_definition(request: CreateAgentDefinitionRequest):
    """Create a new agent definition.

    ``slug`` must be unique across all definitions.
    """
    try:
        service = AgentDefinitionService()
        ok, result = service.create_definition(
            slug=request.slug,
            name=request.name,
            description=request.description,
            capabilities=request.capabilities,
            model_preferences=request.model_preferences,
            prompt_template=request.prompt_template,
            is_active=request.is_active,
        )
        if not ok:
            error_msg = result.get("error", "Failed to create definition")
            status = 409 if "unique" in error_msg.lower() or "duplicate" in error_msg.lower() else 500
            raise HTTPException(status_code=status, detail=error_msg)
        logger.info(f"Agent definition created | slug={request.slug}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"POST agent definition failed | slug={request.slug} | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# GET — by slug (must appear before /{id} to avoid shadowing)
# ---------------------------------------------------------------------------


@router.get("/by-slug/{slug}")
async def get_agent_definition_by_slug(slug: str):
    """Return an agent definition by its slug."""
    try:
        service = AgentDefinitionService()
        ok, result = service.get_definition_by_slug(slug)
        if not ok:
            raise HTTPException(status_code=404, detail=result.get("error", "Not found"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GET agent definition by slug failed | slug={slug} | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# GET — single by ID
# ---------------------------------------------------------------------------


@router.get("/{definition_id}")
async def get_agent_definition(definition_id: str):
    """Return a single agent definition by UUID."""
    try:
        service = AgentDefinitionService()
        ok, result = service.get_definition(definition_id)
        if not ok:
            raise HTTPException(status_code=404, detail=result.get("error", "Not found"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GET agent definition failed | id={definition_id} | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# PUT — update
# ---------------------------------------------------------------------------


@router.put("/{definition_id}")
async def update_agent_definition(definition_id: str, request: UpdateAgentDefinitionRequest):
    """Partially update an agent definition.

    Only fields present in the request body are modified.
    """
    try:
        service = AgentDefinitionService()
        ok, result = service.update_definition(
            definition_id=definition_id,
            slug=request.slug,
            name=request.name,
            description=request.description,
            capabilities=request.capabilities,
            model_preferences=request.model_preferences,
            prompt_template=request.prompt_template,
            is_active=request.is_active,
        )
        if not ok:
            error_msg = result.get("error", "Failed to update definition")
            status = 404 if "not found" in error_msg.lower() else 500
            raise HTTPException(status_code=status, detail=error_msg)
        logger.info(f"Agent definition updated | id={definition_id}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PUT agent definition failed | id={definition_id} | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


@router.delete("/{definition_id}")
async def delete_agent_definition(definition_id: str):
    """Permanently delete an agent definition."""
    try:
        service = AgentDefinitionService()
        ok, result = service.delete_definition(definition_id)
        if not ok:
            error_msg = result.get("error", "Failed to delete definition")
            status = 404 if "not found" in error_msg.lower() else 500
            raise HTTPException(status_code=status, detail=error_msg)
        logger.info(f"Agent definition deleted | id={definition_id}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"DELETE agent definition failed | id={definition_id} | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e
