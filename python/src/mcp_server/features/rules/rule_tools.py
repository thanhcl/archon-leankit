"""
Consolidated rule management tools for Archon MCP Server.

Provides find_rules, manage_rule, and generate_claude_md tools.
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

DEFAULT_PAGE_SIZE = 20


def register_rule_tools(mcp: FastMCP):
    """Register rule management tools with the MCP server."""

    @mcp.tool()
    async def find_rules(
        ctx: Context,
        rule_id: str | None = None,
        project_id: str | None = None,
        section: str | None = None,
        enabled_only: bool = True,
        include_global: bool = True,
    ) -> str:
        """
        Find and list rules (consolidated: list + get).

        Args:
            rule_id: Get specific rule by ID (returns full details)
            project_id: Filter by project (also includes global rules if include_global=True)
            section: Filter by section (e.g., "validation", "security")
            enabled_only: Only return enabled rules (default: True)
            include_global: Include global rules when filtering by project (default: True)

        Returns:
            JSON with rules array or single rule

        Examples:
            find_rules()  # All enabled global rules
            find_rules(rule_id="abc-123")  # Get specific rule
            find_rules(project_id="proj-1")  # Global + project rules
            find_rules(section="security")  # Rules in security section
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            async with httpx.AsyncClient(timeout=timeout) as client:
                if rule_id:
                    response = await client.get(urljoin(api_url, f"/api/rules/{rule_id}"))
                    if response.status_code == 200:
                        return json.dumps({"success": True, "rule": response.json().get("rule")})
                    elif response.status_code == 404:
                        return MCPErrorFormatter.format_error(
                            "not_found", f"Rule {rule_id} not found",
                            suggestion="Use find_rules() to list available rules",
                        )
                    else:
                        return MCPErrorFormatter.from_http_error(response, "get rule")

                # List mode
                params: dict[str, Any] = {
                    "enabled_only": enabled_only,
                    "include_global": include_global,
                }
                if project_id:
                    params["project_id"] = project_id
                if section:
                    params["section"] = section

                response = await client.get(urljoin(api_url, "/api/rules"), params=params)

                if response.status_code == 200:
                    data = response.json()
                    rules = data.get("rules", [])
                    return json.dumps({
                        "success": True,
                        "rules": rules,
                        "total_count": len(rules),
                    })
                else:
                    return MCPErrorFormatter.from_http_error(response, "list rules")

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "find rules")
        except Exception as e:
            logger.error(f"Error finding rules: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "find rules")

    @mcp.tool()
    async def manage_rule(
        ctx: Context,
        action: str,  # "create" | "update" | "delete"
        rule_id: str | None = None,
        section: str | None = None,
        rule_text: str | None = None,
        project_id: str | None = None,
        priority: int | None = None,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> str:
        """
        Manage rules (consolidated: create/update/delete).

        Args:
            action: "create" | "update" | "delete"
            rule_id: Rule UUID for update/delete
            section: Rule section (e.g., "validation", "security", "coding-style")
            rule_text: The rule content in markdown format
            project_id: NULL for global rule, or project UUID for scoped rule
            priority: Sort order within section (lower = higher priority, default 50)
            source: Origin: "manual", "learning-promoted", "agent-suggested"
            enabled: Soft toggle (default: true)

        Examples:
            manage_rule("create", section="testing", rule_text="Always write tests", priority=15)
            manage_rule("update", rule_id="abc-123", enabled=False)
            manage_rule("delete", rule_id="abc-123")

        Returns: {success: bool, rule?: object, message: string}
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            async with httpx.AsyncClient(timeout=timeout) as client:
                if action == "create":
                    if not section or not rule_text:
                        return MCPErrorFormatter.format_error(
                            "validation_error",
                            "section and rule_text required for create",
                        )

                    body: dict[str, Any] = {
                        "section": section,
                        "rule_text": rule_text,
                    }
                    if project_id is not None:
                        body["project_id"] = project_id
                    if priority is not None:
                        body["priority"] = priority
                    if source is not None:
                        body["source"] = source

                    response = await client.post(
                        urljoin(api_url, "/api/rules"), json=body
                    )

                    if response.status_code == 200:
                        result = response.json()
                        return json.dumps({
                            "success": True,
                            "rule": result.get("rule"),
                            "message": "Rule created",
                        })
                    else:
                        return MCPErrorFormatter.from_http_error(response, "create rule")

                elif action == "update":
                    if not rule_id:
                        return MCPErrorFormatter.format_error(
                            "validation_error", "rule_id required for update"
                        )

                    update_data: dict[str, Any] = {}
                    if section is not None:
                        update_data["section"] = section
                    if rule_text is not None:
                        update_data["rule_text"] = rule_text
                    if project_id is not None:
                        update_data["project_id"] = project_id
                    if priority is not None:
                        update_data["priority"] = priority
                    if source is not None:
                        update_data["source"] = source
                    if enabled is not None:
                        update_data["enabled"] = enabled

                    if not update_data:
                        return MCPErrorFormatter.format_error(
                            "validation_error", "No fields to update"
                        )

                    response = await client.put(
                        urljoin(api_url, f"/api/rules/{rule_id}"), json=update_data
                    )

                    if response.status_code == 200:
                        result = response.json()
                        return json.dumps({
                            "success": True,
                            "rule": result.get("rule"),
                            "message": "Rule updated",
                        })
                    else:
                        return MCPErrorFormatter.from_http_error(response, "update rule")

                elif action == "delete":
                    if not rule_id:
                        return MCPErrorFormatter.format_error(
                            "validation_error", "rule_id required for delete"
                        )

                    response = await client.delete(
                        urljoin(api_url, f"/api/rules/{rule_id}")
                    )

                    if response.status_code == 200:
                        return json.dumps({
                            "success": True,
                            "message": "Rule deleted",
                        })
                    else:
                        return MCPErrorFormatter.from_http_error(response, "delete rule")

                else:
                    return MCPErrorFormatter.format_error(
                        "invalid_action",
                        f"Unknown action: {action}",
                        suggestion="Use 'create', 'update', or 'delete'",
                    )

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, f"{action} rule")
        except Exception as e:
            logger.error(f"Error managing rule ({action}): {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, f"{action} rule")

    @mcp.tool()
    async def optimize_rules(
        ctx: Context,
        project_id: str,
    ) -> str:
        """
        Analyze task metrics and suggest CLAUDE.md rule optimizations.

        Examines completed/failed tasks, learnings, and code patterns to
        produce rule suggestions with confidence scores. Owner must approve
        suggestions before applying.

        Args:
            project_id: Project UUID to analyze

        Returns:
            JSON with suggestions array (each with action, section, rule_text,
            confidence, reason, evidence) and analysis metrics.

        Example:
            optimize_rules(project_id="proj-123")
            # Returns: {success, suggestions: [{action: "add", section: "testing",
            #   rule_text: "...", confidence: 0.85, reason: "...", evidence: {...}}],
            #   analysis: {total_tasks, failure_rate, ...}}
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    urljoin(api_url, f"/api/rules/optimize/{project_id}")
                )

                if response.status_code == 200:
                    result = response.json()
                    return json.dumps({
                        "success": True,
                        "suggestions": result.get("suggestions", []),
                        "analysis": result.get("analysis", {}),
                        "project_id": result.get("project_id"),
                    })
                else:
                    return MCPErrorFormatter.from_http_error(response, "optimize rules")

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "optimize rules")
        except Exception as e:
            logger.error(f"Error optimizing rules: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "optimize rules")

    @mcp.tool()
    async def generate_claude_md(
        ctx: Context,
        project_id: str,
    ) -> str:
        """
        Generate assembled CLAUDE.md markdown for a project.

        Merges global rules (project_id=NULL) with project-scoped rules,
        ordered by section then priority within each section.

        Args:
            project_id: Project UUID to generate CLAUDE.md for

        Returns:
            JSON with markdown string, rule_count, and sections list

        Example:
            generate_claude_md(project_id="proj-123")
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    urljoin(api_url, f"/api/rules/generate-claude-md/{project_id}")
                )

                if response.status_code == 200:
                    result = response.json()
                    return json.dumps({
                        "success": True,
                        "markdown": result.get("markdown"),
                        "rule_count": result.get("rule_count"),
                        "sections": result.get("sections"),
                    })
                else:
                    return MCPErrorFormatter.from_http_error(response, "generate CLAUDE.md")

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "generate CLAUDE.md")
        except Exception as e:
            logger.error(f"Error generating CLAUDE.md: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "generate CLAUDE.md")
