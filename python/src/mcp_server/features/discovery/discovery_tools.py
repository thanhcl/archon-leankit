"""
Discovery MCP Tools — manage feeds, view history, manage gaps.

HTTP-based version: calls discovery API endpoints on the server service.
"""

import json
import logging
from urllib.parse import urljoin, urlencode

import httpx
from mcp.server.fastmcp import Context, FastMCP

from src.mcp_server.utils.error_handling import MCPErrorFormatter
from src.mcp_server.utils.timeout_config import get_default_timeout
from src.server.config.service_discovery import get_api_url

logger = logging.getLogger(__name__)


def register_discovery_tools(mcp: FastMCP):
    """Register all discovery tools with the MCP server."""

    @mcp.tool()
    async def discovery_add_feed(
        ctx: Context,
        project_id: str,
        feed_type: str,
        name: str,
        config: dict,
        poll_interval_hours: int = 24,
        max_items_per_poll: int = 5,
    ) -> str:
        """
        Add a new auto-discovery feed to continuously find new knowledge sources.

        Args:
            project_id: Project UUID
            feed_type: One of:
                - 'github_trending': Monitor trending repos
                - 'github_repo': Watch specific repos for changes
                - 'reddit': Monitor subreddits
                - 'rss': Poll RSS/Atom feeds
                - 'hackernews': Monitor HN stories
                - 'web_search': Periodic web searches
                - 'reference_snowball': Follow links from existing wiki pages
            name: Human-readable name like "React Subreddit"
            config: Type-specific configuration dict. Examples:
                github_trending: {"languages": ["typescript"], "min_stars": 100}
                github_repo: {"owner": "anthropics", "repos": ["claude-code"]}
                reddit: {"subreddits": ["reactjs"], "min_upvotes": 50}
                rss: {"urls": ["https://blog.example.com/feed.xml"]}
                hackernews: {"min_score": 100, "topics": ["ai"]}
                web_search: {"keywords": ["React 2026"], "recency_days": 30}
                reference_snowball: {"max_depth": 2, "follow_domains": ["react.dev"]}
            poll_interval_hours: How often to check (default: 24)
            max_items_per_poll: Max new items per check (default: 5)

        Returns:
            Created feed with ID and schedule info.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            body = {
                "project_id": project_id,
                "feed_type": feed_type,
                "name": name,
                "config": config,
                "poll_interval_hours": poll_interval_hours,
                "max_items_per_poll": max_items_per_poll,
            }

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(urljoin(api_url, "/api/discovery/feeds"), json=body)

                if response.status_code == 200:
                    return json.dumps({"success": True, "feed": response.json()}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "add discovery feed")

        except Exception as e:
            logger.error(f"Error in discovery_add_feed: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "add discovery feed")

    @mcp.tool()
    async def discovery_list_feeds(
        ctx: Context,
        project_id: str | None = None,
        enabled_only: bool = False,
    ) -> str:
        """
        List all configured discovery feeds with stats.

        Args:
            project_id: Optional project UUID to filter
            enabled_only: Only show enabled feeds

        Returns:
            List of feeds with type, config, schedule, and discovery stats.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            params: dict = {}
            if project_id:
                params["project_id"] = project_id
            if enabled_only:
                params["enabled_only"] = "true"

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    urljoin(api_url, f"/api/discovery/feeds?{urlencode(params)}")
                )

                if response.status_code == 200:
                    return json.dumps({"success": True, **response.json()}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "list discovery feeds")

        except Exception as e:
            logger.error(f"Error in discovery_list_feeds: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "list discovery feeds")

    @mcp.tool()
    async def discovery_history(
        ctx: Context,
        project_id: str | None = None,
        feed_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> str:
        """
        View discovery history — what URLs were found, ingested, or skipped.

        Args:
            project_id: Optional project filter
            feed_id: Optional feed filter
            status: Filter by 'discovered' | 'queued' | 'ingested' | 'skipped' | 'failed'
            limit: Max results (default: 20)

        Returns:
            List of discovered items with status, source linkage, and timestamps.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            params: dict = {"limit": limit}
            if project_id:
                params["project_id"] = project_id
            if feed_id:
                params["feed_id"] = feed_id
            if status:
                params["status"] = status

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    urljoin(api_url, f"/api/discovery/history?{urlencode(params)}")
                )

                if response.status_code == 200:
                    return json.dumps({"success": True, **response.json()}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "get discovery history")

        except Exception as e:
            logger.error(f"Error in discovery_history: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "get discovery history")

    @mcp.tool()
    async def wiki_gaps(
        ctx: Context,
        project_id: str,
        status: str = "open",
        limit: int = 20,
    ) -> str:
        """
        List identified knowledge gaps in the wiki.

        Knowledge gaps are created by:
        - wiki_lint() detecting orphans, stale content, or missing topics
        - Agents identifying missing information during work
        - Manual reporting

        Open gaps can feed into auto-discovery to find sources that fill them.

        Args:
            project_id: Project UUID
            status: 'open' (default) | 'discovery_queued' | 'resolved' | 'wont_fix'
            limit: Max results (default: 20)

        Returns:
            List of gaps with topic, description, type, priority, and resolution status.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            params = {"project_id": project_id, "status": status, "limit": limit}

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    urljoin(api_url, f"/api/discovery/gaps?{urlencode(params)}")
                )

                if response.status_code == 200:
                    return json.dumps({"success": True, **response.json()}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "get knowledge gaps")

        except Exception as e:
            logger.error(f"Error in wiki_gaps: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "get knowledge gaps")

    @mcp.tool()
    async def discovery_run(
        ctx: Context,
        project_id: str | None = None,
        feed_id: str | None = None,
        max_iterations: int = 2,
    ) -> str:
        """
        Run a full discovery cycle: discover → ingest → lint.

        This triggers the auto-discovery pipeline which:
        1. Polls all due feeds (or a specific feed) for new URLs
        2. Records discoveries with dedup
        3. Crawls and embeds new URLs into RAG knowledge base
        4. Creates wiki pages from ingested sources
        5. Runs lint to detect new knowledge gaps
        6. Optionally repeats gap-filling (max 2 iterations)

        Args:
            project_id: Optional project scope
            feed_id: Optional specific feed to run (skips due-check)
            max_iterations: Max discover→lint loops (default: 2)

        Returns:
            Summary with feeds_polled, items_discovered, pages_created, gaps_found.

        Note: This can take several minutes for large batches. Use discovery_history
        to check progress afterward.
        """
        try:
            api_url = get_api_url()
            timeout = httpx.Timeout(300.0, connect=5.0)  # 5 min for full cycle

            body: dict = {"max_iterations": max_iterations}
            if project_id:
                body["project_id"] = project_id
            if feed_id:
                body["feed_id"] = feed_id

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    urljoin(api_url, "/api/discovery/run"), json=body
                )

                if response.status_code == 200:
                    return json.dumps({"success": True, **response.json()}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "run discovery")

        except Exception as e:
            logger.error(f"Error in discovery_run: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "run discovery")
