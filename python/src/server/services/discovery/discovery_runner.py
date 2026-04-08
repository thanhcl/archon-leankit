"""
Scheduled Discovery Runner — orchestrates the full discover → ingest → lint cycle.

This service is called by a background scheduler (or MCP tool) to:
1. Find feeds that are due for polling
2. Run the appropriate strategy for each feed
3. Record discovered items (with dedup)
4. Queue new items for RAG ingestion
5. After ingestion, run wiki ingest to create pages
6. Run lint to detect new gaps
7. Repeat (max 2 iterations for gap-filling)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ...utils import get_supabase_client
from ..wiki.wiki_ingest_service import WikiIngestService
from ..wiki.wiki_lint_service import WikiLintService
from .discovery_pipeline import DiscoveryPipeline
from .strategies import get_strategy

logger = logging.getLogger(__name__)


class DiscoveryRunner:
    """Orchestrates the full discovery cycle."""

    def __init__(self) -> None:
        self.pipeline = DiscoveryPipeline()
        self.wiki_ingest = WikiIngestService()
        self.wiki_lint = WikiLintService()
        self.supabase = get_supabase_client()

    async def run_cycle(
        self,
        project_id: str | None = None,
        feed_id: str | None = None,
        max_iterations: int = 2,
    ) -> dict[str, Any]:
        """
        Run full discovery cycle: discover → ingest → lint.

        Args:
            project_id: Scope to a specific project
            feed_id: Run only a specific feed (skip due-check)
            max_iterations: Max discover→lint loops (default 2, for gap-filling)

        Returns:
            Summary of the cycle: feeds polled, items discovered, pages created, gaps found.
        """
        result = {
            "feeds_polled": 0,
            "items_discovered": 0,
            "items_ingested": 0,
            "pages_created": 0,
            "links_created": 0,
            "gaps_found": 0,
            "iterations": 0,
            "errors": [],
        }

        for iteration in range(max_iterations):
            result["iterations"] = iteration + 1
            iter_discovered = 0

            # Phase 1: Discover
            if feed_id:
                feeds = self._get_specific_feed(feed_id)
            else:
                feeds = self.pipeline.get_due_feeds(project_id)

            if not feeds and iteration == 0:
                logger.info("No feeds due for polling")
                break

            for feed in feeds:
                try:
                    discovered = await self._run_feed(feed)
                    result["feeds_polled"] += 1
                    result["items_discovered"] += len(discovered)
                    iter_discovered += len(discovered)

                    # Mark feed as polled
                    self.pipeline.mark_feed_polled(feed["id"])
                    self.pipeline.increment_feed_stats(
                        feed["id"], discovered=len(discovered)
                    )

                except Exception as e:
                    error_msg = f"Feed {feed.get('name', '?')}: {e}"
                    logger.error(error_msg)
                    result["errors"].append(error_msg)

            # Phase 2: Ingest queued items
            ingest_result = await self._ingest_queued(project_id)
            result["items_ingested"] += ingest_result.get("ingested", 0)
            result["pages_created"] += ingest_result.get("pages", 0)
            result["links_created"] += ingest_result.get("links", 0)

            # Phase 3: Lint (only on first iteration)
            if iteration == 0 and project_id:
                ok, lint_report = self.wiki_lint.lint(project_id)
                if ok:
                    result["gaps_found"] = lint_report.get("total_issues", 0)

            # Stop if no new items discovered (gap-fill didn't find anything)
            if iter_discovered == 0:
                break

            # On second iteration, only process gap_fill feeds
            if feed_id:
                break  # Single feed run, don't iterate

        return result

    async def _run_feed(self, feed: dict) -> list[dict]:
        """Run a single feed's strategy and record discoveries."""
        feed_type = feed.get("feed_type", "")
        config = feed.get("config", {})
        if isinstance(config, str):
            config = json.loads(config)

        # Inject project_id for strategies that need it
        config["project_id"] = feed.get("project_id")

        max_items = feed.get("max_items_per_poll", 5)

        strategy = get_strategy(feed_type)
        if not strategy:
            logger.warning(f"No strategy for feed type: {feed_type}")
            return []

        # Discover
        items = await strategy.discover(config, max_items=max_items)
        recorded: list[dict] = []

        for item in items:
            ok, record = self.pipeline.record_discovery(
                project_id=feed.get("project_id", ""),
                url=item.url,
                title=item.title,
                snippet=item.snippet,
                feed_id=feed.get("id"),
                status="queued",
            )
            if ok and isinstance(record, dict):
                recorded.append(record)

        return recorded

    async def _ingest_queued(self, project_id: str | None) -> dict[str, int]:
        """Ingest all queued discovery items into RAG + wiki."""
        counts = {"ingested": 0, "pages": 0, "links": 0}

        ok, queued_items = self.pipeline.get_history(
            project_id=project_id, status="queued", limit=20
        )
        if not ok or not queued_items:
            return counts

        for item in queued_items:
            try:
                # Update status to ingesting
                self.pipeline.update_history_status(item["id"], "ingesting")

                # Try to crawl via the existing crawl endpoint
                url = item.get("url", "")
                item_project_id = item.get("project_id", project_id)

                # Attempt RAG ingestion via API
                import httpx
                from ...config.service_discovery import get_api_url

                api_url = get_api_url()
                timeout = httpx.Timeout(120.0, connect=5.0)

                async with httpx.AsyncClient(timeout=timeout) as client:
                    crawl_resp = await client.post(
                        f"{api_url}/api/knowledge-items/crawl",
                        json={
                            "urls": [url],
                            "max_depth": 1,
                            "include_code": True,
                        },
                    )

                    if crawl_resp.status_code == 200:
                        crawl_data = crawl_resp.json()
                        source_id = crawl_data.get("source_id") or crawl_data.get("sources", [{}])[0].get("source_id") if crawl_data.get("sources") else None

                        if source_id and item_project_id:
                            # Wiki ingest
                            ingest_ok, ingest_result = await self.wiki_ingest.ingest_source(
                                source_id=source_id,
                                project_id=item_project_id,
                            )
                            if ingest_ok:
                                counts["pages"] += ingest_result.get("pages_created", 0)
                                counts["links"] += ingest_result.get("links_created", 0)

                                # Update history
                                self.pipeline.update_history_status(
                                    item["id"],
                                    "ingested",
                                    source_id=source_id,
                                    wiki_page_ids=ingest_result.get("page_ids", []),
                                )
                                counts["ingested"] += 1
                                continue

                        # Crawled but no wiki pages
                        self.pipeline.update_history_status(
                            item["id"], "ingested", source_id=source_id
                        )
                        counts["ingested"] += 1

                    else:
                        self.pipeline.update_history_status(
                            item["id"], "failed",
                            error_message=f"Crawl returned {crawl_resp.status_code}",
                        )

            except Exception as e:
                logger.error(f"Error ingesting {item.get('url', '?')}: {e}")
                self.pipeline.update_history_status(
                    item["id"], "failed", error_message=str(e)[:500]
                )

        return counts

    def _get_specific_feed(self, feed_id: str) -> list[dict]:
        """Get a specific feed by ID (ignoring due-check)."""
        try:
            result = (
                self.supabase.table("archon_discovery_feeds")
                .select("*")
                .eq("id", feed_id)
                .execute()
            )
            return result.data or []
        except Exception:
            return []
