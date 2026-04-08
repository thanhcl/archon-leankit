"""
Discovery Pipeline — Orchestrates discover → ingest → lint cycle.

Coordinates all 6 discovery strategies and the wiki ingest process:
1. Gap-filling from lint reports
2. Reddit scan
3. GitHub trending
4. RSS feed polling
5. Web search by keywords
6. Reference snowballing
"""

import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4

from ...utils import get_supabase_client

logger = logging.getLogger(__name__)


def url_hash(url: str) -> str:
    """Compute SHA256 hash of URL for dedup."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class DiscoveryPipeline:
    """Orchestrator for the full discover → ingest → lint cycle."""

    def __init__(self) -> None:
        self.supabase = get_supabase_client()

    # ── Feed Management ───────────────────────────────────────

    def add_feed(
        self,
        project_id: str,
        feed_type: str,
        name: str,
        config: dict[str, Any],
        poll_interval_hours: int = 24,
        max_items_per_poll: int = 5,
    ) -> tuple[bool, dict[str, Any]]:
        """Add a new discovery feed."""
        try:
            feed_data = {
                "id": str(uuid4()),
                "project_id": project_id,
                "feed_type": feed_type,
                "name": name,
                "config": json.dumps(config),
                "poll_interval_hours": poll_interval_hours,
                "max_items_per_poll": max_items_per_poll,
                "next_poll_at": datetime.now(timezone.utc).isoformat(),
            }

            result = (
                self.supabase.table("archon_discovery_feeds")
                .insert(feed_data)
                .execute()
            )
            if result.data:
                return True, result.data[0]
            return False, {"error": "Insert returned no data"}

        except Exception as e:
            logger.error(f"Error adding feed: {e}")
            return False, {"error": str(e)}

    def list_feeds(
        self,
        project_id: str | None = None,
        enabled_only: bool = False,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """List discovery feeds."""
        try:
            q = self.supabase.table("archon_discovery_feeds").select("*")
            if project_id:
                q = q.eq("project_id", project_id)
            if enabled_only:
                q = q.eq("enabled", True)

            result = q.order("created_at", desc=True).execute()
            return True, result.data or []

        except Exception as e:
            logger.error(f"Error listing feeds: {e}")
            return False, []

    def update_feed(
        self,
        feed_id: str,
        enabled: bool | None = None,
        config: dict | None = None,
        poll_interval_hours: int | None = None,
        max_items_per_poll: int | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Update a discovery feed."""
        try:
            update_data: dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
            if enabled is not None:
                update_data["enabled"] = enabled
            if config is not None:
                update_data["config"] = json.dumps(config)
            if poll_interval_hours is not None:
                update_data["poll_interval_hours"] = poll_interval_hours
            if max_items_per_poll is not None:
                update_data["max_items_per_poll"] = max_items_per_poll

            result = (
                self.supabase.table("archon_discovery_feeds")
                .update(update_data)
                .eq("id", feed_id)
                .execute()
            )
            if result.data:
                return True, result.data[0]
            return False, {"error": "Update returned no data"}

        except Exception as e:
            logger.error(f"Error updating feed: {e}")
            return False, {"error": str(e)}

    def delete_feed(self, feed_id: str) -> tuple[bool, str]:
        """Delete a discovery feed."""
        try:
            self.supabase.table("archon_discovery_feeds").delete().eq("id", feed_id).execute()
            return True, "Feed deleted"
        except Exception as e:
            return False, str(e)

    # ── Discovery History ─────────────────────────────────────

    def record_discovery(
        self,
        project_id: str,
        url: str,
        title: str | None = None,
        snippet: str | None = None,
        feed_id: str | None = None,
        status: str = "discovered",
    ) -> tuple[bool, dict[str, Any] | str]:
        """Record a discovered item. Returns False if duplicate."""
        try:
            h = url_hash(url)

            # Check dedup
            existing = (
                self.supabase.table("archon_discovery_history")
                .select("id, status")
                .eq("url_hash", h)
                .execute()
            )
            if existing.data:
                return False, f"Duplicate: {url} already discovered"

            item_data = {
                "id": str(uuid4()),
                "project_id": project_id,
                "feed_id": feed_id,
                "url": url,
                "title": title,
                "snippet": snippet,
                "status": status,
                "url_hash": h,
            }

            result = (
                self.supabase.table("archon_discovery_history")
                .insert(item_data)
                .execute()
            )
            if result.data:
                return True, result.data[0]
            return False, "Insert returned no data"

        except Exception as e:
            logger.error(f"Error recording discovery: {e}")
            return False, str(e)

    def get_history(
        self,
        project_id: str | None = None,
        feed_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Get discovery history with optional filters."""
        try:
            q = self.supabase.table("archon_discovery_history").select("*")
            if project_id:
                q = q.eq("project_id", project_id)
            if feed_id:
                q = q.eq("feed_id", feed_id)
            if status:
                q = q.eq("status", status)

            result = q.order("discovered_at", desc=True).limit(limit).execute()
            return True, result.data or []

        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return False, []

    def update_history_status(
        self,
        item_id: str,
        status: str,
        source_id: str | None = None,
        wiki_page_ids: list[str] | None = None,
        error_message: str | None = None,
        skip_reason: str | None = None,
    ) -> tuple[bool, str]:
        """Update a discovery history item's status."""
        try:
            update_data: dict[str, Any] = {"status": status}
            if source_id:
                update_data["source_id"] = source_id
            if wiki_page_ids:
                update_data["wiki_page_ids"] = json.dumps(wiki_page_ids)
            if error_message:
                update_data["error_message"] = error_message
            if skip_reason:
                update_data["skip_reason"] = skip_reason
            if status == "ingested":
                update_data["ingested_at"] = datetime.now(timezone.utc).isoformat()

            self.supabase.table("archon_discovery_history").update(update_data).eq("id", item_id).execute()
            return True, "Updated"

        except Exception as e:
            return False, str(e)

    # ── Knowledge Gaps ────────────────────────────────────────

    def get_gaps(
        self,
        project_id: str,
        status: str = "open",
        limit: int = 20,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Get knowledge gaps for a project."""
        try:
            q = (
                self.supabase.table("archon_knowledge_gaps")
                .select("*")
                .eq("project_id", project_id)
            )
            if status:
                q = q.eq("status", status)

            result = q.order("priority", desc=True).limit(limit).execute()
            return True, result.data or []

        except Exception as e:
            logger.error(f"Error getting gaps: {e}")
            return False, []

    def resolve_gap(self, gap_id: str, resolved_by_page_id: str) -> tuple[bool, str]:
        """Mark a knowledge gap as resolved."""
        try:
            self.supabase.table("archon_knowledge_gaps").update({
                "status": "resolved",
                "resolved_by": resolved_by_page_id,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", gap_id).execute()
            return True, "Gap resolved"
        except Exception as e:
            return False, str(e)

    # ── Due Feeds ─────────────────────────────────────────────

    def get_due_feeds(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Get feeds that are due for polling."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            q = (
                self.supabase.table("archon_discovery_feeds")
                .select("*")
                .eq("enabled", True)
                .lte("next_poll_at", now)
            )
            if project_id:
                q = q.eq("project_id", project_id)

            result = q.execute()
            return result.data or []

        except Exception as e:
            logger.error(f"Error getting due feeds: {e}")
            return []

    def mark_feed_polled(self, feed_id: str) -> None:
        """Update feed's last_polled_at and calculate next_poll_at."""
        try:
            # Get feed to know interval
            feed = (
                self.supabase.table("archon_discovery_feeds")
                .select("poll_interval_hours")
                .eq("id", feed_id)
                .single()
                .execute()
            )
            interval = feed.data.get("poll_interval_hours", 24) if feed.data else 24
            now = datetime.now(timezone.utc)
            next_poll = now + timedelta(hours=interval)

            self.supabase.table("archon_discovery_feeds").update({
                "last_polled_at": now.isoformat(),
                "next_poll_at": next_poll.isoformat(),
                "updated_at": now.isoformat(),
            }).eq("id", feed_id).execute()

        except Exception as e:
            logger.error(f"Error marking feed polled: {e}")

    def increment_feed_stats(
        self, feed_id: str, discovered: int = 0, ingested: int = 0
    ) -> None:
        """Increment feed discovery/ingestion counters."""
        try:
            if discovered > 0:
                # Fetch current, increment, update
                feed = (
                    self.supabase.table("archon_discovery_feeds")
                    .select("total_discovered, total_ingested")
                    .eq("id", feed_id)
                    .single()
                    .execute()
                )
                if feed.data:
                    self.supabase.table("archon_discovery_feeds").update({
                        "total_discovered": (feed.data.get("total_discovered", 0) or 0) + discovered,
                        "total_ingested": (feed.data.get("total_ingested", 0) or 0) + ingested,
                    }).eq("id", feed_id).execute()

        except Exception as e:
            logger.error(f"Error incrementing feed stats: {e}")
