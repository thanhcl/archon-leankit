"""
Gap-Fill Discovery Strategy — search the web to fill identified knowledge gaps.

This is the highest-priority strategy: it reads open gaps from
archon_knowledge_gaps and searches for content to fill them.

Config example:
  {} (no config needed — reads gaps from DB)
"""

import logging
from typing import Any

import httpx

from ....utils import get_supabase_client
from .base import BaseStrategy, DiscoveredItem

logger = logging.getLogger(__name__)


class GapFillStrategy(BaseStrategy):
    """Fill knowledge gaps by searching the web for relevant content."""

    @property
    def strategy_type(self) -> str:
        return "gap_fill"

    async def discover(
        self, config: dict[str, Any], max_items: int = 5
    ) -> list[DiscoveredItem]:
        """Search for content that fills identified knowledge gaps."""
        supabase = get_supabase_client()
        project_id = config.get("project_id")

        if not project_id:
            logger.warning("Gap-fill strategy: no project_id in config")
            return []

        # Get open gaps ordered by priority
        try:
            result = (
                supabase.table("archon_knowledge_gaps")
                .select("id, topic, description, gap_type, priority")
                .eq("project_id", project_id)
                .eq("status", "open")
                .order("priority", desc=True)
                .limit(3)
                .execute()
            )
            gaps = result.data or []
        except Exception as e:
            logger.warning(f"Error fetching gaps: {e}")
            return []

        if not gaps:
            return []

        items: list[DiscoveredItem] = []
        timeout = httpx.Timeout(15.0, connect=5.0)

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for gap in gaps:
                topic = gap.get("topic", "")
                description = gap.get("description", "")

                # Build search query from gap topic
                search_query = f"{topic} documentation tutorial guide"

                try:
                    resp = await client.get(
                        "https://html.duckduckgo.com/html/",
                        params={"q": search_query},
                        headers={"User-Agent": "Archon-Discovery/1.0"},
                    )
                    if resp.status_code == 200:
                        parsed = self._parse_ddg_html(resp.text)
                        for item in parsed[:2]:
                            item.snippet = f"[Gap: {topic}] {item.snippet or description[:100]}"
                            items.append(item)

                        # Mark gap as discovery_queued
                        try:
                            supabase.table("archon_knowledge_gaps").update(
                                {"status": "discovery_queued"}
                            ).eq("id", gap["id"]).execute()
                        except Exception:
                            pass

                except Exception as e:
                    logger.warning(f"Gap-fill search error for '{topic}': {e}")

        return items[:max_items]

    def _parse_ddg_html(self, html: str) -> list[DiscoveredItem]:
        """Parse DuckDuckGo HTML results."""
        items: list[DiscoveredItem] = []
        try:
            import re
            from urllib.parse import unquote

            links = re.findall(
                r'class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>',
                html,
            )
            for url, title in links:
                if "uddg=" in url:
                    match = re.search(r'uddg=([^&]+)', url)
                    if match:
                        url = unquote(match.group(1))
                if url.startswith("http"):
                    items.append(DiscoveredItem(url=url, title=title.strip()))
        except Exception as e:
            logger.warning(f"Error parsing DDG HTML: {e}")
        return items
