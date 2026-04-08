"""
Hacker News Discovery Strategy — monitor HN for high-score stories.

Config example:
  {"min_score": 100, "topics": ["ai", "typescript", "react"]}
"""

import logging
from typing import Any

import httpx

from .base import BaseStrategy, DiscoveredItem

logger = logging.getLogger(__name__)

HN_API = "https://hacker-news.firebaseio.com/v0"


class HackerNewsStrategy(BaseStrategy):
    """Discover high-score Hacker News stories."""

    @property
    def strategy_type(self) -> str:
        return "hackernews"

    async def discover(
        self, config: dict[str, Any], max_items: int = 5
    ) -> list[DiscoveredItem]:
        min_score = config.get("min_score", 50)
        topics = [t.lower() for t in config.get("topics", [])]

        items: list[DiscoveredItem] = []
        timeout = httpx.Timeout(15.0, connect=5.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                # Get top stories
                resp = await client.get(f"{HN_API}/topstories.json")
                if resp.status_code != 200:
                    return []

                story_ids = resp.json()[:50]  # Check top 50

                for story_id in story_ids:
                    if len(items) >= max_items:
                        break

                    try:
                        story_resp = await client.get(f"{HN_API}/item/{story_id}.json")
                        if story_resp.status_code != 200:
                            continue

                        story = story_resp.json()
                        if not story:
                            continue

                        score = story.get("score", 0)
                        if score < min_score:
                            continue

                        title = story.get("title", "").lower()
                        url = story.get("url", "")

                        # Filter by topics if configured
                        if topics:
                            if not any(t in title for t in topics):
                                continue

                        # Use story URL, fallback to HN comments page
                        if not url:
                            url = f"https://news.ycombinator.com/item?id={story_id}"

                        items.append(DiscoveredItem(
                            url=url,
                            title=story.get("title", ""),
                            snippet=f"HN score: {score}, {story.get('descendants', 0)} comments",
                        ))

                    except Exception:
                        continue

            except Exception as e:
                logger.warning(f"HN discovery error: {e}")

        return items[:max_items]
