"""
Reference Snowball Discovery Strategy — follow external URLs from existing wiki pages.

Extracts outbound URLs from wiki page content that aren't already in the knowledge
base, filters by allowed domains, and queues them for ingestion.

Config example:
  {"max_depth": 2, "follow_domains": ["docs.anthropic.com", "react.dev", "supabase.com"]}
"""

import json
import logging
import re
from typing import Any

from ....utils import get_supabase_client
from .base import BaseStrategy, DiscoveredItem

logger = logging.getLogger(__name__)

# Regex for extracting URLs from markdown content
URL_PATTERN = re.compile(r'https?://[^\s\)\]\>\"\'\`]+')


class SnowballStrategy(BaseStrategy):
    """Discover new sources by following links from existing wiki pages."""

    @property
    def strategy_type(self) -> str:
        return "reference_snowball"

    async def discover(
        self, config: dict[str, Any], max_items: int = 5
    ) -> list[DiscoveredItem]:
        follow_domains = config.get("follow_domains", [])
        project_id = config.get("project_id")

        if not project_id:
            logger.warning("Snowball strategy: no project_id in config")
            return []

        supabase = get_supabase_client()

        # Get active wiki pages with content
        try:
            result = (
                supabase.table("archon_wiki_pages")
                .select("id, title, content, source_ids")
                .eq("project_id", project_id)
                .eq("status", "active")
                .order("updated_at", desc=True)
                .limit(20)
                .execute()
            )
            pages = result.data or []
        except Exception as e:
            logger.warning(f"Error fetching wiki pages: {e}")
            return []

        # Get existing source URLs for dedup
        try:
            sources_result = (
                supabase.table("archon_sources")
                .select("source_url")
                .execute()
            )
            known_urls = {
                s.get("source_url", "").rstrip("/")
                for s in (sources_result.data or [])
                if s.get("source_url")
            }
        except Exception:
            known_urls = set()

        # Extract URLs from wiki content
        discovered: list[DiscoveredItem] = []
        seen_urls: set[str] = set()

        for page in pages:
            content = page.get("content", "")
            urls = URL_PATTERN.findall(content)

            for url in urls:
                # Clean trailing punctuation
                url = url.rstrip(".,;:!?)")
                normalized = url.rstrip("/")

                if normalized in known_urls or normalized in seen_urls:
                    continue

                # Filter by allowed domains
                if follow_domains:
                    domain_match = any(d in url for d in follow_domains)
                    if not domain_match:
                        continue

                seen_urls.add(normalized)
                discovered.append(DiscoveredItem(
                    url=url,
                    title=f"Referenced from: {page.get('title', 'wiki page')}",
                    snippet=f"URL found in wiki page '{page.get('title', '')}'",
                ))

                if len(discovered) >= max_items:
                    return discovered

        return discovered[:max_items]
