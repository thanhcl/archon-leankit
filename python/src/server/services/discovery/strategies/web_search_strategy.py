"""
Web Search Discovery Strategy — search web for new knowledge sources.

Config example:
  {"keywords": ["React server components 2026"], "recency_days": 30, "max_results": 10}

Uses a simple search approach via HTTP. In production, could integrate
with Brave Search API, Serper, or similar.
"""

import logging
from typing import Any

import httpx

from .base import BaseStrategy, DiscoveredItem

logger = logging.getLogger(__name__)


class WebSearchStrategy(BaseStrategy):
    """Discover new content via web search."""

    @property
    def strategy_type(self) -> str:
        return "web_search"

    async def discover(
        self, config: dict[str, Any], max_items: int = 5
    ) -> list[DiscoveredItem]:
        keywords = config.get("keywords", [])
        if not keywords:
            logger.warning("Web search strategy: no keywords configured")
            return []

        items: list[DiscoveredItem] = []
        timeout = httpx.Timeout(15.0, connect=5.0)

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for query in keywords:
                try:
                    # Use DuckDuckGo HTML lite (no API key needed)
                    resp = await client.get(
                        "https://html.duckduckgo.com/html/",
                        params={"q": query},
                        headers={"User-Agent": "Archon-Discovery/1.0"},
                    )
                    if resp.status_code == 200:
                        parsed = self._parse_ddg_html(resp.text)
                        items.extend(parsed)
                except Exception as e:
                    logger.warning(f"Web search error for '{query}': {e}")

        return items[:max_items]

    def _parse_ddg_html(self, html: str) -> list[DiscoveredItem]:
        """Parse DuckDuckGo HTML results (best-effort, no heavy deps)."""
        items: list[DiscoveredItem] = []
        try:
            # Simple regex-free parsing for result links
            # DDG HTML format: <a rel="nofollow" class="result__a" href="...">title</a>
            import re
            links = re.findall(
                r'class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>',
                html,
            )
            for url, title in links:
                # DDG wraps URLs in redirect, extract actual URL
                if "uddg=" in url:
                    match = re.search(r'uddg=([^&]+)', url)
                    if match:
                        from urllib.parse import unquote
                        url = unquote(match.group(1))

                if url.startswith("http"):
                    items.append(DiscoveredItem(url=url, title=title.strip()))

        except Exception as e:
            logger.warning(f"Error parsing DDG HTML: {e}")

        return items
