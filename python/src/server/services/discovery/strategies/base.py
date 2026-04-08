"""
Base discovery strategy — interface for all strategy implementations.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredItem:
    """A single discovered URL with metadata."""
    url: str
    title: str | None = None
    snippet: str | None = None


class BaseStrategy(ABC):
    """Abstract base for all discovery strategies."""

    @property
    @abstractmethod
    def strategy_type(self) -> str:
        """Return the feed_type this strategy handles."""
        ...

    @abstractmethod
    async def discover(
        self, config: dict[str, Any], max_items: int = 5
    ) -> list[DiscoveredItem]:
        """
        Discover new URLs from the configured source.

        Args:
            config: Feed-specific configuration from archon_discovery_feeds.config
            max_items: Maximum items to return per poll

        Returns:
            List of discovered items (URL + metadata). Dedup happens upstream.
        """
        ...
