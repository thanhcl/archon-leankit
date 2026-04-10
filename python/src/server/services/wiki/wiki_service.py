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
from datetime import datetime, timedelta, timezone
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

    # ── E3: Quick Note (Write-Back Loop) ─────────────────────

    def create_quick_note(
        self,
        project_id: str,
        text: str,
        link_to_slugs: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Create a lightweight synthesis page from a quick note.

        Auto-generates title from first line. Sets status='active' immediately.
        Optionally links to existing pages by slug.
        """
        if not text or not text.strip():
            return False, {"error": "Note text cannot be empty"}

        # Extract title from first line
        first_line = text.strip().split("\n")[0]
        title = first_line.lstrip("#").strip()[:80]
        if not title:
            title = "Untitled Note"

        # Create page
        ok, page = self.create_page(
            project_id=project_id,
            title=title,
            content=text,
            page_type="synthesis",
            tags=tags,
            summary=title,
            status="active",
        )
        if not ok:
            return False, page

        # Auto-link to referenced pages
        links_created = 0
        for slug in (link_to_slugs or []):
            ok_target, target = self.get_page(
                slug=slug, project_id=project_id, include_links=False
            )
            if ok_target and target:
                ok_link, _ = self.create_link(
                    from_page_id=page["id"],
                    to_page_id=target["id"],
                    link_type="related",
                    context=f"Referenced from note: {title}",
                    confidence="inferred",
                    created_by="agent",
                )
                if ok_link:
                    links_created += 1

        page["links_created"] = links_created
        return True, page

    # ── Link CRUD ──────────────────────────────────────────────

    def create_link(
        self,
        from_page_id: str,
        to_page_id: str,
        link_type: str = "related",
        context: str | None = None,
        strength: float = 0.5,
        confidence: str = "inferred",
        created_by: str = "system",
    ) -> tuple[bool, dict[str, Any]]:
        """Create a link between two wiki pages. Ignores duplicates."""
        try:
            if from_page_id == to_page_id:
                return False, {"error": "Cannot link page to itself"}

            # Validate confidence
            if confidence not in ("extracted", "inferred", "ambiguous"):
                confidence = "inferred"

            link_data = {
                "id": str(uuid4()),
                "from_page_id": from_page_id,
                "to_page_id": to_page_id,
                "link_type": link_type,
                "context": context,
                "strength": strength,
                "confidence": confidence,
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
                .select("id, to_page_id, link_type, context, strength, confidence, created_by")
                .eq("from_page_id", page_id)
                .execute()
            )

            # Inbound links
            inbound = (
                self.supabase.table("archon_wiki_links")
                .select("id, from_page_id, link_type, context, strength, confidence, created_by")
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
        follow_tunnels: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """Get knowledge graph (nodes + edges) around a page or for a project.

        Args:
            follow_tunnels: When True, also follow approved cross-project
                tunnels during BFS traversal (depth limited to 1 for tunnels).
        """
        try:
            if page_id:
                # BFS from page_id up to depth
                return self._bfs_graph(page_id, depth, link_types, follow_tunnels=follow_tunnels)
            elif project_id:
                # Full project graph
                return self._full_project_graph(project_id, link_types)
            else:
                return False, {"error": "Provide page_id or project_id"}

        except Exception as e:
            logger.error(f"Error getting graph: {e}")
            return False, {"error": str(e)}

    def _bfs_graph(
        self, start_page_id: str, depth: int, link_types: list[str] | None,
        follow_tunnels: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """BFS graph traversal from a starting page."""
        visited_ids: set[str] = set()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        frontier = {start_page_id}

        for level in range(depth):
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

                # Follow cross-project tunnels (depth-1 only)
                if follow_tunnels and level == 0:
                    tunnel_edges = self._get_active_tunnels_for_page(pid)
                    for te in tunnel_edges:
                        target = te["to_page_id"] if te["from_page_id"] == pid else te["from_page_id"]
                        edges.append({
                            "from": te["from_page_id"],
                            "to": te["to_page_id"],
                            "type": te["tunnel_type"],
                            "is_tunnel": True,
                            "tunnel_id": te["id"],
                        })
                        next_frontier.add(target)

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

    # ── Verbatim Evidence (MemPalace-inspired) ───────────────────

    MAX_EVIDENCE_PER_PAGE = 50

    def add_evidence(
        self,
        page_id: str,
        quote: str,
        source_url: str = "",
        source_page_id: str = "",
        context: str = "",
        captured_by: str = "agent",
        stale_after_days: int = 90,
    ) -> tuple[bool, dict[str, Any]]:
        """Append a verbatim evidence item to a wiki page.

        Deduplicates by quote hash. Evicts oldest items when the per-page
        cap (MAX_EVIDENCE_PER_PAGE) is reached. Bumps quality_score.
        """
        try:
            normalized = re.sub(r"\s+", " ", quote.strip().lower())
            quote_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]

            # Read current evidence
            resp = (
                self.supabase.table("archon_wiki_pages")
                .select("evidence,quality_score")
                .eq("id", page_id)
                .execute()
            )
            if not resp.data:
                return False, {"error": f"Page {page_id} not found"}

            raw_evidence = resp.data[0].get("evidence") or "[]"
            evidence_list: list[dict[str, Any]] = (
                json.loads(raw_evidence) if isinstance(raw_evidence, str) else raw_evidence
            )

            # Dedup check
            for item in evidence_list:
                if item.get("quote_hash") == quote_hash:
                    return True, {"message": "Duplicate quote — already stored", "quote_hash": quote_hash}

            # Build evidence item
            now = datetime.now(timezone.utc).isoformat()
            evidence_item = {
                "quote": quote.strip(),
                "quote_hash": quote_hash,
                "source_url": source_url,
                "source_page_id": source_page_id,
                "captured_at": now,
                "captured_by": captured_by,
                "context": context[:500] if context else "",
                "stale_after_days": stale_after_days,
                "verified_at": None,
            }

            evidence_list.append(evidence_item)

            # Evict oldest if over cap
            if len(evidence_list) > self.MAX_EVIDENCE_PER_PAGE:
                evidence_list.sort(key=lambda x: x.get("captured_at", ""))
                evidence_list = evidence_list[-self.MAX_EVIDENCE_PER_PAGE:]

            # Write back
            current_quality = resp.data[0].get("quality_score") or 0.5
            # Bump quality slightly for having evidence (capped at 1.0)
            new_quality = min(1.0, current_quality + 0.02)

            self.supabase.table("archon_wiki_pages").update({
                "evidence": json.dumps(evidence_list),
                "quality_score": new_quality,
                "updated_at": now,
            }).eq("id", page_id).execute()

            return True, {
                "quote_hash": quote_hash,
                "evidence_count": len(evidence_list),
                "quality_score": new_quality,
            }

        except Exception as e:
            logger.error(f"Error adding evidence to page {page_id}: {e}")
            return False, {"error": str(e)}

    def search_evidence(
        self,
        project_id: str,
        query: str,
        limit: int = 10,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Search verbatim evidence quotes across all pages in a project.

        Returns page metadata plus matching evidence items.
        """
        try:
            query_lower = query.lower().strip()
            if not query_lower:
                return True, []

            # Fetch pages with non-empty evidence
            resp = (
                self.supabase.table("archon_wiki_pages")
                .select("id,title,slug,evidence")
                .eq("project_id", project_id)
                .neq("evidence", "[]")
                .execute()
            )
            pages = resp.data or []

            matches: list[dict[str, Any]] = []
            query_terms = set(query_lower.split())

            for page in pages:
                raw = page.get("evidence") or "[]"
                evidence_list = json.loads(raw) if isinstance(raw, str) else raw

                matching_evidence = []
                for item in evidence_list:
                    quote_text = (item.get("quote") or "").lower()
                    context_text = (item.get("context") or "").lower()
                    combined = f"{quote_text} {context_text}"

                    # Match if any query term appears in quote or context
                    if any(term in combined for term in query_terms):
                        matching_evidence.append(item)

                if matching_evidence:
                    matches.append({
                        "page_id": page["id"],
                        "page_title": page.get("title", ""),
                        "page_slug": page.get("slug", ""),
                        "matching_evidence": matching_evidence[:5],  # Limit per page
                    })

                if len(matches) >= limit:
                    break

            return True, matches

        except Exception as e:
            logger.error(f"Error searching evidence: {e}")
            return False, []

    def get_stale_evidence(
        self,
        project_id: str,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Find evidence items past their staleness threshold.

        Returns pages with stale evidence for re-verification queue.
        """
        try:
            resp = (
                self.supabase.table("archon_wiki_pages")
                .select("id,title,slug,evidence")
                .eq("project_id", project_id)
                .neq("evidence", "[]")
                .execute()
            )
            pages = resp.data or []
            now = datetime.now(timezone.utc)

            stale_results: list[dict[str, Any]] = []

            for page in pages:
                raw = page.get("evidence") or "[]"
                evidence_list = json.loads(raw) if isinstance(raw, str) else raw

                stale_items = []
                for item in evidence_list:
                    captured_at_str = item.get("captured_at")
                    if not captured_at_str:
                        continue

                    captured_at = datetime.fromisoformat(
                        captured_at_str.replace("Z", "+00:00")
                    )
                    stale_days = item.get("stale_after_days", 90)
                    expiry = captured_at + timedelta(days=stale_days)

                    # Check if verified_at is also past threshold
                    verified_at_str = item.get("verified_at")
                    if verified_at_str:
                        verified_at = datetime.fromisoformat(
                            verified_at_str.replace("Z", "+00:00")
                        )
                        expiry = verified_at + timedelta(days=stale_days)

                    if now > expiry:
                        stale_items.append(item)

                if stale_items:
                    stale_results.append({
                        "page_id": page["id"],
                        "page_title": page.get("title", ""),
                        "stale_count": len(stale_items),
                        "stale_evidence": stale_items,
                    })

            return True, stale_results

        except Exception as e:
            logger.error(f"Error getting stale evidence: {e}")
            return False, []

    # ── Cross-Project Knowledge Tunnels (MemPalace-inspired) ─────

    TUNNEL_TABLE = "archon_wiki_tunnels"
    VALID_TUNNEL_TYPES = {"reference", "extends", "consumes", "publishes"}
    VALID_VISIBILITY = {"read_only", "bidirectional"}

    def create_tunnel(
        self,
        from_page_id: str,
        to_page_id: str,
        tunnel_type: str = "reference",
        visibility: str = "read_only",
        context: str = "",
        created_by: str = "system",
    ) -> tuple[bool, dict[str, Any]]:
        """Create a cross-project knowledge tunnel (pending approval).

        The tunnel is created without approval — TeamLead must call
        approve_tunnel() before it becomes active in graph traversal.
        """
        if tunnel_type not in self.VALID_TUNNEL_TYPES:
            return False, {"error": f"Invalid tunnel_type: {tunnel_type}"}
        if visibility not in self.VALID_VISIBILITY:
            return False, {"error": f"Invalid visibility: {visibility}"}
        if from_page_id == to_page_id:
            return False, {"error": "Cannot tunnel a page to itself"}

        try:
            tunnel_data = {
                "id": str(uuid4()),
                "from_page_id": from_page_id,
                "to_page_id": to_page_id,
                "tunnel_type": tunnel_type,
                "visibility": visibility,
                "context": context[:500] if context else "",
                "created_by": created_by,
            }

            result = (
                self.supabase.table(self.TUNNEL_TABLE)
                .insert(tunnel_data)
                .execute()
            )

            if result.data:
                return True, result.data[0]
            return False, {"error": "Insert returned no data"}

        except Exception as e:
            error_str = str(e)
            if "uq_wiki_tunnel" in error_str or "duplicate" in error_str.lower():
                return False, {"error": "Duplicate tunnel already exists"}
            logger.error(f"Error creating tunnel: {e}")
            return False, {"error": error_str}

    def approve_tunnel(
        self,
        tunnel_id: str,
        approved_by: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Approve a pending tunnel (TeamLead action)."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            result = (
                self.supabase.table(self.TUNNEL_TABLE)
                .update({"approved_by": approved_by, "approved_at": now})
                .eq("id", tunnel_id)
                .is_("revoked_at", "null")
                .execute()
            )
            if result.data:
                return True, result.data[0]
            return False, {"error": f"Tunnel {tunnel_id} not found or already revoked"}
        except Exception as e:
            logger.error(f"Error approving tunnel: {e}")
            return False, {"error": str(e)}

    def revoke_tunnel(
        self,
        tunnel_id: str,
    ) -> tuple[bool, str]:
        """Soft-revoke a tunnel (sets revoked_at, preserves audit trail)."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            result = (
                self.supabase.table(self.TUNNEL_TABLE)
                .update({"revoked_at": now})
                .eq("id", tunnel_id)
                .is_("revoked_at", "null")
                .execute()
            )
            if result.data:
                return True, "Tunnel revoked"
            return False, f"Tunnel {tunnel_id} not found or already revoked"
        except Exception as e:
            logger.error(f"Error revoking tunnel: {e}")
            return False, str(e)

    def list_tunnels(
        self,
        page_id: str | None = None,
        project_id: str | None = None,
        tunnel_type: str | None = None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """List active (non-revoked) tunnels for a page or project."""
        try:
            query = (
                self.supabase.table(self.TUNNEL_TABLE)
                .select("*")
                .is_("revoked_at", "null")
            )

            if tunnel_type:
                query = query.eq("tunnel_type", tunnel_type)

            if page_id:
                # Tunnels where this page is either endpoint
                # Supabase doesn't support OR directly — fetch both directions
                from_result = query.eq("from_page_id", page_id).execute()
                to_query = (
                    self.supabase.table(self.TUNNEL_TABLE)
                    .select("*")
                    .is_("revoked_at", "null")
                    .eq("to_page_id", page_id)
                )
                if tunnel_type:
                    to_query = to_query.eq("tunnel_type", tunnel_type)
                to_result = to_query.execute()

                combined = (from_result.data or []) + (to_result.data or [])
                # Dedup by id
                seen = set()
                tunnels = []
                for t in combined:
                    if t["id"] not in seen:
                        seen.add(t["id"])
                        tunnels.append(t)
                return True, tunnels

            elif project_id:
                # Get all page ids for the project, then find tunnels involving them
                pages_resp = (
                    self.supabase.table("archon_wiki_pages")
                    .select("id")
                    .eq("project_id", project_id)
                    .execute()
                )
                page_ids = [p["id"] for p in (pages_resp.data or [])]
                if not page_ids:
                    return True, []

                all_tunnels: list[dict[str, Any]] = []
                for pid in page_ids:
                    ok, tunnels = self.list_tunnels(page_id=pid, tunnel_type=tunnel_type)
                    if ok:
                        all_tunnels.extend(tunnels)

                # Dedup
                seen = set()
                unique = []
                for t in all_tunnels:
                    if t["id"] not in seen:
                        seen.add(t["id"])
                        unique.append(t)
                return True, unique

            else:
                result = query.limit(100).execute()
                return True, result.data or []

        except Exception as e:
            logger.error(f"Error listing tunnels: {e}")
            return False, []

    def _get_active_tunnels_for_page(
        self, page_id: str,
    ) -> list[dict[str, Any]]:
        """Get active (approved + non-revoked) tunnels for a page.

        Used internally by BFS graph traversal.
        """
        try:
            # From this page
            from_resp = (
                self.supabase.table(self.TUNNEL_TABLE)
                .select("*")
                .eq("from_page_id", page_id)
                .not_.is_("approved_at", "null")
                .is_("revoked_at", "null")
                .execute()
            )
            # To this page
            to_resp = (
                self.supabase.table(self.TUNNEL_TABLE)
                .select("*")
                .eq("to_page_id", page_id)
                .not_.is_("approved_at", "null")
                .is_("revoked_at", "null")
                .execute()
            )
            return (from_resp.data or []) + (to_resp.data or [])
        except Exception as e:
            logger.warning(f"Error fetching tunnels for page {page_id}: {e}")
            return []

    def search_tunneled_pages(
        self,
        project_id: str,
        query: str,
        limit: int = 5,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Search pages in OTHER projects that are tunneled to this project.

        Used by the prompt builder to discover cross-project knowledge.
        """
        try:
            # 1. Get page ids for this project
            pages_resp = (
                self.supabase.table("archon_wiki_pages")
                .select("id")
                .eq("project_id", project_id)
                .execute()
            )
            local_page_ids = {p["id"] for p in (pages_resp.data or [])}
            if not local_page_ids:
                return True, []

            # 2. Find active tunnels pointing TO our pages from other projects
            remote_page_ids: set[str] = set()
            for pid in local_page_ids:
                tunnels = self._get_active_tunnels_for_page(pid)
                for t in tunnels:
                    # Get the "other" end of the tunnel
                    other = t["from_page_id"] if t["to_page_id"] == pid else t["to_page_id"]
                    if other not in local_page_ids:
                        remote_page_ids.add(other)

            if not remote_page_ids:
                return True, []

            # 3. Search those remote pages by query
            query_lower = query.lower().strip()
            results: list[dict[str, Any]] = []
            for rpid in remote_page_ids:
                ok, page = self.get_page(page_id=rpid, include_links=False)
                if not ok:
                    continue
                content = (page.get("content") or "").lower()
                title = (page.get("title") or "").lower()
                if query_lower in content or query_lower in title:
                    results.append({
                        "page_id": page["id"],
                        "title": page.get("title", ""),
                        "summary": (page.get("summary") or "")[:200],
                        "project_id": page.get("project_id"),
                        "is_cross_project": True,
                    })
                    if len(results) >= limit:
                        break

            return True, results

        except Exception as e:
            logger.error(f"Error searching tunneled pages: {e}")
            return False, []
