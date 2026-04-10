"""
Wiki Export Service — export wiki pages to Obsidian vault format.

Generates a zip archive of markdown files with YAML frontmatter and
[[wiki-links]] for use in Obsidian or any markdown editor.
"""

import io
import json
import logging
import zipfile
from collections import defaultdict
from datetime import datetime
from typing import Any

from ...utils import get_supabase_client
from .wiki_service import WikiService

logger = logging.getLogger(__name__)


class WikiExportService:
    """Service for exporting wiki pages to various formats."""

    def __init__(self) -> None:
        self.supabase = get_supabase_client()
        self.wiki = WikiService()

    def export_obsidian_vault(self, project_id: str) -> tuple[bool, bytes | dict]:
        """Export all wiki pages as an Obsidian-compatible zip vault.

        Returns (True, zip_bytes) on success or (False, {"error": ...}) on failure.
        """
        try:
            # Fetch all pages with links
            pages_result = (
                self.supabase.table("archon_wiki_pages")
                .select("*")
                .eq("project_id", project_id)
                .neq("status", "archived")
                .order("title")
                .execute()
            )
            pages = pages_result.data or []
            if not pages:
                return False, {"error": "No wiki pages to export"}

            # Build page lookup by ID for link resolution
            page_map = {p["id"]: p for p in pages}

            # Fetch all links
            all_links = (
                self.supabase.table("archon_wiki_links")
                .select("from_page_id, to_page_id, link_type, confidence, context")
                .execute()
            )
            links = all_links.data or []

            # Build link maps
            outbound: dict[str, list[dict]] = defaultdict(list)
            inbound: dict[str, list[dict]] = defaultdict(list)
            for link in links:
                src, tgt = link["from_page_id"], link["to_page_id"]
                if src in page_map and tgt in page_map:
                    outbound[src].append(link)
                    inbound[tgt].append(link)

            # Generate zip
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                # Index file
                index_md = self._generate_index(pages)
                zf.writestr("_Index.md", index_md)

                # Individual page files
                for page in pages:
                    filename = self._safe_filename(page["title"]) + ".md"
                    content = self._generate_page_md(
                        page, page_map, outbound.get(page["id"], []), inbound.get(page["id"], [])
                    )
                    zf.writestr(filename, content)

                # Community overview files
                communities = defaultdict(list)
                for page in pages:
                    comm = page.get("community")
                    if comm:
                        communities[comm].append(page)

                for comm_name, members in communities.items():
                    filename = f"_Community - {self._safe_filename(comm_name)}.md"
                    content = self._generate_community_md(comm_name, members)
                    zf.writestr(filename, content)

            buf.seek(0)
            return True, buf.read()

        except Exception as e:
            logger.error(f"Error exporting vault: {e}", exc_info=True)
            return False, {"error": str(e)}

    def _generate_page_md(
        self,
        page: dict,
        page_map: dict[str, dict],
        outbound_links: list[dict],
        inbound_links: list[dict],
    ) -> str:
        """Generate Obsidian markdown for a single page."""
        tags = self._parse_json_field(page.get("tags", "[]"))
        source_ids = self._parse_json_field(page.get("source_ids", "[]"))

        # YAML frontmatter
        lines = ["---"]
        lines.append(f"title: \"{page['title']}\"")
        lines.append(f"type: {page.get('page_type', 'concept')}")
        if page.get("category"):
            lines.append(f"category: {page['category']}")
        if page.get("community"):
            lines.append(f"community: \"{page['community']}\"")
        if tags:
            lines.append(f"tags: [{', '.join(tags)}]")
        lines.append(f"quality: {page.get('quality_score', 0)}")
        lines.append(f"status: {page.get('status', 'draft')}")
        if page.get("created_at"):
            lines.append(f"created: {page['created_at'][:10]}")
        if page.get("updated_at"):
            lines.append(f"updated: {page['updated_at'][:10]}")
        lines.append("---")
        lines.append("")

        # Content
        content = page.get("content", "")
        if not content.startswith("#"):
            lines.append(f"# {page['title']}")
            lines.append("")
        lines.append(content)
        lines.append("")

        # Outbound links
        if outbound_links:
            lines.append("## Linked Pages")
            lines.append("")
            for link in outbound_links:
                target = page_map.get(link["to_page_id"])
                if target:
                    conf = link.get("confidence", "inferred")
                    ltype = link.get("link_type", "related")
                    lines.append(f"- [[{target['title']}]] ({ltype}, {conf})")
            lines.append("")

        # Inbound links (backlinks)
        if inbound_links:
            lines.append("## Backlinks")
            lines.append("")
            for link in inbound_links:
                source = page_map.get(link["from_page_id"])
                if source:
                    lines.append(f"- [[{source['title']}]]")
            lines.append("")

        return "\n".join(lines)

    def _generate_index(self, pages: list[dict]) -> str:
        """Generate the _Index.md file listing all pages by type."""
        lines = ["---", "title: Wiki Index", "---", "", "# Wiki Knowledge Base Index", ""]

        # Group by type
        by_type: dict[str, list[dict]] = defaultdict(list)
        for p in pages:
            by_type[p.get("page_type", "other")].append(p)

        lines.append(f"**Total: {len(pages)} pages**")
        lines.append("")

        for ptype in ["entity", "concept", "synthesis", "source_summary"]:
            items = by_type.get(ptype, [])
            if not items:
                continue
            lines.append(f"## {ptype.replace('_', ' ').title()} ({len(items)})")
            lines.append("")
            for p in sorted(items, key=lambda x: x["title"]):
                community = f" `{p['community']}`" if p.get("community") else ""
                lines.append(f"- [[{p['title']}]]{community}")
            lines.append("")

        return "\n".join(lines)

    def _generate_community_md(self, name: str, members: list[dict]) -> str:
        """Generate a community overview file."""
        lines = [
            "---",
            f"title: \"Community: {name}\"",
            "type: community-overview",
            "---",
            "",
            f"# Community: {name}",
            "",
            f"**{len(members)} pages** in this topic cluster.",
            "",
            "## Members",
            "",
        ]
        for m in sorted(members, key=lambda x: -(x.get("quality_score") or 0)):
            q = int((m.get("quality_score") or 0) * 100)
            lines.append(f"- [[{m['title']}]] ({m.get('page_type', '?')}, quality {q}%)")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _safe_filename(text: str) -> str:
        """Convert text to a safe filename (no special chars, max 80 chars)."""
        safe = "".join(c if c.isalnum() or c in " -_" else "" for c in text)
        return safe.strip()[:80] or "Untitled"

    @staticmethod
    def _parse_json_field(value: Any) -> list:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return []
        return []
