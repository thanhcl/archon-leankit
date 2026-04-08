"""
Discovery strategies — pluggable source finders.

Registry maps feed_type -> strategy class for the discovery pipeline.
"""

from .base import BaseStrategy, DiscoveredItem
from .gap_fill_strategy import GapFillStrategy
from .github_strategy import GitHubRepoStrategy, GitHubTrendingStrategy
from .hackernews_strategy import HackerNewsStrategy
from .reddit_strategy import RedditStrategy
from .rss_strategy import RSSStrategy
from .snowball_strategy import SnowballStrategy
from .web_search_strategy import WebSearchStrategy

# Strategy registry: feed_type -> strategy instance
STRATEGY_REGISTRY: dict[str, BaseStrategy] = {
    "gap_fill": GapFillStrategy(),
    "github_trending": GitHubTrendingStrategy(),
    "github_repo": GitHubRepoStrategy(),
    "reddit": RedditStrategy(),
    "rss": RSSStrategy(),
    "hackernews": HackerNewsStrategy(),
    "web_search": WebSearchStrategy(),
    "reference_snowball": SnowballStrategy(),
}


def get_strategy(feed_type: str) -> BaseStrategy | None:
    """Get the strategy for a given feed type."""
    return STRATEGY_REGISTRY.get(feed_type)


__all__ = [
    "BaseStrategy",
    "DiscoveredItem",
    "STRATEGY_REGISTRY",
    "get_strategy",
]
