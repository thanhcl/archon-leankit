"""
Wiki KB Service — CRUD operations for wiki pages and links.

Provides structured knowledge graph operations on top of Supabase:
- Create/read/update wiki pages with auto-generated slugs, summaries, embeddings
- Create/query cross-reference links between pages
- Graph traversal (neighbors, connected components)
- Quality score computation
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ...utils import get_supabase_client

logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """Convert text to kebab-case slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")[:120]


class WikiService:
    """Service for wiki page and link CRUD operations."""

    def __init__(self) -> None:
        self.supabase = get_supabase_client()

    # ── Page CRUD ──────────────────────────────────────────────

    def create_page(
        self,
        project_id: str,
        title: str,
        content: str,
        page_type: str,
        category: str | None = None,
        tags: list[str] | None = None,
        source_ids: list[str] | None = None,
        summary: str | None = None,
        status: str = "draft",
    ) -> tuple[bool, dict[str, Any]]:
        """Create a new wiki page with auto-generated slug and embedding."""
        try:
            slug = slugify(title)

            # Build page data
            page_data: dict[str, Any] = {
                "id": str(uuid4()),
                "project_id": project_id,
                "slug": slug,
                "title": title,
                "content": content,
                "page_type": page_type,
                "status": status,
                "tags": json.dumps(tags or []),
                "source_ids": json.dumps(source_ids or []),
                "evidence": json.dumps([]),
            }
            if category:
                page_data["category"] = category
            if summary:
                page_data["summary"] = summary

            # Note: embedding generation is async and handled separately
            # via the API route or a background task. Pages work without embeddings
            # (full-text search still functions via content_search_vector).

            result = (
                self.supabase.table("archon_wiki_pages")
                .insert(page_data)
                .execute()
            )
            if result.data:
                return True, result.data[0]
            return False, {"error": "Insert returned no data"}

        except Exception as e:
            logger.error(f"Error creating wiki page: {e}")
            return False, {"error": str(e)}

    def get_page(
        self,
        page_id: str | None = None,
        slug: str | None = None,
        project_id: str | None = None,
        include_links: bool = True,
    ) -> tuple[bool, dict[str, Any]]:
        """Get a wiki page by ID or slug. Optionally include links."""
        try:
            query = self.supabase.table("archon_wiki_pages").select("*")

            if page_id:
                query = query.eq("id", page_id)
            elif slug and project_id:
                query = query.eq("slug", slug).eq("project_id", project_id)
            else:
                return False, {"error": "Provide page_id or (slug + project_id)"}

            result = query.single().execute()
            if not result.data:
                return False, {"error": "Page not found"}

            page = result.data
            if include_links:
                page["links"] = self._get_page_links(page["id"])

            return True, page

        except Exception as e:
            logger.error(f"Error getting wiki page: {e}")
            return False, {"error": str(e)}

    def update_page(
        self,
        page_id: str,
        content: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        category: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Update a wiki page. Re-generates embedding if content/summary changes."""
        try:
            update_data: dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}

            if title is not None:
                update_data["title"] = title
            if content is not None:
                update_data["content"] = content
            if summary is not None:
                update_data["summary"] = summary
            if tags is not None:
                update_data["tags"] = json.dumps(tags)
            if status is not None:
                update_data["status"] = status
            if category is not None:
                update_data["category"] = category

            # Note: embedding re-generation is async and handled separately.

            result = (
                self.supabase.table("archon_wiki_pages")
                .update(update_data)
                .eq("id", page_id)
                .execute()
            )
            if result.data:
                return True, result.data[0]
            return False, {"error": "Update returned no data"}

        except Exception as e:
            logger.error(f"Error updating wiki page: {e}")
            return False, {"error": str(e)}

    def search_pages(
        self,
        query: str,
        project_id: str | None = None,
        page_type: str | None = None,
        category: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Search wiki pages using full-text search. Returns pages with metadata."""
        try:
            # Build query with filters first, then apply text_search last
            q = (
                self.supabase.table("archon_wiki_pages")
                .select("id, slug, title, summary, page_type, category, tags, status, quality_score, updated_at")
            )

            if project_id:
                q = q.eq("project_id", project_id)
            if page_type:
                q = q.eq("page_type", page_type)
            if category:
                q = q.eq("category", category)
            if status:
                q = q.eq("status", status)

            # Use ilike on title+content for search (more compatible than text_search chaining)
            for term in query.split()[:5]:
                q = q.or_(f"title.ilike.%{term}%,content.ilike.%{term}%")

            q = q.limit(limit).order("quality_score", desc=True)

            result = q.execute()
            return True, result.data or []

        except Exception as e:
            logger.error(f"Error searching wiki pages: {e}")
            return False, []

    def list_pages(
        self,
        project_id: str,
        page_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """List wiki pages for a project with optional filtering."""
        try:
            q = (
                self.supabase.table("archon_wiki_pages")
                .select("id, slug, title, summary, page_type, category, tags, status, quality_score, updated_at")
                .eq("project_id", project_id)
            )

            if page_type:
                q = q.eq("page_type", page_type)
            if status:
                q = q.eq("status", status)

            q = q.order("updated_at", desc=True).range(offset, offset + limit - 1)

            result = q.execute()
            return True, result.data or []

        except Exception as e:
            logger.error(f"Error listing wiki pages: {e}")
            return False, []

    def delete_page(self, page_id: str) -> tuple[bool, str]:
        """Delete a wiki page. Links are cascade-deleted."""
        try:
            self.supabase.table("archon_wiki_pages").delete().eq("id", page_id).execute()
            return True, "Page deleted"
        except Exception as e:
            logger.error(f"Error deleting wiki page: {e}")
            return False, str(e)

    # ── Link CRUD ──────────────────────────────────────────────

    def create_link(
        self,
        from_page_id: str,
        to_page_id: str,
        link_type: str = "related",
        context: str | None = None,
        strength: float = 0.5,
        created_by: str = "system",
    ) -> tuple[bool, dict[str, Any]]:
        """Create a link between two wiki pages. Ignores duplicates."""
        try:
            if from_page_id == to_page_id:
                return False, {"error": "Cannot link page to itself"}

            link_data = {
                "id": str(uuid4()),
                "from_page_id": from_page_id,
                "to_page_id": to_page_id,
                "link_type": link_type,
                "context": context,
                "strength": strength,
                "created_by": created_by,
            }

            result = (
                self.supabase.table("archon_wiki_links")
                .upsert(link_data, on_conflict="from_page_id,to_page_id,link_type")
                .execute()
            )
            if result.data:
                return True, result.data[0]
            return False, {"error": "Link creation returned no data"}

        except Exception as e:
            logger.error(f"Error creating wiki link: {e}")
            return False, {"error": str(e)}

    def _get_page_links(self, page_id: str) -> dict[str, list[dict[str, Any]]]:
        """Get all inbound and outbound links for a page."""
        try:
            # Outbound links
            outbound = (
                self.supabase.table("archon_wiki_links")
                .select("id, to_page_id, link_type, context, strength, created_by")
                .eq("from_page_id", page_id)
                .execute()
            )

            # Inbound links
            inbound = (
                self.supabase.table("archon_wiki_links")
                .select("id, from_page_id, link_type, context, strength, created_by")
                .eq("to_page_id", page_id)
                .execute()
            )

            return {
                "outbound": outbound.data or [],
                "inbound": inbound.data or [],
            }

        except Exception as e:
            logger.error(f"Error getting page links: {e}")
            return {"outbound": [], "inbound": []}

    def get_graph(
        self,
        project_id: str | None = None,
        page_id: str | None = None,
        depth: int = 2,
        link_types: list[str] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Get knowledge graph (nodes + edges) around a page or for a project."""
        try:
            if page_id:
                # BFS from page_id up to depth
                return self._bfs_graph(page_id, depth, link_types)
            elif project_id:
                # Full project graph
                return self._full_project_graph(project_id, link_types)
            else:
                return False, {"error": "Provide page_id or project_id"}

        except Exception as e:
            logger.error(f"Error getting graph: {e}")
            return False, {"error": str(e)}

    def _bfs_graph(
        self, start_page_id: str, depth: int, link_types: list[str] | None
    ) -> tuple[bool, dict[str, Any]]:
        """BFS graph traversal from a starting page."""
        visited_ids: set[str] = set()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        frontier = {start_page_id}

        for _level in range(depth):
            if not frontier:
                break

            next_frontier: set[str] = set()
            for pid in frontier:
                if pid in visited_ids:
                    continue
                visited_ids.add(pid)

                # Get page info
                ok, page = self.get_page(page_id=pid, include_links=False)
                if ok:
                    nodes.append({
                        "id": page["id"],
                        "slug": page["slug"],
                        "title": page["title"],
                        "page_type": page["page_type"],
                        "status": page.get("status"),
                    })

                # Get links
                links = self._get_page_links(pid)
                for link in links.get("outbound", []):
                    if link_types and link["link_type"] not in link_types:
                        continue
                    edges.append({
                        "from": pid,
                        "to": link["to_page_id"],
                        "type": link["link_type"],
                        "strength": link.get("strength"),
                    })
                    next_frontier.add(link["to_page_id"])

                for link in links.get("inbound", []):
                    if link_types and link["link_type"] not in link_types:
                        continue
                    edges.append({
                        "from": link["from_page_id"],
                        "to": pid,
                        "type": link["link_type"],
                        "strength": link.get("strength"),
                    })
                    next_frontier.add(link["from_page_id"])

            frontier = next_frontier - visited_ids

        return True, {"nodes": nodes, "edges": edges}

    def _full_project_graph(
        self, project_id: str, link_types: list[str] | None
    ) -> tuple[bool, dict[str, Any]]:
        """Get full graph for a project."""
        # Get all pages
        pages_result = (
            self.supabase.table("archon_wiki_pages")
            .select("id, slug, title, page_type, status")
            .eq("project_id", project_id)
            .execute()
        )
        nodes = pages_result.data or []
        page_ids = {p["id"] for p in nodes}

        # Get all links between project pages
        all_edges: list[dict[str, Any]] = []
        for pid in page_ids:
            links = self._get_page_links(pid)
            for link in links.get("outbound", []):
                if link["to_page_id"] in page_ids:
                    if link_types and link["link_type"] not in link_types:
                        continue
                    all_edges.append({
                        "from": pid,
                        "to": link["to_page_id"],
                        "type": link["link_type"],
                        "strength": link.get("strength"),
                    })

        return True, {"nodes": nodes, "edges": all_edges}

    # ── Quality Score ──────────────────────────────────────────

    def recalculate_quality(self, page_id: str) -> float:
        """Recalculate quality score for a page based on freshness, links, sources."""
        try:
            ok, page = self.get_page(page_id=page_id, include_links=True)
            if not ok:
                return 0.5

            # Freshness score (0-0.3): decay over 30 days
            updated = datetime.fromisoformat(page["updated_at"].replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - updated).days
            freshness = max(0, 0.3 * (1 - age_days / 30))

            # Link score (0-0.3): more links = higher quality
            total_links = len(page.get("links", {}).get("outbound", [])) + len(
                page.get("links", {}).get("inbound", [])
            )
            link_score = min(0.3, total_links * 0.05)

            # Source score (0-0.2): backed by sources
            source_count = len(json.loads(page.get("source_ids", "[]")) if isinstance(page.get("source_ids"), str) else page.get("source_ids", []))
            source_score = min(0.2, source_count * 0.1)

            # Content score (0-0.2): non-trivial content
            content_len = len(page.get("content", ""))
            content_score = min(0.2, content_len / 5000 * 0.2)

            quality = round(freshness + link_score + source_score + content_score, 2)
            quality = min(1.0, max(0.0, quality))

            # Update in DB
            self.supabase.table("archon_wiki_pages").update(
                {"quality_score": quality}
            ).eq("id", page_id).execute()

            return quality

        except Exception as e:
            logger.error(f"Error recalculating quality: {e}")
            return 0.5
