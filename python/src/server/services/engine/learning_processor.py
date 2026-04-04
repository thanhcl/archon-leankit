"""
Learning Processor for LeanKit V3 Task Engine.

Processes learnings from CC task executions, detects recurring patterns,
and auto-promotes to KB when recurrence >= 3.

Also processes code patterns extracted from completed tasks, with
auto-promotion to KB when confidence >= 0.9 and usage_count >= 3.

Usage:
    processor = LearningProcessor()
    await processor.process(task, learnings, code_patterns)
"""

import re
from datetime import datetime
from typing import Any

from ...config.logfire_config import get_logger
from ...utils import get_supabase_client

logger = get_logger(__name__)

PROMOTE_THRESHOLD = 3
TABLE = "archon_learnings"
PATTERNS_TABLE = "archon_code_patterns"
PROMOTION_LOG_TABLE = "archon_promotion_log"

PATTERN_PROMOTE_CONFIDENCE = 0.9
PATTERN_PROMOTE_USAGE = 3
PATTERN_CONFIDENCE_INCREMENT = 0.05

VALID_TYPES = {
    "error", "correction", "best_practice", "knowledge_gap",
    # GStack-inspired reflection types (adopted 2026-04-04)
    "pitfall", "operational", "tool", "architecture_insight",
}
VALID_AREAS = {"frontend", "backend", "infra", "tests", "config", "security", "database"}

# Confidence decay: observed/inferred learnings lose 1 point per DECAY_PERIOD_DAYS
DECAY_PERIOD_DAYS = 30
# Learnings with decayed confidence below this threshold are excluded from injection
CONFIDENCE_FLOOR = 2
VALID_CATEGORIES = {"security", "error-handling", "testing", "architecture", "performance", "api-design"}


def _normalize(text: str) -> str:
    """Normalize text for pattern key generation."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower().strip())[:50]


def _pattern_key(learning: dict[str, Any]) -> str:
    """Generate a dedup key from type + area + description prefix."""
    ltype = learning.get("type", "unknown")
    area = learning.get("area", "unknown")
    desc = _normalize(learning.get("description", ""))
    return f"{ltype}:{area}:{desc}"


def _code_pattern_key(pattern: dict[str, Any]) -> str:
    """Generate a dedup key for a code pattern: category.language.normalized_name."""
    category = pattern.get("category", "unknown")
    language = pattern.get("language", "java")
    name = re.sub(r"[^a-z0-9]+", "-", pattern.get("pattern_name", "").lower().strip())[:60]
    return f"{category}.{language}.{name}".rstrip("-")


def _effective_confidence(learning: dict[str, Any], now: datetime | None = None) -> int:
    """Compute confidence with time decay for observed/inferred learnings.

    Adopted from GStack's operational self-improvement pattern:
    - observed/inferred sources lose 1 confidence point per 30 days
    - user_stated learnings never decay (explicit user knowledge)
    - Floors at 0 (never negative)
    """
    now = now or datetime.now()
    base_confidence = learning.get("confidence", 5)
    source = learning.get("source", "observed")

    if source == "user_stated":
        return base_confidence

    created = learning.get("created_at") or learning.get("last_seen")
    if not created:
        return base_confidence

    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            return base_confidence

    age_days = max(0, (now - created).days)
    decay = age_days // DECAY_PERIOD_DAYS
    return max(0, base_confidence - decay)


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

    async def process(
        self,
        task: dict[str, Any],
        learnings: list[dict[str, Any]],
        code_patterns: list[dict[str, Any]] | None = None,
        execution_run_id: str | None = None,
    ) -> list[str]:
        """Process learnings and code patterns from a completed task.

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
                await self.increment_recurrence(similar, task_id, execution_run_id)
                new_count = (similar.get("recurrence_count") or 1) + 1

                if new_count >= PROMOTE_THRESHOLD and similar.get("status") == "pending":
                    await self.auto_promote(similar)

                ids.append(similar["id"])
            else:
                learning_id = await self.store(task, learning, execution_run_id)
                if learning_id:
                    ids.append(learning_id)

        # Process code patterns
        if code_patterns:
            await self._process_code_patterns(task, code_patterns)

        return ids

    # ── Storage ───────────────────────────────────────────────────────

    async def store(
        self,
        task: dict[str, Any],
        learning: dict[str, Any],
        execution_run_id: str | None = None,
    ) -> str | None:
        """Store a new learning.

        Supports extended fields from structured reflection:
        - confidence (int 1-10): how certain the agent is about this learning
        - source (str): observed | inferred | user_stated
        - files (list[str]): file paths referenced by this learning
        """
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
                "source_run_ids": [execution_run_id] if execution_run_id else [],
                "status": "pending",
                # Extended fields from structured reflection
                "confidence": learning.get("confidence", 5),
                "source": learning.get("source", "observed"),
                "files": learning.get("files", []),
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

    async def increment_recurrence(
        self,
        existing: dict[str, Any],
        task_id: str | None,
        execution_run_id: str | None = None,
    ) -> None:
        """Increment recurrence count and link the task and execution run."""
        try:
            lid = existing["id"]
            related = existing.get("related_tasks") or []
            if task_id and task_id not in related:
                related = related + [task_id]

            source_runs = existing.get("source_run_ids") or []
            if execution_run_id and execution_run_id not in source_runs:
                source_runs = source_runs + [execution_run_id]

            self._client.table(TABLE).update({
                "recurrence_count": (existing.get("recurrence_count") or 1) + 1,
                "last_seen": datetime.now().isoformat(),
                "related_tasks": related,
                "source_run_ids": source_runs,
            }).eq("id", lid).execute()

            logger.info(
                f"Learning recurrence incremented | id={lid} | "
                f"count={(existing.get('recurrence_count') or 1) + 1}"
            )
        except Exception as e:
            logger.error(f"Failed to increment recurrence: {e}")

    # ── Auto-promote ──────────────────────────────────────────────────

    async def auto_promote(self, learning: dict[str, Any]) -> None:
        """Auto-promote learning to KB when recurrence threshold met.

        If the learning has a suggested_rule, also creates a pending entry in
        archon_rule_suggestions for the owner to approve/reject in the Rules UI.
        Writes a promotion log entry regardless.
        """
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

            # Write promotion audit log
            await self._log_promotion(learning, promotion_type="auto", promoted_to="KB")

            # Create a pending rule suggestion for owner review
            await self._create_rule_suggestion(learning)

            if self._notifier:
                await self._notifier.on_learning_promoted(learning)

        except Exception as e:
            logger.error(f"Failed to auto-promote learning {lid}: {e}")

    async def _log_promotion(
        self,
        learning: dict[str, Any],
        promotion_type: str,
        promoted_to: str,
    ) -> None:
        """Write a promotion audit entry to archon_promotion_log."""
        lid = learning["id"]
        recurrence = learning.get("recurrence_count", 1)
        source_runs = learning.get("source_run_ids") or []
        source_tasks = learning.get("related_tasks") or []
        confidence = round(min(0.95, 0.5 + max(0, recurrence - 1) * 0.1), 2)
        reason = (
            f"Recurred {recurrence}x across {len(source_runs)} run(s) / "
            f"{len(source_tasks)} task(s)"
        )
        try:
            self._client.table(PROMOTION_LOG_TABLE).insert({
                "learning_id": lid,
                "project_id": learning.get("project_id"),
                "promotion_type": promotion_type,
                "promoted_to": promoted_to,
                "recurrence_count": recurrence,
                "source_run_ids": source_runs,
                "source_task_ids": source_tasks,
                "confidence": confidence,
                "reason": reason,
                "learning_description": (learning.get("description", ""))[:500],
            }).execute()
            logger.info(
                f"Promotion logged | learning_id={lid} | type={promotion_type} | "
                f"to={promoted_to} | runs={len(source_runs)} | tasks={len(source_tasks)}"
            )
        except Exception as e:
            logger.warning(f"Failed to write promotion log for learning {lid}: {e}")

    async def _create_rule_suggestion(self, learning: dict[str, Any]) -> None:
        """Create a pending rule suggestion from a promoted learning."""
        from ..rules.rule_optimizer_service import RuleOptimizerService

        lid = learning["id"]
        suggested_rule = learning.get("suggested_rule", "")
        area = learning.get("area", "")
        recurrence = learning.get("recurrence_count", 1)
        section = RuleOptimizerService._area_to_section(area)

        # Check if a pending suggestion for this learning already exists
        try:
            existing = (
                self._client.table("archon_rule_suggestions")
                .select("id")
                .eq("learning_id", lid)
                .eq("status", "pending")
                .execute()
            )
            if existing.data:
                return  # Already has a pending suggestion
        except Exception as e:
            logger.warning(f"Failed to check existing rule suggestion: {e}")

        try:
            confidence = min(0.95, 0.5 + max(0, recurrence - 1) * 0.1)
            related_tasks = learning.get("related_tasks") or []
            reason = (
                f"Learning recurred {recurrence} times across {len(related_tasks)} tasks: "
                f"{learning.get('description', '')[:100]}"
            )

            self._client.table("archon_rule_suggestions").insert({
                "project_id": learning.get("project_id"),
                "learning_id": lid,
                "section": section,
                "rule_text": suggested_rule,
                "confidence": round(confidence, 2),
                "reason": reason,
                "status": "pending",
            }).execute()

            logger.info(f"Rule suggestion created | learning_id={lid} | section={section}")
        except Exception as e:
            logger.error(f"Failed to create rule suggestion for learning {lid}: {e}")

    # ── Code Patterns ────────────────────────────────────────────────

    async def _process_code_patterns(
        self, task: dict[str, Any], patterns: list[dict[str, Any]],
    ) -> None:
        """Process code patterns extracted from CC output."""
        for pattern in patterns:
            if not self._validate_pattern(pattern):
                continue
            try:
                existing = await self._find_similar_pattern(pattern, task.get("project_id"))

                if existing:
                    await self._increment_pattern_usage(existing, task)
                    # Check auto-promote threshold
                    new_count = (existing.get("usage_count") or 1) + 1
                    new_confidence = min(1.0, (existing.get("confidence") or 0.7) + PATTERN_CONFIDENCE_INCREMENT)
                    if new_confidence >= PATTERN_PROMOTE_CONFIDENCE and new_count >= PATTERN_PROMOTE_USAGE:
                        if existing.get("status") not in ("promoted", "deprecated"):
                            await self._promote_pattern(existing)
                else:
                    await self._create_pattern(task, pattern)
            except Exception as e:
                logger.error(f"Failed to process code pattern: {e}", exc_info=True)

    async def _find_similar_pattern(
        self, pattern: dict[str, Any], project_id: str | None,
    ) -> dict[str, Any] | None:
        """Find an existing pattern by pattern_key match."""
        key = _code_pattern_key(pattern)
        try:
            query = self._client.table(PATTERNS_TABLE).select("*").eq("pattern_key", key)
            if project_id:
                query = query.eq("project_id", project_id)
            resp = query.execute()
            if resp.data:
                return resp.data[0]
            return None
        except Exception as e:
            logger.error(f"Failed to find similar pattern: {e}")
            return None

    async def _create_pattern(self, task: dict[str, Any], pattern: dict[str, Any]) -> str | None:
        """Store a new code pattern."""
        try:
            data = {
                "project_id": task.get("project_id"),
                "pattern_name": pattern["pattern_name"],
                "pattern_key": _code_pattern_key(pattern),
                "category": pattern["category"],
                "language": pattern.get("language", "java"),
                "code_example": pattern["code_example"],
                "context": pattern["context"],
                "anti_pattern": pattern.get("anti_pattern"),
                "source_task_ids": [task["id"]] if task.get("id") else [],
                "source_files": pattern.get("source_files", []),
                "extracted_from": "task_completion",
                "usage_count": 1,
                "confidence": 0.7,
                "status": "pending",
            }
            resp = self._client.table(PATTERNS_TABLE).insert(data).execute()
            if resp.data:
                pid = resp.data[0]["id"]
                logger.info(
                    f"Code pattern created | id={pid} | name={pattern['pattern_name']} "
                    f"| category={pattern['category']}"
                )
                return pid
            return None
        except Exception as e:
            logger.error(f"Failed to create code pattern: {e}", exc_info=True)
            return None

    async def _increment_pattern_usage(
        self, existing: dict[str, Any], task: dict[str, Any],
    ) -> None:
        """Increment usage_count and merge source data for an existing pattern."""
        try:
            pid = existing["id"]
            task_id = task.get("id")
            source_tasks = existing.get("source_task_ids") or []
            if task_id and task_id not in source_tasks:
                source_tasks = source_tasks + [task_id]

            new_confidence = min(1.0, (existing.get("confidence") or 0.7) + PATTERN_CONFIDENCE_INCREMENT)

            self._client.table(PATTERNS_TABLE).update({
                "usage_count": (existing.get("usage_count") or 1) + 1,
                "last_used_at": datetime.now().isoformat(),
                "source_task_ids": source_tasks,
                "confidence": new_confidence,
                "updated_at": datetime.now().isoformat(),
            }).eq("id", pid).execute()

            logger.info(
                f"Code pattern usage incremented | id={pid} | "
                f"count={(existing.get('usage_count') or 1) + 1} | confidence={new_confidence:.2f}"
            )
        except Exception as e:
            logger.error(f"Failed to increment pattern usage: {e}")

    async def _promote_pattern(self, pattern: dict[str, Any]) -> None:
        """Promote a code pattern to KB via RAG indexing."""
        pid = pattern["id"]
        try:
            kb_content = self._build_pattern_kb_content(pattern)

            # Index in RAG KB
            try:
                from ..search.rag_service import RAGService
                rag = RAGService()
                await rag.index_document(
                    kb_content,
                    source=f"code-pattern:{pattern.get('pattern_key', pid)}",
                )
            except Exception as e:
                logger.warning(f"Failed to index pattern in KB (continuing): {e}")

            self._client.table(PATTERNS_TABLE).update({
                "status": "promoted",
                "promoted_to": "KB",
                "updated_at": datetime.now().isoformat(),
            }).eq("id", pid).execute()

            logger.info(f"Code pattern promoted to KB | id={pid} | name={pattern.get('pattern_name')}")

            if self._notifier:
                await self._notifier.on_pattern_promoted(pattern)

        except Exception as e:
            logger.error(f"Failed to promote code pattern {pid}: {e}", exc_info=True)

    @staticmethod
    def _build_pattern_kb_content(pattern: dict[str, Any]) -> str:
        """Build KB-indexable content from a code pattern."""
        language = pattern.get("language", "java")
        anti = pattern.get("anti_pattern") or "N/A"
        source_files = pattern.get("source_files") or []
        source_tasks = pattern.get("source_task_ids") or []
        return (
            f"## Expert Code Pattern: {pattern.get('pattern_name', 'Unknown')}\n"
            f"Category: {pattern.get('category', 'unknown')}\n"
            f"Language: {language}\n\n"
            f"### When to Use\n{pattern.get('context', '')}\n\n"
            f"### Expert Implementation\n```{language}\n{pattern.get('code_example', '')}\n```\n\n"
            f"### Anti-Pattern (What NOT to Do)\n{anti}\n\n"
            f"### Provenance\n"
            f"Used {pattern.get('usage_count', 1)} times across {len(source_tasks)} tasks.\n"
            f"Source files: {', '.join(source_files) if source_files else 'N/A'}\n"
        )

    @staticmethod
    def _validate_pattern(pattern: dict[str, Any]) -> bool:
        """Validate a code pattern has required fields."""
        if not pattern.get("pattern_name"):
            return False
        if not pattern.get("category") or pattern["category"] not in VALID_CATEGORIES:
            return False
        if not pattern.get("code_example"):
            return False
        if not pattern.get("context"):
            return False
        return True

    # ── Code Pattern Query Helpers (for API) ─────────────────────────

    def list_code_patterns(
        self,
        project_id: str | None = None,
        category: str | None = None,
        status: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        try:
            query = self._client.table(PATTERNS_TABLE).select("*")
            if project_id:
                query = query.eq("project_id", project_id)
            if category:
                query = query.eq("category", category)
            if status:
                query = query.eq("status", status)
            query = query.order("usage_count", desc=True)
            resp = query.execute()
            return True, {"patterns": resp.data or [], "count": len(resp.data or [])}
        except Exception as e:
            return False, {"error": str(e)}

    def get_code_pattern(self, pattern_id: str) -> tuple[bool, dict[str, Any]]:
        try:
            resp = self._client.table(PATTERNS_TABLE).select("*").eq("id", pattern_id).execute()
            if resp.data:
                return True, {"pattern": resp.data[0]}
            return False, {"error": "Pattern not found"}
        except Exception as e:
            return False, {"error": str(e)}

    async def validate_pattern(self, pattern_id: str) -> tuple[bool, str]:
        """Mark a pattern as expert-validated by the owner."""
        try:
            self._client.table(PATTERNS_TABLE).update({
                "expert_validated": True,
                "status": "active",
                "updated_at": datetime.now().isoformat(),
            }).eq("id", pattern_id).execute()
            return True, "Pattern validated"
        except Exception as e:
            return False, str(e)

    async def deprecate_pattern(self, pattern_id: str) -> tuple[bool, str]:
        """Deprecate an outdated pattern."""
        try:
            self._client.table(PATTERNS_TABLE).update({
                "status": "deprecated",
                "updated_at": datetime.now().isoformat(),
            }).eq("id", pattern_id).execute()
            return True, "Pattern deprecated"
        except Exception as e:
            return False, str(e)

    def get_pattern_stats(self, project_id: str | None = None) -> tuple[bool, dict[str, Any]]:
        try:
            query = self._client.table(PATTERNS_TABLE).select("*")
            if project_id:
                query = query.eq("project_id", project_id)
            resp = query.execute()
            data = resp.data or []

            by_category: dict[str, int] = {}
            by_status: dict[str, int] = {}
            promoted = 0
            validated = 0

            for item in data:
                cat = item.get("category", "unknown")
                by_category[cat] = by_category.get(cat, 0) + 1
                st = item.get("status", "unknown")
                by_status[st] = by_status.get(st, 0) + 1
                if item.get("status") == "promoted":
                    promoted += 1
                if item.get("expert_validated"):
                    validated += 1

            # Top patterns by usage
            top = sorted(data, key=lambda x: x.get("usage_count", 0), reverse=True)[:5]
            top_patterns = [
                {"id": p["id"], "pattern_name": p.get("pattern_name"), "usage_count": p.get("usage_count", 0)}
                for p in top
            ]

            return True, {
                "total": len(data),
                "promoted": promoted,
                "validated": validated,
                "by_category": by_category,
                "by_status": by_status,
                "top_patterns": top_patterns,
            }
        except Exception as e:
            return False, {"error": str(e)}

    def get_relevant_learnings(
        self,
        project_id: str | None,
        task_keywords: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Fetch relevant learnings for prompt injection.

        Queries pending/promoted learnings for the project, applies confidence
        decay (observed/inferred lose 1 point per 30 days), filters out stale
        entries below CONFIDENCE_FLOOR, and returns top learnings sorted by
        effective confidence then recurrence count.
        """
        try:
            query = (
                self._client.table(TABLE)
                .select("*")
                .in_("status", ["pending", "promoted"])
            )
            if project_id:
                query = query.eq("project_id", project_id)
            query = query.order("recurrence_count", desc=True).limit(limit * 3)
            resp = query.execute()
            results = resp.data or []

            if not results:
                return []

            now = datetime.now()

            # Apply confidence decay and filter stale learnings
            alive = []
            for learning in results:
                eff_conf = _effective_confidence(learning, now)
                if eff_conf >= CONFIDENCE_FLOOR:
                    learning["effective_confidence"] = eff_conf
                    alive.append(learning)
            results = alive

            # Filter by keyword overlap if task keywords provided
            if task_keywords:
                task_words = {w.lower() for w in task_keywords if len(w) > 2}
                if task_words:
                    scored = []
                    for learning in results:
                        desc_words = set(_normalize(learning.get("description", "")).split())
                        overlap = len(task_words & desc_words)
                        scored.append((overlap, learning))
                    # Sort by overlap desc, then effective confidence, then recurrence
                    scored.sort(
                        key=lambda x: (
                            x[0],
                            x[1].get("effective_confidence", 5),
                            x[1].get("recurrence_count", 0),
                        ),
                        reverse=True,
                    )
                    results = [item for _, item in scored]

            return results[:limit]
        except Exception as e:
            logger.error(f"Failed to fetch relevant learnings: {e}")
            return []

    def get_relevant_patterns(
        self, project_id: str | None, limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Fetch active/promoted patterns for prompt injection."""
        try:
            query = (
                self._client.table(PATTERNS_TABLE)
                .select("*")
                .in_("status", ["active", "promoted"])
                .gte("confidence", 0.7)
            )
            if project_id:
                query = query.eq("project_id", project_id)
            query = query.order("usage_count", desc=True).limit(limit)
            resp = query.execute()
            return resp.data or []
        except Exception as e:
            logger.error(f"Failed to fetch relevant patterns: {e}")
            return []

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

            # Fetch learning for log context
            resp = self._client.table(TABLE).select("*").eq("id", learning_id).execute()
            if resp.data:
                await self._log_promotion(resp.data[0], promotion_type="manual", promoted_to="KB_manual")

            return True, "Learning promoted"
        except Exception as e:
            return False, str(e)
