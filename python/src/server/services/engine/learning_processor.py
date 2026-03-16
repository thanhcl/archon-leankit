"""
Learning Processor for LeanKit V3 Task Engine.

Processes learnings from CC task executions, detects recurring patterns,
and auto-promotes to KB when recurrence >= 3.

Usage:
    processor = LearningProcessor()
    await processor.process(task, learnings)
"""

import re
from datetime import datetime
from typing import Any

from ...config.logfire_config import get_logger
from ...utils import get_supabase_client

logger = get_logger(__name__)

PROMOTE_THRESHOLD = 3
TABLE = "archon_learnings"

VALID_TYPES = {"error", "correction", "best_practice", "knowledge_gap"}
VALID_AREAS = {"frontend", "backend", "infra", "tests", "config", "security", "database"}


def _normalize(text: str) -> str:
    """Normalize text for pattern key generation."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower().strip())[:50]


def _pattern_key(learning: dict[str, Any]) -> str:
    """Generate a dedup key from type + area + description prefix."""
    ltype = learning.get("type", "unknown")
    area = learning.get("area", "unknown")
    desc = _normalize(learning.get("description", ""))
    return f"{ltype}:{area}:{desc}"


def _keyword_overlap(a: str, b: str) -> float:
    """Compute keyword overlap ratio between two strings."""
    words_a = set(_normalize(a).split())
    words_b = set(_normalize(b).split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union) if union else 0.0


class LearningProcessor:
    """Processes and indexes CC learnings."""

    def __init__(self, supabase_client=None, notifier=None):
        self._client = supabase_client or get_supabase_client()
        self._notifier = notifier

    # ── Public API ────────────────────────────────────────────────────

    async def process(self, task: dict[str, Any], learnings: list[dict[str, Any]]) -> list[str]:
        """Process learnings from a completed task.

        Returns list of stored/updated learning IDs.
        """
        ids: list[str] = []
        project_id = task.get("project_id")
        task_id = task.get("id")

        for learning in learnings:
            if not self._validate(learning):
                continue

            similar = await self.find_similar(learning.get("description", ""), project_id)

            if similar:
                await self.increment_recurrence(similar, task_id)
                new_count = (similar.get("recurrence_count") or 1) + 1

                if new_count >= PROMOTE_THRESHOLD and similar.get("status") == "pending":
                    await self.auto_promote(similar)

                ids.append(similar["id"])
            else:
                learning_id = await self.store(task, learning)
                if learning_id:
                    ids.append(learning_id)

        return ids

    # ── Storage ───────────────────────────────────────────────────────

    async def store(self, task: dict[str, Any], learning: dict[str, Any]) -> str | None:
        """Store a new learning."""
        try:
            data = {
                "project_id": task.get("project_id"),
                "task_id": task.get("id"),
                "type": learning.get("type", "knowledge_gap"),
                "description": learning.get("description", ""),
                "area": learning.get("area"),
                "suggested_rule": learning.get("suggested_rule"),
                "pattern_key": _pattern_key(learning),
                "recurrence_count": 1,
                "related_tasks": [task["id"]] if task.get("id") else [],
                "status": "pending",
            }

            resp = self._client.table(TABLE).insert(data).execute()
            if resp.data:
                lid = resp.data[0]["id"]
                logger.info(f"Learning stored | id={lid} | type={data['type']} | area={data['area']}")
                return lid
            return None
        except Exception as e:
            logger.error(f"Failed to store learning: {e}", exc_info=True)
            return None

    # ── Similarity search ─────────────────────────────────────────────

    async def find_similar(
        self, description: str, project_id: str | None,
    ) -> dict[str, Any] | None:
        """Find a similar existing learning using keyword overlap."""
        try:
            query = self._client.table(TABLE).select("*").eq("status", "pending")
            if project_id:
                query = query.eq("project_id", project_id)
            resp = query.execute()

            if not resp.data:
                return None

            best_match = None
            best_score = 0.0

            for existing in resp.data:
                score = _keyword_overlap(description, existing.get("description", ""))
                if score > 0.6 and score > best_score:
                    best_score = score
                    best_match = existing

            return best_match
        except Exception as e:
            logger.error(f"Failed to find similar learnings: {e}")
            return None

    # ── Recurrence tracking ───────────────────────────────────────────

    async def increment_recurrence(self, existing: dict[str, Any], task_id: str | None) -> None:
        """Increment recurrence count and link the task."""
        try:
            lid = existing["id"]
            related = existing.get("related_tasks") or []
            if task_id and task_id not in related:
                related = related + [task_id]

            self._client.table(TABLE).update({
                "recurrence_count": (existing.get("recurrence_count") or 1) + 1,
                "last_seen": datetime.now().isoformat(),
                "related_tasks": related,
            }).eq("id", lid).execute()

            logger.info(
                f"Learning recurrence incremented | id={lid} | "
                f"count={(existing.get('recurrence_count') or 1) + 1}"
            )
        except Exception as e:
            logger.error(f"Failed to increment recurrence: {e}")

    # ── Auto-promote ──────────────────────────────────────────────────

    async def auto_promote(self, learning: dict[str, Any]) -> None:
        """Auto-promote learning to KB when recurrence threshold met."""
        lid = learning["id"]

        if not learning.get("suggested_rule"):
            logger.info(f"Learning {lid} has no suggested_rule — notifying only")
            if self._notifier:
                await self._notifier.on_learning_pattern_detected(learning)
            return

        try:
            # Update status
            self._client.table(TABLE).update({
                "status": "promoted",
                "promoted_to": "KB",
                "promoted_at": datetime.now().isoformat(),
            }).eq("id", lid).execute()

            logger.info(f"Learning auto-promoted to KB | id={lid} | rule={learning['suggested_rule'][:80]}")

            if self._notifier:
                await self._notifier.on_learning_promoted(learning)

        except Exception as e:
            logger.error(f"Failed to auto-promote learning {lid}: {e}")

    # ── Validation ────────────────────────────────────────────────────

    @staticmethod
    def _validate(learning: dict[str, Any]) -> bool:
        if not learning.get("description"):
            return False
        if learning.get("type") and learning["type"] not in VALID_TYPES:
            return False
        if learning.get("area") and learning["area"] not in VALID_AREAS:
            return False
        return True

    # ── Query helpers (for API) ───────────────────────────────────────

    def list_learnings(
        self, project_id: str | None = None, status: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        try:
            query = self._client.table(TABLE).select("*")
            if project_id:
                query = query.eq("project_id", project_id)
            if status:
                query = query.eq("status", status)
            query = query.order("last_seen", desc=True)
            resp = query.execute()
            return True, {"learnings": resp.data or [], "count": len(resp.data or [])}
        except Exception as e:
            return False, {"error": str(e)}

    def list_patterns(self, project_id: str | None = None, min_recurrence: int = 2) -> tuple[bool, dict[str, Any]]:
        try:
            query = self._client.table(TABLE).select("*").gte("recurrence_count", min_recurrence)
            if project_id:
                query = query.eq("project_id", project_id)
            query = query.order("recurrence_count", desc=True)
            resp = query.execute()
            return True, {"patterns": resp.data or [], "count": len(resp.data or [])}
        except Exception as e:
            return False, {"error": str(e)}

    def get_stats(self, project_id: str | None = None) -> tuple[bool, dict[str, Any]]:
        try:
            query = self._client.table(TABLE).select("*")
            if project_id:
                query = query.eq("project_id", project_id)
            resp = query.execute()
            data = resp.data or []

            by_type: dict[str, int] = {}
            by_area: dict[str, int] = {}
            promoted = 0
            recurring = 0

            for item in data:
                t = item.get("type", "unknown")
                by_type[t] = by_type.get(t, 0) + 1
                a = item.get("area", "unknown")
                by_area[a] = by_area.get(a, 0) + 1
                if item.get("status") == "promoted":
                    promoted += 1
                if (item.get("recurrence_count") or 1) >= 2:
                    recurring += 1

            return True, {
                "total": len(data),
                "promoted": promoted,
                "recurring": recurring,
                "by_type": by_type,
                "by_area": by_area,
            }
        except Exception as e:
            return False, {"error": str(e)}

    async def dismiss(self, learning_id: str) -> tuple[bool, str]:
        try:
            self._client.table(TABLE).update({"status": "dismissed"}).eq("id", learning_id).execute()
            return True, "Learning dismissed"
        except Exception as e:
            return False, str(e)

    async def manual_promote(self, learning_id: str) -> tuple[bool, str]:
        try:
            self._client.table(TABLE).update({
                "status": "promoted",
                "promoted_to": "KB_manual",
                "promoted_at": datetime.now().isoformat(),
            }).eq("id", learning_id).execute()
            return True, "Learning promoted"
        except Exception as e:
            return False, str(e)
