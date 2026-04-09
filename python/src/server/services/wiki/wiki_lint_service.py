"""
Wiki Lint Service — Knowledge health checks.

Detects:
- Orphan pages (0 inbound links)
- Stale content (not verified in 30+ days)
- Contradictions (pages linked with 'contradicts' type)
- Knowledge gaps (topics referenced but no page exists)
- Broken source references
- Low quality pages
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4

from ...utils import get_supabase_client

logger = logging.getLogger(__name__)


@dataclass
class LintReport:
    """Results of a wiki lint run."""

    orphans: list[dict[str, Any]] = field(default_factory=list)
    stale: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    broken_sources: list[dict[str, Any]] = field(default_factory=list)
    low_quality: list[dict[str, Any]] = field(default_factory=list)
    god_nodes: list[dict[str, Any]] = field(default_factory=list)
    surprise_connections: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "orphans": self.orphans,
            "stale": self.stale,
            "contradictions": self.contradictions,
            "gaps": self.gaps,
            "broken_sources": self.broken_sources,
            "low_quality": self.low_quality,
            "god_nodes": self.god_nodes,
            "surprise_connections": self.surprise_connections,
            "stats": self.stats,
            "total_issues": (
                len(self.orphans)
                + len(self.stale)
                + len(self.contradictions)
                + len(self.gaps)
                + len(self.broken_sources)
                + len(self.low_quality)
            ),
        }


class WikiLintService:
    """Service for knowledge base health checks."""

    def __init__(self) -> None:
        self.supabase = get_supabase_client()

    def lint(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """Run full lint on a project's wiki. Returns a LintReport."""
        try:
            report = LintReport()

            # Get all pages for project
            pages_result = (
                self.supabase.table("archon_wiki_pages")
                .select("id, slug, title, page_type, status, quality_score, updated_at, last_verified_at, source_ids")
                .eq("project_id", project_id)
                .neq("status", "archived")
                .execute()
            )
            pages = pages_result.data or []
            page_ids = {p["id"] for p in pages}

            if not pages:
                report.stats = {"total_pages": 0}
                return True, report.to_dict()

            # 1. Orphan detection — pages with 0 inbound links
            report.orphans = self._find_orphans(pages, page_ids)

            # 2. Stale content — not updated in 30+ days
            report.stale = self._find_stale(pages, days=30)

            # 3. Contradictions — pages linked with 'contradicts'
            report.contradictions = self._find_contradictions(page_ids)

            # 4. Low quality pages
            report.low_quality = [
                {"id": p["id"], "slug": p["slug"], "title": p["title"], "quality_score": p.get("quality_score", 0)}
                for p in pages
                if (p.get("quality_score") or 0) < 0.3 and p.get("status") == "active"
            ]

            # 5. Broken source references
            report.broken_sources = self._find_broken_sources(pages)

            # 6. God nodes — highly connected hubs
            report.god_nodes = self._find_god_nodes(pages, page_ids)

            # 7. Surprise connections — weak cross-community links
            report.surprise_connections = self._find_surprise_connections(page_ids)

            # Stats
            report.stats = {
                "total_pages": len(pages),
                "active": sum(1 for p in pages if p.get("status") == "active"),
                "draft": sum(1 for p in pages if p.get("status") == "draft"),
                "stale": len(report.stale),
                "orphans": len(report.orphans),
                "contradictions": len(report.contradictions),
                "low_quality": len(report.low_quality),
                "god_nodes": len(report.god_nodes),
                "surprise_connections": len(report.surprise_connections),
            }

            # 8. Create knowledge gaps for significant findings
            self._create_gaps_from_findings(project_id, report)

            return True, report.to_dict()

        except Exception as e:
            logger.error(f"Error running wiki lint: {e}")
            return False, {"error": str(e)}

    def _find_orphans(
        self, pages: list[dict], page_ids: set[str]
    ) -> list[dict[str, Any]]:
        """Find pages with 0 inbound links (excluding source_summary pages).

        Uses a single batch query instead of per-page queries to avoid N+1.
        """
        # Batch fetch all inbound link targets in one query
        all_inbound = (
            self.supabase.table("archon_wiki_links")
            .select("to_page_id")
            .execute()
        )
        inbound_targets: set[str] = {
            link["to_page_id"] for link in (all_inbound.data or [])
        }

        orphans = []
        for page in pages:
            if page.get("page_type") == "source_summary":
                continue  # Source summaries are naturally terminal

            if page["id"] not in inbound_targets:
                orphans.append({
                    "id": page["id"],
                    "slug": page["slug"],
                    "title": page["title"],
                    "page_type": page["page_type"],
                })
        return orphans

    def _find_stale(self, pages: list[dict], days: int = 30) -> list[dict[str, Any]]:
        """Find pages not updated or verified in N days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stale = []
        for page in pages:
            if page.get("status") != "active":
                continue

            # Check last_verified_at first, then updated_at
            check_date_str = page.get("last_verified_at") or page.get("updated_at")
            if not check_date_str:
                stale.append({
                    "id": page["id"],
                    "slug": page["slug"],
                    "title": page["title"],
                    "last_updated": None,
                })
                continue

            check_date = datetime.fromisoformat(check_date_str.replace("Z", "+00:00"))
            if check_date < cutoff:
                stale.append({
                    "id": page["id"],
                    "slug": page["slug"],
                    "title": page["title"],
                    "last_updated": check_date_str,
                    "days_stale": (datetime.now(timezone.utc) - check_date).days,
                })
        return stale

    def _find_contradictions(self, page_ids: set[str]) -> list[dict[str, Any]]:
        """Find pages connected by 'contradicts' links.

        Uses a single batch query instead of per-page queries to avoid N+1.
        """
        # Single query for all contradiction links
        all_contradicts = (
            self.supabase.table("archon_wiki_links")
            .select("from_page_id, to_page_id, context")
            .eq("link_type", "contradicts")
            .execute()
        )

        contradictions = []
        seen_pairs: set[tuple[str, str]] = set()
        for link in all_contradicts.data or []:
            # Only include links where at least one side is in our project pages
            if link["from_page_id"] not in page_ids and link["to_page_id"] not in page_ids:
                continue
            pair = tuple(sorted([link["from_page_id"], link["to_page_id"]]))
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                contradictions.append({
                    "page_a": link["from_page_id"],
                    "page_b": link["to_page_id"],
                    "context": link.get("context"),
                })
        return contradictions

    def _find_broken_sources(self, pages: list[dict]) -> list[dict[str, Any]]:
        """Find pages referencing source_ids that no longer exist.

        Uses a single batch query to fetch all known source_ids instead of
        checking per source per page (avoids N+1).
        """
        # Collect all referenced source_ids across all pages
        all_referenced: set[str] = set()
        page_source_map: list[tuple[dict, list[str]]] = []
        for page in pages:
            source_ids_raw = page.get("source_ids", "[]")
            if isinstance(source_ids_raw, str):
                source_ids = json.loads(source_ids_raw)
            else:
                source_ids = source_ids_raw or []
            if source_ids:
                page_source_map.append((page, source_ids))
                all_referenced.update(source_ids)

        if not all_referenced:
            return []

        # Single query: fetch all existing source_ids
        existing_sources_result = (
            self.supabase.table("archon_sources")
            .select("source_id")
            .execute()
        )
        existing_ids: set[str] = {
            s["source_id"] for s in (existing_sources_result.data or [])
        }

        # Check in-memory
        broken = []
        for page, source_ids in page_source_map:
            for sid in source_ids:
                if sid not in existing_ids:
                    broken.append({
                        "page_id": page["id"],
                        "page_slug": page["slug"],
                        "missing_source_id": sid,
                    })
        return broken

    def _find_god_nodes(
        self, pages: list[dict], page_ids: set[str]
    ) -> list[dict[str, Any]]:
        """Find highly-connected hub pages (degree > 2× median, min 4)."""
        # Batch fetch all links
        all_links = (
            self.supabase.table("archon_wiki_links")
            .select("from_page_id, to_page_id")
            .execute()
        )
        # Count degree per page
        degree: dict[str, dict[str, int]] = {
            pid: {"inbound": 0, "outbound": 0} for pid in page_ids
        }
        for link in all_links.data or []:
            src, tgt = link["from_page_id"], link["to_page_id"]
            if src in degree:
                degree[src]["outbound"] += 1
            if tgt in degree:
                degree[tgt]["inbound"] += 1

        total_degrees = [d["inbound"] + d["outbound"] for d in degree.values()]
        if not total_degrees:
            return []

        total_degrees_sorted = sorted(total_degrees)
        median = total_degrees_sorted[len(total_degrees_sorted) // 2]
        threshold = max(4, median * 2)

        page_map = {p["id"]: p for p in pages}
        god_nodes = []
        for pid, d in degree.items():
            total = d["inbound"] + d["outbound"]
            if total >= threshold and pid in page_map:
                p = page_map[pid]
                god_nodes.append({
                    "id": pid,
                    "slug": p["slug"],
                    "title": p["title"],
                    "degree": total,
                    "inbound": d["inbound"],
                    "outbound": d["outbound"],
                    "median_degree": median,
                })

        return sorted(god_nodes, key=lambda x: -x["degree"])

    def _find_surprise_connections(
        self, page_ids: set[str]
    ) -> list[dict[str, Any]]:
        """Find weak cross-community links (potential unexplored relationships).

        Gracefully returns empty if communities haven't been assigned yet.
        """
        # Fetch community assignments
        pages_with_community = (
            self.supabase.table("archon_wiki_pages")
            .select("id, community")
            .execute()
        )
        community_map: dict[str, str | None] = {
            p["id"]: p.get("community")
            for p in (pages_with_community.data or [])
            if p["id"] in page_ids
        }

        # Check if any communities are assigned
        assigned = [c for c in community_map.values() if c is not None]
        if len(assigned) < 2:
            return []  # No communities or only one — nothing to compare

        # Fetch all links with strength
        all_links = (
            self.supabase.table("archon_wiki_links")
            .select("from_page_id, to_page_id, strength, link_type, context")
            .execute()
        )

        surprises = []
        for link in all_links.data or []:
            src, tgt = link["from_page_id"], link["to_page_id"]
            if src not in page_ids or tgt not in page_ids:
                continue

            src_comm = community_map.get(src)
            tgt_comm = community_map.get(tgt)

            # Cross-community + weak strength
            if (
                src_comm and tgt_comm
                and src_comm != tgt_comm
                and (link.get("strength") or 0.5) <= 0.4
            ):
                surprises.append({
                    "from_page_id": src,
                    "to_page_id": tgt,
                    "from_community": src_comm,
                    "to_community": tgt_comm,
                    "strength": link.get("strength"),
                    "link_type": link.get("link_type"),
                    "context": link.get("context"),
                })

        return surprises

    def _create_gaps_from_findings(self, project_id: str, report: LintReport) -> None:
        """Create knowledge gap entries for significant lint findings."""
        try:
            # Stale pages become gaps
            for stale in report.stale[:10]:  # Limit to top 10
                self._upsert_gap(
                    project_id=project_id,
                    topic=stale["title"],
                    description=f"Wiki page '{stale['title']}' has not been verified in {stale.get('days_stale', 30)}+ days",
                    gap_type="stale_content",
                    related_pages=[stale["id"]],
                    priority=40,
                )

            # Orphans become gaps
            for orphan in report.orphans[:10]:
                self._upsert_gap(
                    project_id=project_id,
                    topic=orphan["title"],
                    description=f"Wiki page '{orphan['title']}' has no inbound links — isolated from knowledge graph",
                    gap_type="orphan",
                    related_pages=[orphan["id"]],
                    priority=30,
                )

            # Surprise connections become shallow_coverage gaps
            for surprise in report.surprise_connections[:10]:
                self._upsert_gap(
                    project_id=project_id,
                    topic=f"{surprise.get('from_community', '?')} <-> {surprise.get('to_community', '?')}",
                    description=f"Weak link between communities '{surprise.get('from_community')}' and '{surprise.get('to_community')}' — may indicate important but under-explored relationship",
                    gap_type="shallow_coverage",
                    related_pages=[surprise["from_page_id"], surprise["to_page_id"]],
                    priority=60,
                )

        except Exception as e:
            logger.warning(f"Error creating gaps from findings: {e}")

    def _upsert_gap(
        self,
        project_id: str,
        topic: str,
        description: str,
        gap_type: str,
        related_pages: list[str] | None = None,
        priority: int = 50,
    ) -> None:
        """Create or update a knowledge gap."""
        try:
            # Check if similar gap already exists (open)
            existing = (
                self.supabase.table("archon_knowledge_gaps")
                .select("id")
                .eq("project_id", project_id)
                .eq("topic", topic)
                .eq("status", "open")
                .execute()
            )
            if existing.data:
                return  # Gap already exists

            gap_data = {
                "id": str(uuid4()),
                "project_id": project_id,
                "topic": topic,
                "description": description,
                "gap_type": gap_type,
                "detected_by": "lint",
                "related_pages": json.dumps(related_pages or []),
                "priority": priority,
            }
            self.supabase.table("archon_knowledge_gaps").insert(gap_data).execute()

        except Exception as e:
            logger.warning(f"Error upserting knowledge gap: {e}")
