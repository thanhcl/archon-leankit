"""
GitHub Discovery Strategies — trending repos and repo watching.

github_trending config:
  {"languages": ["typescript", "python"], "min_stars": 100, "topics": ["ai", "llm"]}

github_repo config:
  {"owner": "anthropics", "repos": ["claude-code"], "watch": "releases"}
"""

import logging
from typing import Any

import httpx

from .base import BaseStrategy, DiscoveredItem

logger = logging.getLogger(__name__)


class GitHubTrendingStrategy(BaseStrategy):
    """Discover trending GitHub repositories."""

    @property
    def strategy_type(self) -> str:
        return "github_trending"

    async def discover(
        self, config: dict[str, Any], max_items: int = 5
    ) -> list[DiscoveredItem]:
        languages = config.get("languages", [])
        min_stars = config.get("min_stars", 50)
        topics = config.get("topics", [])

        items: list[DiscoveredItem] = []
        timeout = httpx.Timeout(15.0, connect=5.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            # Use GitHub search API (works without auth for low-volume)
            for lang in (languages or [""]):
                try:
                    query_parts = [f"stars:>={min_stars}"]
                    if lang:
                        query_parts.append(f"language:{lang}")
                    if topics:
                        for topic in topics[:2]:
                            query_parts.append(f"topic:{topic}")

                    query = " ".join(query_parts)
                    resp = await client.get(
                        "https://api.github.com/search/repositories",
                        params={"q": query, "sort": "updated", "per_page": max_items},
                        headers={
                            "Accept": "application/vnd.github+json",
                            "User-Agent": "Archon-Discovery/1.0",
                        },
                    )

                    if resp.status_code == 200:
                        data = resp.json()
                        for repo in data.get("items", []):
                            items.append(DiscoveredItem(
                                url=repo.get("html_url", ""),
                                title=repo.get("full_name", ""),
                                snippet=repo.get("description", ""),
                            ))
                    elif resp.status_code == 403:
                        logger.warning("GitHub API rate limited")
                        break
                    else:
                        logger.warning(f"GitHub API returned {resp.status_code}")

                except Exception as e:
                    logger.warning(f"GitHub trending error: {e}")

        return items[:max_items]


class GitHubRepoStrategy(BaseStrategy):
    """Watch specific GitHub repos for new content."""

    @property
    def strategy_type(self) -> str:
        return "github_repo"

    async def discover(
        self, config: dict[str, Any], max_items: int = 5
    ) -> list[DiscoveredItem]:
        owner = config.get("owner", "")
        repos = config.get("repos", [])
        watch = config.get("watch", "releases")  # releases | readme | commits

        if not owner or not repos:
            return []

        items: list[DiscoveredItem] = []
        timeout = httpx.Timeout(15.0, connect=5.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            for repo_name in repos:
                try:
                    full_name = f"{owner}/{repo_name}"

                    if watch == "releases":
                        resp = await client.get(
                            f"https://api.github.com/repos/{full_name}/releases",
                            params={"per_page": 3},
                            headers={
                                "Accept": "application/vnd.github+json",
                                "User-Agent": "Archon-Discovery/1.0",
                            },
                        )
                        if resp.status_code == 200:
                            for release in resp.json():
                                items.append(DiscoveredItem(
                                    url=release.get("html_url", ""),
                                    title=f"{full_name} {release.get('tag_name', '')}",
                                    snippet=release.get("body", "")[:200],
                                ))

                    elif watch == "readme":
                        # Discover the README URL for crawling
                        items.append(DiscoveredItem(
                            url=f"https://github.com/{full_name}",
                            title=f"{full_name} README",
                            snippet=f"README for {full_name}",
                        ))

                except Exception as e:
                    logger.warning(f"GitHub repo watch error for {owner}/{repo_name}: {e}")

        return items[:max_items]
