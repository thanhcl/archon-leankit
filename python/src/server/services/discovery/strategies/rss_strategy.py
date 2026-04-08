"""
RSS Feed Discovery Strategy — poll RSS/Atom feeds for new entries.

Config example:
  {"urls": ["https://blog.example.com/feed.xml", "https://other.com/rss"]}
"""

import logging
from typing import Any
from xml.etree import ElementTree

import httpx

from .base import BaseStrategy, DiscoveredItem

logger = logging.getLogger(__name__)

# Common Atom/RSS namespaces
ATOM_NS = "{http://www.w3.org/2005/Atom}"


class RSSStrategy(BaseStrategy):
    """Discover new content from RSS/Atom feeds."""

    @property
    def strategy_type(self) -> str:
        return "rss"

    async def discover(
        self, config: dict[str, Any], max_items: int = 5
    ) -> list[DiscoveredItem]:
        urls = config.get("urls", [])
        if not urls:
            logger.warning("RSS strategy: no feed URLs configured")
            return []

        items: list[DiscoveredItem] = []
        timeout = httpx.Timeout(15.0, connect=5.0)

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for feed_url in urls:
                try:
                    resp = await client.get(feed_url)
                    if resp.status_code != 200:
                        logger.warning(f"RSS feed {feed_url} returned {resp.status_code}")
                        continue

                    feed_items = self._parse_feed(resp.text)
                    items.extend(feed_items)

                except Exception as e:
                    logger.warning(f"Error fetching RSS feed {feed_url}: {e}")

        return items[:max_items]

    def _parse_feed(self, xml_text: str) -> list[DiscoveredItem]:
        """Parse RSS or Atom feed XML into DiscoveredItems."""
        items: list[DiscoveredItem] = []
        try:
            root = ElementTree.fromstring(xml_text)

            # Try RSS 2.0 format
            for item in root.iter("item"):
                link = self._get_text(item, "link")
                title = self._get_text(item, "title")
                desc = self._get_text(item, "description")
                if link:
                    items.append(DiscoveredItem(
                        url=link, title=title, snippet=desc[:200] if desc else None
                    ))

            # Try Atom format if no RSS items found
            if not items:
                for entry in root.iter(f"{ATOM_NS}entry"):
                    link_el = entry.find(f"{ATOM_NS}link")
                    link = link_el.get("href") if link_el is not None else None
                    title = self._get_text(entry, f"{ATOM_NS}title")
                    summary = self._get_text(entry, f"{ATOM_NS}summary")
                    if link:
                        items.append(DiscoveredItem(
                            url=link, title=title, snippet=summary[:200] if summary else None
                        ))

        except ElementTree.ParseError as e:
            logger.warning(f"XML parse error in RSS feed: {e}")

        return items

    @staticmethod
    def _get_text(element: ElementTree.Element, tag: str) -> str | None:
        child = element.find(tag)
        return child.text.strip() if child is not None and child.text else None
