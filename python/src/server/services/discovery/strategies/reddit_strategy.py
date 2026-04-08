"""
Reddit Discovery Strategy — monitor subreddits for useful content.

Config example:
  {"subreddits": ["reactjs", "typescript"], "search_terms": ["best practices"], "min_upvotes": 50}
"""

import logging
from typing import Any

import httpx

from .base import BaseStrategy, DiscoveredItem

logger = logging.getLogger(__name__)


class RedditStrategy(BaseStrategy):
    """Discover content from Reddit subreddits."""

    @property
    def strategy_type(self) -> str:
        return "reddit"

    async def discover(
        self, config: dict[str, Any], max_items: int = 5
    ) -> list[DiscoveredItem]:
        subreddits = config.get("subreddits", [])
        search_terms = config.get("search_terms", [])
        min_upvotes = config.get("min_upvotes", 10)

        if not subreddits:
            logger.warning("Reddit strategy: no subreddits configured")
            return []

        items: list[DiscoveredItem] = []
        timeout = httpx.Timeout(15.0, connect=5.0)
        headers = {"User-Agent": "Archon-Discovery/1.0 (knowledge-base)"}

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for sub in subreddits:
                try:
                    if search_terms:
                        # Search within subreddit
                        for term in search_terms[:2]:
                            resp = await client.get(
                                f"https://www.reddit.com/r/{sub}/search.json",
                                params={
                                    "q": term,
                                    "restrict_sr": "on",
                                    "sort": "relevance",
                                    "t": "month",
                                    "limit": max_items,
                                },
                                headers=headers,
                            )
                            if resp.status_code == 200:
                                items.extend(self._parse_listing(resp.json(), min_upvotes))
                    else:
                        # Get hot/top posts
                        resp = await client.get(
                            f"https://www.reddit.com/r/{sub}/hot.json",
                            params={"limit": max_items * 2},
                            headers=headers,
                        )
                        if resp.status_code == 200:
                            items.extend(self._parse_listing(resp.json(), min_upvotes))

                except Exception as e:
                    logger.warning(f"Reddit error for r/{sub}: {e}")

        return items[:max_items]

    def _parse_listing(
        self, data: dict, min_upvotes: int
    ) -> list[DiscoveredItem]:
        """Parse Reddit JSON listing into DiscoveredItems."""
        items: list[DiscoveredItem] = []
        try:
            children = data.get("data", {}).get("children", [])
            for child in children:
                post = child.get("data", {})
                ups = post.get("ups", 0)
                if ups < min_upvotes:
                    continue

                # Prefer the linked URL for link posts, otherwise use the Reddit permalink
                url = post.get("url", "")
                if post.get("is_self", False) or not url or "reddit.com" in url:
                    url = f"https://www.reddit.com{post.get('permalink', '')}"

                title = post.get("title", "")
                selftext = post.get("selftext", "")[:200]

                if url and title:
                    items.append(DiscoveredItem(
                        url=url,
                        title=f"[r/{post.get('subreddit', '?')}] {title}",
                        snippet=selftext or f"{ups} upvotes",
                    ))

        except Exception as e:
            logger.warning(f"Error parsing Reddit listing: {e}")

        return items
