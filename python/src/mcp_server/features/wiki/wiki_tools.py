"""
Wiki MCP Tools — search, create, update, link, graph, lint wiki pages.

HTTP-based version: calls wiki API endpoints on the server service.
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


def register_wiki_tools(mcp: FastMCP):
    """Register all wiki KB tools with the MCP server."""

    @mcp.tool()
    async def wiki_search(
        ctx: Context,
        query: str,
        project_id: str | None = None,
        page_type: str | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> str:
        """
        Search wiki pages by full-text query.

        The wiki is a structured knowledge graph built on top of the RAG knowledge base.
        Each wiki page represents one entity, concept, source summary, or synthesis
        with cross-references (links) to related pages.

        Args:
            query: Search keywords (2-5 words work best).
                   Good: "React hooks patterns", "Supabase RLS"
                   Bad: "how to implement authentication with JWT in React"
            project_id: Optional project UUID to scope search
            page_type: Filter by type: 'entity' | 'concept' | 'source_summary' | 'synthesis'
            category: Filter by category: 'tool' | 'framework' | 'pattern' | 'architecture' | etc.
            limit: Max results (default: 10)

        Returns:
            JSON with matching wiki pages including title, summary, type, tags, quality score.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            params: dict = {"query": query, "limit": limit}
            if project_id:
                params["project_id"] = project_id
            if page_type:
                params["page_type"] = page_type
            if category:
                params["category"] = category

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    urljoin(api_url, f"/api/wiki/search?{urlencode(params)}")
                )

                if response.status_code == 200:
                    result = response.json()
                    return json.dumps({"success": True, **result}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "wiki search")

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "wiki search")
        except Exception as e:
            logger.error(f"Error in wiki_search: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "wiki search")

    @mcp.tool()
    async def wiki_get_page(
        ctx: Context,
        page_id: str | None = None,
        slug: str | None = None,
        project_id: str | None = None,
        include_links: bool = True,
    ) -> str:
        """
        Get a wiki page by ID or slug with its content and linked pages.

        Args:
            page_id: Page UUID (use this OR slug+project_id)
            slug: Page slug like "react-hooks" (requires project_id)
            project_id: Required when using slug
            include_links: Include inbound/outbound links (default: true)

        Returns:
            Full wiki page with content, metadata, and links to related pages.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            async with httpx.AsyncClient(timeout=timeout) as client:
                if page_id:
                    url = urljoin(api_url, f"/api/wiki/pages/{page_id}?include_links={include_links}")
                elif slug and project_id:
                    url = urljoin(api_url, f"/api/wiki/pages/by-slug/{slug}?project_id={project_id}&include_links={include_links}")
                else:
                    return json.dumps({"success": False, "error": "Provide page_id or (slug + project_id)"})

                response = await client.get(url)

                if response.status_code == 200:
                    return json.dumps({"success": True, "page": response.json()}, indent=2)
                elif response.status_code == 404:
                    return MCPErrorFormatter.format_error(
                        error_type="not_found",
                        message="Wiki page not found",
                        suggestion="Check the page_id or slug, or use wiki_search to find pages",
                        http_status=404,
                    )
                else:
                    return MCPErrorFormatter.from_http_error(response, "get wiki page")

        except Exception as e:
            logger.error(f"Error in wiki_get_page: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "get wiki page")

    @mcp.tool()
    async def wiki_create_page(
        ctx: Context,
        project_id: str,
        title: str,
        content: str,
        page_type: str,
        category: str | None = None,
        tags: list[str] | None = None,
        source_ids: list[str] | None = None,
        summary: str | None = None,
        status: str = "draft",
    ) -> str:
        """
        Create a new wiki page. Auto-generates slug, embedding, and registers in search.

        Args:
            project_id: Project UUID (required)
            title: Page title like "React Hooks" (required)
            content: Markdown content of the wiki page (required)
            page_type: 'entity' | 'concept' | 'source_summary' | 'synthesis' (required)
            category: 'tool' | 'framework' | 'pattern' | 'architecture' | 'api' | etc.
            tags: List of tags like ["react", "frontend"]
            source_ids: List of RAG source IDs this page is derived from
            summary: 1-2 sentence summary (auto-generated from title if not provided)
            status: 'draft' (default) | 'active'

        Returns:
            Created wiki page with ID and slug.

        After creating a page, use wiki_link() to connect it to related pages.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            body = {
                "project_id": project_id,
                "title": title,
                "content": content,
                "page_type": page_type,
                "status": status,
            }
            if category:
                body["category"] = category
            if tags:
                body["tags"] = tags
            if source_ids:
                body["source_ids"] = source_ids
            if summary:
                body["summary"] = summary

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(urljoin(api_url, "/api/wiki/pages"), json=body)

                if response.status_code == 200:
                    return json.dumps({"success": True, "page": response.json()}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "create wiki page")

        except Exception as e:
            logger.error(f"Error in wiki_create_page: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "create wiki page")

    @mcp.tool()
    async def wiki_update_page(
        ctx: Context,
        page_id: str,
        content: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        category: str | None = None,
    ) -> str:
        """
        Update a wiki page. Re-generates embedding on content/summary change.

        Args:
            page_id: Page UUID (required)
            content: New markdown content
            title: New title
            summary: New summary
            tags: New tags list
            status: 'draft' | 'active' | 'stale' | 'archived'
            category: New category

        Returns:
            Updated wiki page.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            body: dict = {}
            if content is not None:
                body["content"] = content
            if title is not None:
                body["title"] = title
            if summary is not None:
                body["summary"] = summary
            if tags is not None:
                body["tags"] = tags
            if status is not None:
                body["status"] = status
            if category is not None:
                body["category"] = category

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.put(
                    urljoin(api_url, f"/api/wiki/pages/{page_id}"), json=body
                )

                if response.status_code == 200:
                    return json.dumps({"success": True, "page": response.json()}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "update wiki page")

        except Exception as e:
            logger.error(f"Error in wiki_update_page: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "update wiki page")

    @mcp.tool()
    async def wiki_link(
        ctx: Context,
        from_page_id: str,
        to_page_id: str,
        link_type: str = "related",
        context: str | None = None,
        confidence: str = "inferred",
    ) -> str:
        """
        Create a link between two wiki pages.

        Args:
            from_page_id: Source page UUID
            to_page_id: Target page UUID
            link_type: 'related' | 'depends_on' | 'contradicts' | 'extends' | 'supersedes' | 'example_of' | 'part_of'
            context: Why these pages are linked
            confidence: Link confidence level:
                - 'extracted': explicit reference found in source content (strength=1.0)
                - 'inferred' (default): reasonable inference from shared tags, topics, etc. (strength=0.5)
                - 'ambiguous': weak signal, may not be meaningful (strength=0.2)

        Returns:
            Created link with metadata.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            body = {
                "from_page_id": from_page_id,
                "to_page_id": to_page_id,
                "link_type": link_type,
                "confidence": confidence,
                "created_by": "agent",
            }
            if context:
                body["context"] = context

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(urljoin(api_url, "/api/wiki/links"), json=body)

                if response.status_code == 200:
                    return json.dumps({"success": True, "link": response.json()}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "create wiki link")

        except Exception as e:
            logger.error(f"Error in wiki_link: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "create wiki link")

    @mcp.tool()
    async def wiki_graph(
        ctx: Context,
        project_id: str | None = None,
        page_id: str | None = None,
        depth: int = 2,
    ) -> str:
        """
        Get the knowledge graph around a page or for an entire project.

        Returns nodes (pages) and edges (links) for visualization or navigation.

        Args:
            project_id: Get full project graph (provide this OR page_id)
            page_id: Get graph around a specific page (with BFS up to depth)
            depth: How many hops to traverse from page_id (default: 2, max: 4)

        Returns:
            JSON with nodes (id, slug, title, type) and edges (from, to, type, strength).
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            params: dict = {"depth": min(depth, 4)}
            if project_id:
                params["project_id"] = project_id
            if page_id:
                params["page_id"] = page_id

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    urljoin(api_url, f"/api/wiki/graph?{urlencode(params)}")
                )

                if response.status_code == 200:
                    result = response.json()
                    return json.dumps({"success": True, **result}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "get wiki graph")

        except Exception as e:
            logger.error(f"Error in wiki_graph: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "get wiki graph")

    @mcp.tool()
    async def wiki_lint(ctx: Context, project_id: str) -> str:
        """
        Run knowledge health check on the wiki.

        Detects:
        - Orphan pages (no inbound links — isolated knowledge)
        - Stale content (not updated in 30+ days)
        - Contradictions (pages with conflicting information)
        - Low quality pages (poor freshness, links, or source backing)
        - Broken source references

        Also creates knowledge gaps for significant findings which
        can feed into the auto-discovery pipeline.

        Args:
            project_id: Project UUID to lint

        Returns:
            Lint report with categorized issues and stats.
        """
        try:
            api_url = get_api_url()
            timeout = httpx.Timeout(60.0, connect=5.0)  # Lint can be slow

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    urljoin(api_url, f"/api/wiki/lint/{project_id}")
                )

                if response.status_code == 200:
                    return json.dumps({"success": True, **response.json()}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "wiki lint")

        except Exception as e:
            logger.error(f"Error in wiki_lint: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "wiki lint")

    # ── E3: Write-Back Loop ───────────────────────────────────

    @mcp.tool()
    async def wiki_note(
        ctx: Context,
        text: str,
        project_id: str,
        link_to_slugs: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """
        Quick-capture a note into the wiki as a synthesis page.

        Unlike wiki_create_page which requires full metadata, wiki_note
        just takes text and optional link targets. Perfect for capturing
        insights during work without breaking flow.

        The title is auto-generated from the first line of text.
        The page is created as 'active' immediately (no draft stage).

        Args:
            text: Note content in markdown. First line becomes the title.
            project_id: Project UUID (required)
            link_to_slugs: Optional slugs of existing pages to link to.
                           e.g. ["react-hooks", "state-management"]
            tags: Optional tags like ["insight", "architecture"]

        Returns:
            Created wiki page with link count.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            body: dict = {"text": text, "project_id": project_id}
            if link_to_slugs:
                body["link_to_slugs"] = link_to_slugs
            if tags:
                body["tags"] = tags

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    urljoin(api_url, "/api/wiki/notes"), json=body
                )
                if response.status_code == 200:
                    return json.dumps({"success": True, "page": response.json()}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "create wiki note")

        except Exception as e:
            logger.error(f"Error in wiki_note: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "create wiki note")

    # ── E4: Community Detection ───────────────────────────────

    @mcp.tool()
    async def wiki_communities(
        ctx: Context,
        project_id: str,
        recompute: bool = False,
    ) -> str:
        """
        View or recompute topic communities in the wiki knowledge graph.

        Communities are auto-detected clusters of related wiki pages using
        label propagation on the link graph. Useful for:
        - Understanding how knowledge is organized
        - Finding related pages you didn't know were connected
        - Identifying isolated topic clusters

        Args:
            project_id: Project UUID
            recompute: If True, re-runs community detection (takes a few seconds).
                       If False (default), returns cached community assignments.

        Returns:
            List of communities with their member pages.
        """
        try:
            api_url = get_api_url()
            timeout = httpx.Timeout(60.0, connect=5.0)

            async with httpx.AsyncClient(timeout=timeout) as client:
                if recompute:
                    response = await client.post(
                        urljoin(api_url, f"/api/wiki/communities/{project_id}")
                    )
                else:
                    response = await client.get(
                        urljoin(api_url, f"/api/wiki/communities/{project_id}")
                    )

                if response.status_code == 200:
                    return json.dumps({"success": True, **response.json()}, indent=2)
                else:
                    return MCPErrorFormatter.from_http_error(response, "wiki communities")

        except Exception as e:
            logger.error(f"Error in wiki_communities: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "wiki communities")

    # ── C3: Obsidian Export ───────────────────────────────────

    @mcp.tool()
    async def wiki_export_obsidian(
        ctx: Context,
        project_id: str,
    ) -> str:
        """
        Export wiki pages as an Obsidian vault (zip download).

        Generates a zip archive containing:
        - One .md file per wiki page with YAML frontmatter
        - [[wiki-links]] for cross-references
        - _Index.md listing all pages by type
        - Community overview files

        The exported vault can be opened directly in Obsidian for
        graph visualization and offline browsing.

        Args:
            project_id: Project UUID to export

        Returns:
            Download URL for the zip file.
        """
        try:
            api_url = get_api_url()
            return json.dumps({
                "success": True,
                "download_url": f"{api_url}/api/wiki/export/obsidian/{project_id}",
                "message": "Use the download URL to save the Obsidian vault zip file.",
            }, indent=2)
        except Exception as e:
            logger.error(f"Error in wiki_export_obsidian: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "export obsidian vault")

    @mcp.tool()
    async def wiki_quote(
        ctx: Context,
        page_id: str,
        quote: str,
        source_url: str = "",
        context: str = "",
        stale_after_days: int = 90,
    ) -> str:
        """
        Capture a verbatim quote as evidence attached to a wiki page.

        Use this to preserve exact text (error messages, API constraints,
        code review findings) with full provenance. Verbatim evidence
        achieves higher recall than summarized content.

        The quote is deduplicated by content hash — calling with the same
        quote twice will not create a duplicate.

        Args:
            page_id: UUID of the wiki page to attach evidence to
            quote: Exact verbatim text to preserve (max ~500 chars recommended)
            source_url: Where the quote came from (URL, file path, etc.)
            context: Surrounding context explaining the quote's relevance
            stale_after_days: Days before the quote is flagged for re-verification (default: 90)

        Returns:
            JSON with quote_hash, evidence_count, and quality_score.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            payload = {
                "quote": quote,
                "source_url": source_url,
                "context": context,
                "captured_by": "agent",
                "stale_after_days": stale_after_days,
            }

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    urljoin(api_url, f"/api/wiki/pages/{page_id}/evidence"),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            return json.dumps(data, indent=2)
        except Exception as e:
            logger.error(f"Error in wiki_quote: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "capture wiki evidence")

    @mcp.tool()
    async def wiki_search_evidence(
        ctx: Context,
        project_id: str,
        query: str,
        limit: int = 10,
    ) -> str:
        """
        Search verbatim evidence quotes across all wiki pages in a project.

        Returns matching quotes with their source pages. Use this when you
        need to find exact text that was previously captured.

        Args:
            project_id: Project UUID to search within
            query: Keywords to search in quote text and context
            limit: Max results (default: 10)

        Returns:
            JSON with matching pages and their evidence items.
        """
        try:
            api_url = get_api_url()
            timeout = get_default_timeout()

            params = {"project_id": project_id, "query": query, "limit": limit}

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    urljoin(api_url, "/api/wiki/evidence/search"),
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

            return json.dumps(data, indent=2)
        except Exception as e:
            logger.error(f"Error in wiki_search_evidence: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "search wiki evidence")
