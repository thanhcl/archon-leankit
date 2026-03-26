"""
Agent Definition Service — CRUD operations for archon_agent_definitions.

Agent definitions are control-plane records that capture specialized agent roles:
capabilities, model preferences, and optional prompt templates injected by the
prompt builder at task execution time.
"""

from typing import Any

from ...config.logfire_config import get_logger
from ...utils import get_supabase_client

logger = get_logger(__name__)

_TABLE = "archon_agent_definitions"


class AgentDefinitionService:
    """Manages agent definition CRUD against the archon_agent_definitions table."""

    def __init__(self, supabase_client: Any = None) -> None:
        self.supabase_client = supabase_client or get_supabase_client()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_definitions(self, include_inactive: bool = False) -> tuple[bool, dict[str, Any]]:
        """Return all agent definitions ordered by slug."""
        try:
            query = self.supabase_client.table(_TABLE).select("*").order("slug")
            if not include_inactive:
                query = query.eq("is_active", True)
            result = query.execute()
            rows = result.data or []
            return True, {"agent_definitions": rows, "total_count": len(rows)}
        except Exception as e:
            logger.error(f"list_definitions failed | error={e}", exc_info=True)
            return False, {"error": str(e)}

    def get_definition(self, definition_id: str) -> tuple[bool, dict[str, Any]]:
        """Return a single agent definition by UUID."""
        try:
            result = self.supabase_client.table(_TABLE).select("*").eq("id", definition_id).single().execute()
            if not result.data:
                return False, {"error": f"Agent definition '{definition_id}' not found"}
            return True, {"agent_definition": result.data}
        except Exception as e:
            logger.error(f"get_definition failed | id={definition_id} | error={e}", exc_info=True)
            return False, {"error": str(e)}

    def get_definition_by_slug(self, slug: str) -> tuple[bool, dict[str, Any]]:
        """Return a single active agent definition by slug."""
        try:
            result = self.supabase_client.table(_TABLE).select("*").eq("slug", slug).single().execute()
            if not result.data:
                return False, {"error": f"Agent definition with slug '{slug}' not found"}
            return True, {"agent_definition": result.data}
        except Exception as e:
            logger.error(f"get_definition_by_slug failed | slug={slug} | error={e}", exc_info=True)
            return False, {"error": str(e)}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create_definition(
        self,
        slug: str,
        name: str,
        description: str | None = None,
        capabilities: list[str] | None = None,
        model_preferences: dict[str, Any] | None = None,
        prompt_template: str | None = None,
        is_active: bool = True,
    ) -> tuple[bool, dict[str, Any]]:
        """Create a new agent definition. Raises on duplicate slug."""
        try:
            data: dict[str, Any] = {
                "slug": slug,
                "name": name,
                "is_active": is_active,
                "capabilities": capabilities or [],
                "model_preferences": model_preferences or {},
            }
            if description is not None:
                data["description"] = description
            if prompt_template is not None:
                data["prompt_template"] = prompt_template

            result = self.supabase_client.table(_TABLE).insert(data).execute()
            if not result.data:
                return False, {"error": "Insert returned no data"}
            logger.info(f"Agent definition created | slug={slug}")
            return True, {"agent_definition": result.data[0]}
        except Exception as e:
            logger.error(f"create_definition failed | slug={slug} | error={e}", exc_info=True)
            return False, {"error": str(e)}

    def update_definition(
        self,
        definition_id: str,
        **fields: Any,
    ) -> tuple[bool, dict[str, Any]]:
        """Partial-update an agent definition. Only provided fields are changed."""
        try:
            allowed = {"slug", "name", "description", "capabilities", "model_preferences", "prompt_template", "is_active"}
            updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
            if not updates:
                return False, {"error": "No valid fields to update"}

            from datetime import datetime, timezone
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()

            result = (
                self.supabase_client.table(_TABLE)
                .update(updates)
                .eq("id", definition_id)
                .execute()
            )
            if not result.data:
                return False, {"error": f"Agent definition '{definition_id}' not found or update failed"}
            logger.info(f"Agent definition updated | id={definition_id} | fields={list(updates.keys())}")
            return True, {"agent_definition": result.data[0]}
        except Exception as e:
            logger.error(f"update_definition failed | id={definition_id} | error={e}", exc_info=True)
            return False, {"error": str(e)}

    def delete_definition(self, definition_id: str) -> tuple[bool, dict[str, Any]]:
        """Permanently delete an agent definition."""
        try:
            result = (
                self.supabase_client.table(_TABLE)
                .delete()
                .eq("id", definition_id)
                .execute()
            )
            if not result.data:
                return False, {"error": f"Agent definition '{definition_id}' not found"}
            logger.info(f"Agent definition deleted | id={definition_id}")
            return True, {"message": f"Agent definition '{definition_id}' deleted"}
        except Exception as e:
            logger.error(f"delete_definition failed | id={definition_id} | error={e}", exc_info=True)
            return False, {"error": str(e)}
