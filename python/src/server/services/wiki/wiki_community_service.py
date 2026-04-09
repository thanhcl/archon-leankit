"""
Wiki Community Detection — auto-cluster pages using label propagation.

No external dependencies needed — implements a simple label propagation
algorithm using only standard library + Supabase client.
"""

import json
import logging
import random
from collections import Counter, defaultdict
from typing import Any

from ...utils import get_supabase_client

logger = logging.getLogger(__name__)


class WikiCommunityService:
    """Service for detecting and managing topic communities in the wiki graph."""

    def __init__(self) -> None:
        self.supabase = get_supabase_client()

    def detect_communities(
        self, project_id: str, max_iterations: int = 20
    ) -> tuple[bool, dict[str, Any]]:
        """Run label propagation to detect page communities.

        Algorithm:
        1. Each page starts with its own ID as label.
        2. Each iteration: every page adopts the most frequent label
           among its neighbors (weighted by link strength).
        3. Converges when no labels change, or max_iterations reached.
        4. Communities named by the highest-quality page in each cluster.
        """
        try:
            # 1. Fetch all active pages
            pages_result = (
                self.supabase.table("archon_wiki_pages")
                .select("id, slug, title, quality_score, tags")
                .eq("project_id", project_id)
                .neq("status", "archived")
                .execute()
            )
            pages = pages_result.data or []
            if not pages:
                return True, {"communities": [], "total_communities": 0}

            page_map = {p["id"]: p for p in pages}
            page_ids = set(page_map.keys())

            # 2. Fetch all links between project pages
            all_links = (
                self.supabase.table("archon_wiki_links")
                .select("from_page_id, to_page_id, strength")
                .execute()
            )
            # Build adjacency list (undirected, weighted)
            neighbors: dict[str, list[tuple[str, float]]] = defaultdict(list)
            for link in all_links.data or []:
                src, tgt = link["from_page_id"], link["to_page_id"]
                if src in page_ids and tgt in page_ids:
                    w = link.get("strength") or 0.5
                    neighbors[src].append((tgt, w))
                    neighbors[tgt].append((src, w))

            # 3. Initialize labels
            labels: dict[str, str] = {pid: pid for pid in page_ids}

            # 4. Label propagation
            node_list = list(page_ids)
            for _iteration in range(max_iterations):
                random.shuffle(node_list)
                changed = False
                for node in node_list:
                    nbrs = neighbors.get(node, [])
                    if not nbrs:
                        continue
                    # Weighted label vote
                    label_weights: dict[str, float] = defaultdict(float)
                    for nbr, weight in nbrs:
                        label_weights[labels[nbr]] += weight
                    best_label = max(label_weights, key=label_weights.get)
                    if labels[node] != best_label:
                        labels[node] = best_label
                        changed = True
                if not changed:
                    break

            # 5. Group by label → communities
            community_members: dict[str, list[str]] = defaultdict(list)
            for pid, label in labels.items():
                community_members[label].append(pid)

            # 6. Name communities by highest-quality page
            communities = []
            for idx, (label, member_ids) in enumerate(
                sorted(community_members.items(), key=lambda x: -len(x[1]))
            ):
                members = [page_map[mid] for mid in member_ids if mid in page_map]
                if not members:
                    continue
                best = max(members, key=lambda m: m.get("quality_score") or 0)
                name = best.get("title", f"Community {idx + 1}")
                communities.append({
                    "name": name,
                    "page_count": len(members),
                    "pages": [
                        {"id": m["id"], "slug": m["slug"], "title": m["title"]}
                        for m in members
                    ],
                })

            # 7. Write back community assignments
            for comm in communities:
                comm_name = comm["name"]
                for page_info in comm["pages"]:
                    self.supabase.table("archon_wiki_pages").update(
                        {"community": comm_name}
                    ).eq("id", page_info["id"]).execute()

            return True, {
                "communities": communities,
                "total_communities": len(communities),
                "iterations_used": _iteration + 1 if pages else 0,
            }

        except Exception as e:
            logger.error(f"Error detecting communities: {e}", exc_info=True)
            return False, {"error": str(e)}

    def get_communities(
        self, project_id: str
    ) -> tuple[bool, dict[str, Any]]:
        """Get existing community assignments (no recomputation)."""
        try:
            result = (
                self.supabase.table("archon_wiki_pages")
                .select("id, slug, title, community, quality_score")
                .eq("project_id", project_id)
                .neq("status", "archived")
                .not_.is_("community", "null")
                .order("community")
                .execute()
            )
            pages = result.data or []

            # Group by community
            groups: dict[str, list[dict]] = defaultdict(list)
            for p in pages:
                groups[p["community"]].append({
                    "id": p["id"],
                    "slug": p["slug"],
                    "title": p["title"],
                })

            communities = [
                {"name": name, "page_count": len(members), "pages": members}
                for name, members in sorted(groups.items(), key=lambda x: -len(x[1]))
            ]

            return True, {
                "communities": communities,
                "total_communities": len(communities),
            }

        except Exception as e:
            logger.error(f"Error getting communities: {e}")
            return False, {"error": str(e)}
