"""
Rule Service — CRUD and CLAUDE.md generation for archon_rules.
"""

from datetime import datetime
from typing import Any

from src.server.config.logfire_config import get_logger
from src.server.utils import get_supabase_client

logger = get_logger(__name__)

VALID_SECTIONS = [
    "validation", "integration", "security", "coding-style",
    "architecture", "testing", "performance", "documentation",
]


class RuleService:
    """Service class for archon_rules operations."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    @staticmethod
    def validate_section(section: str) -> tuple[bool, str]:
        """Validate section against VALID_SECTIONS."""
        if section not in VALID_SECTIONS:
            return False, (
                f"Invalid section '{section}'. "
                f"Must be one of: {', '.join(VALID_SECTIONS)}"
            )
        return True, ""

    def list_rules(
        self,
        project_id: str | None = None,
        section: str | None = None,
        enabled_only: bool = True,
        include_global: bool = True,
    ) -> tuple[bool, dict[str, Any]]:
        """List rules with optional filters."""
        try:
            query = self.supabase_client.table("archon_rules").select("*")

            if enabled_only:
                query = query.eq("enabled", True)

            if section:
                query = query.eq("section", section)

            if project_id and include_global:
                # Both global and project-scoped
                query = query.or_(f"project_id.is.null,project_id.eq.{project_id}")
            elif project_id:
                query = query.eq("project_id", project_id)
            elif include_global:
                query = query.is_("project_id", "null")

            response = query.order("section").order("priority").execute()

            rules = response.data or []
            return True, {"rules": rules, "total_count": len(rules)}

        except Exception as e:
            logger.error(f"Error listing rules: {e}", exc_info=True)
            return False, {"error": f"Error listing rules: {str(e)}"}

    def get_rule(self, rule_id: str) -> tuple[bool, dict[str, Any]]:
        """Get a single rule by ID."""
        try:
            response = (
                self.supabase_client.table("archon_rules")
                .select("*")
                .eq("id", rule_id)
                .execute()
            )

            if response.data:
                return True, {"rule": response.data[0]}
            return False, {"error": f"Rule {rule_id} not found"}

        except Exception as e:
            logger.error(f"Error getting rule: {e}", exc_info=True)
            return False, {"error": f"Error getting rule: {str(e)}"}

    def create_rule(
        self,
        section: str,
        rule_text: str,
        project_id: str | None = None,
        priority: int = 100,
        source: str = "manual",
    ) -> tuple[bool, dict[str, Any]]:
        """Create a new rule."""
        try:
            if not section or not rule_text:
                return False, {"error": "section and rule_text are required"}

            is_valid, error_msg = self.validate_section(section)
            if not is_valid:
                return False, {"error": error_msg}

            now = datetime.now().isoformat()
            rule_data: dict[str, Any] = {
                "section": section,
                "rule_text": rule_text,
                "priority": priority,
                "source": source,
                "enabled": True,
                "created_at": now,
                "updated_at": now,
            }
            if project_id:
                rule_data["project_id"] = project_id

            response = (
                self.supabase_client.table("archon_rules")
                .insert(rule_data)
                .execute()
            )

            if response.data:
                return True, {"rule": response.data[0]}
            return False, {"error": "Failed to create rule"}

        except Exception as e:
            logger.error(f"Error creating rule: {e}", exc_info=True)
            return False, {"error": f"Error creating rule: {str(e)}"}

    def update_rule(
        self, rule_id: str, update_fields: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """Update a rule by ID."""
        try:
            if "section" in update_fields:
                is_valid, error_msg = self.validate_section(update_fields["section"])
                if not is_valid:
                    return False, {"error": error_msg}

            update_data: dict[str, Any] = {"updated_at": datetime.now().isoformat()}

            allowed_fields = ["section", "rule_text", "priority", "source", "enabled", "project_id"]
            for field in allowed_fields:
                if field in update_fields:
                    update_data[field] = update_fields[field]

            response = (
                self.supabase_client.table("archon_rules")
                .update(update_data)
                .eq("id", rule_id)
                .execute()
            )

            if response.data:
                return True, {"rule": response.data[0]}
            return False, {"error": f"Rule {rule_id} not found"}

        except Exception as e:
            logger.error(f"Error updating rule: {e}", exc_info=True)
            return False, {"error": f"Error updating rule: {str(e)}"}

    def delete_rule(self, rule_id: str) -> tuple[bool, dict[str, Any]]:
        """Delete a rule by ID."""
        try:
            response = (
                self.supabase_client.table("archon_rules")
                .delete()
                .eq("id", rule_id)
                .execute()
            )

            if response.data:
                return True, {"message": f"Rule {rule_id} deleted"}
            return False, {"error": f"Rule {rule_id} not found"}

        except Exception as e:
            logger.error(f"Error deleting rule: {e}", exc_info=True)
            return False, {"error": f"Error deleting rule: {str(e)}"}

    def get_global_rules(self, enabled_only: bool = True) -> tuple[bool, dict[str, Any]]:
        """Get rules where project_id IS NULL (global rules)."""
        return self.list_rules(project_id=None, enabled_only=enabled_only, include_global=True)

    def get_project_rules(self, project_id: str, enabled_only: bool = True) -> tuple[bool, dict[str, Any]]:
        """Get rules scoped to a specific project (excludes global rules)."""
        return self.list_rules(project_id=project_id, enabled_only=enabled_only, include_global=False)

    def generate_claude_md(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """
        Generate assembled CLAUDE.md markdown from global + project-scoped rules.
        Rules are grouped by section and ordered by priority within each section.
        """
        try:
            # Fetch global + project rules, enabled only
            ok, result = self.list_rules(
                project_id=project_id, enabled_only=True, include_global=True
            )
            if not ok:
                return False, result

            rules = result["rules"]
            if not rules:
                return True, {
                    "markdown": "# CLAUDE.md\n\nNo rules configured.\n",
                    "rule_count": 0,
                    "sections": [],
                }

            # Separate global vs project rules
            global_rules = [r for r in rules if r.get("project_id") is None]
            project_rules = [r for r in rules if r.get("project_id") is not None]

            # Group by section within each scope
            def _group_by_section(rule_list: list[dict]) -> dict[str, list[dict]]:
                grouped: dict[str, list[dict]] = {}
                for rule in rule_list:
                    grouped.setdefault(rule["section"], []).append(rule)
                return grouped

            global_sections = _group_by_section(global_rules)
            project_sections = _group_by_section(project_rules)

            # Collect all unique section names in order
            section_names = list(dict.fromkeys(
                [r["section"] for r in rules]
            ))

            # Build markdown
            lines = ["# CLAUDE.md", "", "<!-- Auto-generated from archon_rules -->", ""]

            if global_rules:
                lines.append("<!-- Global Rules -->")
                lines.append("")
                for sec in section_names:
                    for rule in global_sections.get(sec, []):
                        lines.append(rule["rule_text"])
                        lines.append("")

            if project_rules:
                lines.append(f"<!-- Project Rules: {project_id} -->")
                lines.append("")
                for sec in section_names:
                    for rule in project_sections.get(sec, []):
                        lines.append(rule["rule_text"])
                        lines.append("")

            markdown = "\n".join(lines)

            return True, {
                "markdown": markdown,
                "rule_count": len(rules),
                "sections": section_names,
            }

        except Exception as e:
            logger.error(f"Error generating CLAUDE.md: {e}", exc_info=True)
            return False, {"error": f"Error generating CLAUDE.md: {str(e)}"}
