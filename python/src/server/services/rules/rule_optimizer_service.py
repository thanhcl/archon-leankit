"""
Rule Optimizer Service — Analyzes task metrics to suggest CLAUDE.md rule changes.

Examines completed/failed tasks' retry_count, learnings, code_patterns,
and execution_result to produce rule suggestions with confidence scores.

Usage:
    optimizer = RuleOptimizerService()
    ok, result = await optimizer.optimize_rules(project_id)
"""

from collections import Counter, defaultdict
from typing import Any

from src.server.config.logfire_config import get_logger
from src.server.utils import get_supabase_client

logger = get_logger(__name__)

# Minimum tasks to produce meaningful suggestions
MIN_TASKS_FOR_ANALYSIS = 3

# Thresholds for suggestion generation
HIGH_RETRY_THRESHOLD = 2  # Tasks with retry_count >= this are "high retry"
HIGH_RETRY_RATE_THRESHOLD = 0.3  # 30% of tasks retrying triggers a suggestion
RECURRING_LEARNING_THRESHOLD = 2  # Learnings seen >= this times suggest a rule
PATTERN_CONFIDENCE_THRESHOLD = 0.8  # Code patterns above this suggest a rule
FAILURE_RATE_THRESHOLD = 0.2  # 20% failure rate triggers a suggestion

# Confidence scoring weights
BASE_CONFIDENCE = 0.5
RECURRENCE_WEIGHT = 0.1  # per recurrence above threshold
TASK_COUNT_WEIGHT = 0.05  # per supporting task
MAX_CONFIDENCE = 0.95

SUGGESTION_ACTIONS = {"add", "modify", "remove"}


def _compute_confidence(recurrence: int, supporting_tasks: int) -> float:
    """Compute confidence score for a suggestion based on evidence strength."""
    score = BASE_CONFIDENCE
    score += max(0, recurrence - 1) * RECURRENCE_WEIGHT
    score += min(supporting_tasks, 5) * TASK_COUNT_WEIGHT
    return round(min(score, MAX_CONFIDENCE), 2)


class RuleOptimizerService:
    """Analyzes task execution metrics to suggest rule optimizations."""

    def __init__(self, supabase_client=None):
        self._client = supabase_client or get_supabase_client()

    async def optimize_rules(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """
        Analyze task metrics for a project and return rule suggestions.

        Returns suggestions with action (add/modify/remove), section,
        rule_text, confidence, and supporting evidence.
        """
        try:
            tasks = self._fetch_tasks(project_id)
            if len(tasks) < MIN_TASKS_FOR_ANALYSIS:
                return True, {
                    "suggestions": [],
                    "analysis": {"total_tasks": len(tasks), "message": "Not enough tasks for analysis"},
                    "project_id": project_id,
                }

            learnings = self._fetch_learnings(project_id)
            code_patterns = self._fetch_code_patterns(project_id)
            existing_rules = self._fetch_existing_rules(project_id)

            analysis = self._analyze_tasks(tasks)
            suggestions: list[dict[str, Any]] = []

            suggestions.extend(self._suggest_from_retry_patterns(tasks, analysis, existing_rules))
            suggestions.extend(self._suggest_from_learnings(learnings, existing_rules))
            suggestions.extend(self._suggest_from_code_patterns(code_patterns, existing_rules))
            suggestions.extend(self._suggest_from_failure_patterns(tasks, analysis, existing_rules))

            # Deduplicate by section+action, keeping highest confidence
            suggestions = self._deduplicate(suggestions)

            # Sort by confidence descending
            suggestions.sort(key=lambda s: s.get("confidence", 0), reverse=True)

            return True, {
                "suggestions": suggestions,
                "analysis": analysis,
                "project_id": project_id,
            }

        except Exception as e:
            logger.error(f"Error optimizing rules for project {project_id}: {e}", exc_info=True)
            return False, {"error": f"Error optimizing rules: {str(e)}"}

    # ── Data fetching ─────────────────────────────────────────────────

    def _fetch_tasks(self, project_id: str) -> list[dict[str, Any]]:
        """Fetch tasks with execution data for analysis."""
        try:
            resp = (
                self._client.table("archon_tasks")
                .select(
                    "id, title, status, retry_count, complexity, priority, "
                    "created_at, updated_at, state_changed_at, execution_result"
                )
                .eq("project_id", project_id)
                .or_("archived.is.null,archived.is.false")
                .execute()
            )
            return resp.data or []
        except Exception as e:
            logger.error(f"Failed to fetch tasks for optimization: {e}")
            return []

    def _fetch_learnings(self, project_id: str) -> list[dict[str, Any]]:
        """Fetch learnings for the project."""
        try:
            resp = (
                self._client.table("archon_learnings")
                .select("*")
                .eq("project_id", project_id)
                .order("recurrence_count", desc=True)
                .execute()
            )
            return resp.data or []
        except Exception as e:
            logger.error(f"Failed to fetch learnings for optimization: {e}")
            return []

    def _fetch_code_patterns(self, project_id: str) -> list[dict[str, Any]]:
        """Fetch code patterns for the project."""
        try:
            resp = (
                self._client.table("archon_code_patterns")
                .select("*")
                .eq("project_id", project_id)
                .order("usage_count", desc=True)
                .execute()
            )
            return resp.data or []
        except Exception as e:
            logger.error(f"Failed to fetch code patterns for optimization: {e}")
            return []

    def _fetch_existing_rules(self, project_id: str) -> list[dict[str, Any]]:
        """Fetch existing rules (global + project-scoped) for overlap detection."""
        try:
            resp = (
                self._client.table("archon_rules")
                .select("*")
                .eq("enabled", True)
                .or_(f"project_id.is.null,project_id.eq.{project_id}")
                .execute()
            )
            return resp.data or []
        except Exception as e:
            logger.error(f"Failed to fetch existing rules: {e}")
            return []

    # ── Analysis ──────────────────────────────────────────────────────

    def _analyze_tasks(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute aggregate metrics from tasks."""
        total = len(tasks)
        done = [t for t in tasks if t.get("status") == "done"]
        failed = [t for t in tasks if t.get("status") == "failed"]
        high_retry = [t for t in tasks if (t.get("retry_count") or 0) >= HIGH_RETRY_THRESHOLD]

        total_retries = sum(t.get("retry_count", 0) for t in tasks)

        # Count by complexity
        complexity_counts: dict[str, int] = Counter(t.get("complexity", "unknown") for t in tasks)

        # Count failure areas from learnings in execution_result
        failure_areas: dict[str, int] = defaultdict(int)
        for task in failed + high_retry:
            result = task.get("execution_result")
            if isinstance(result, dict):
                for learning in result.get("learnings", []):
                    if isinstance(learning, dict):
                        area = learning.get("area", "unknown")
                        failure_areas[area] += 1

        return {
            "total_tasks": total,
            "done_count": len(done),
            "failed_count": len(failed),
            "high_retry_count": len(high_retry),
            "total_retries": total_retries,
            "avg_retries": round(total_retries / total, 2) if total else 0,
            "failure_rate": round(len(failed) / total, 4) if total else 0,
            "high_retry_rate": round(len(high_retry) / total, 4) if total else 0,
            "complexity_distribution": dict(complexity_counts),
            "failure_areas": dict(failure_areas),
        }

    # ── Suggestion generators ─────────────────────────────────────────

    def _suggest_from_retry_patterns(
        self,
        tasks: list[dict[str, Any]],
        analysis: dict[str, Any],
        existing_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Suggest rules based on high retry rates."""
        suggestions: list[dict[str, Any]] = []
        high_retry_rate = analysis.get("high_retry_rate", 0)

        if high_retry_rate < HIGH_RETRY_RATE_THRESHOLD:
            return suggestions

        # Find common areas where retries happen
        retry_areas: dict[str, list[str]] = defaultdict(list)
        for task in tasks:
            if (task.get("retry_count") or 0) >= HIGH_RETRY_THRESHOLD:
                result = task.get("execution_result")
                if isinstance(result, dict):
                    for learning in result.get("learnings", []):
                        if isinstance(learning, dict) and learning.get("area"):
                            retry_areas[learning["area"]].append(task["id"])

        for area, task_ids in retry_areas.items():
            if len(task_ids) < 2:
                continue

            section = self._area_to_section(area)
            if self._rule_exists_for_area(existing_rules, section, area):
                continue

            confidence = _compute_confidence(len(task_ids), len(task_ids))
            suggestions.append({
                "action": "add",
                "section": section,
                "rule_text": (
                    f"Tasks in the '{area}' area frequently require retries. "
                    f"Add explicit validation and testing steps for {area} changes."
                ),
                "confidence": confidence,
                "reason": f"{len(task_ids)} tasks required {HIGH_RETRY_THRESHOLD}+ retries in '{area}' area",
                "evidence": {"task_ids": task_ids[:5], "area": area, "high_retry_rate": high_retry_rate},
            })

        return suggestions

    def _suggest_from_learnings(
        self,
        learnings: list[dict[str, Any]],
        existing_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Suggest rules from recurring learnings."""
        suggestions: list[dict[str, Any]] = []

        for learning in learnings:
            recurrence = learning.get("recurrence_count", 1)
            if recurrence < RECURRING_LEARNING_THRESHOLD:
                continue

            suggested_rule = learning.get("suggested_rule")
            if not suggested_rule:
                continue

            section = self._area_to_section(learning.get("area", ""))
            if self._text_overlaps_existing(existing_rules, suggested_rule):
                continue

            related_tasks = learning.get("related_tasks") or []
            confidence = _compute_confidence(recurrence, len(related_tasks))

            suggestions.append({
                "action": "add",
                "section": section,
                "rule_text": suggested_rule,
                "confidence": confidence,
                "reason": (
                    f"Learning recurred {recurrence} times across {len(related_tasks)} tasks: "
                    f"{learning.get('description', '')[:100]}"
                ),
                "evidence": {
                    "learning_id": learning.get("id"),
                    "type": learning.get("type"),
                    "recurrence_count": recurrence,
                    "related_tasks": related_tasks[:5],
                },
            })

        return suggestions

    def _suggest_from_code_patterns(
        self,
        code_patterns: list[dict[str, Any]],
        existing_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Suggest rules from high-confidence, frequently-used code patterns."""
        suggestions: list[dict[str, Any]] = []

        for pattern in code_patterns:
            confidence = pattern.get("confidence", 0)
            usage = pattern.get("usage_count", 0)

            if confidence < PATTERN_CONFIDENCE_THRESHOLD or usage < 2:
                continue

            category = pattern.get("category", "")
            section = self._category_to_section(category)
            pattern_name = pattern.get("pattern_name", "Unknown")

            if self._text_overlaps_existing(existing_rules, pattern_name):
                continue

            suggestions.append({
                "action": "add",
                "section": section,
                "rule_text": (
                    f"Follow the '{pattern_name}' pattern for {category} implementations. "
                    f"Context: {pattern.get('context', '')[:150]}"
                ),
                "confidence": round(min(confidence, MAX_CONFIDENCE), 2),
                "reason": (
                    f"Code pattern '{pattern_name}' used {usage} times "
                    f"with {confidence:.0%} confidence"
                ),
                "evidence": {
                    "pattern_id": pattern.get("id"),
                    "category": category,
                    "usage_count": usage,
                    "pattern_confidence": confidence,
                },
            })

        return suggestions

    def _suggest_from_failure_patterns(
        self,
        tasks: list[dict[str, Any]],
        analysis: dict[str, Any],
        existing_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Suggest rules based on task failure patterns."""
        suggestions: list[dict[str, Any]] = []

        if analysis.get("failure_rate", 0) < FAILURE_RATE_THRESHOLD:
            return suggestions

        failure_areas = analysis.get("failure_areas", {})
        if not failure_areas:
            # Generic high failure rate suggestion
            section = "validation"
            if not self._rule_exists_for_area(existing_rules, section, "failure"):
                confidence = _compute_confidence(analysis["failed_count"], analysis["failed_count"])
                suggestions.append({
                    "action": "add",
                    "section": section,
                    "rule_text": (
                        "High task failure rate detected. Add pre-execution validation "
                        "checklist and ensure acceptance criteria are clear before starting tasks."
                    ),
                    "confidence": confidence,
                    "reason": (
                        f"{analysis['failed_count']}/{analysis['total_tasks']} tasks failed "
                        f"({analysis['failure_rate']:.0%} failure rate)"
                    ),
                    "evidence": {"failure_rate": analysis["failure_rate"], "failed_count": analysis["failed_count"]},
                })
            return suggestions

        # Area-specific failure suggestions
        for area, count in sorted(failure_areas.items(), key=lambda x: x[1], reverse=True):
            section = self._area_to_section(area)
            if self._rule_exists_for_area(existing_rules, section, area):
                continue

            confidence = _compute_confidence(count, count)
            suggestions.append({
                "action": "add",
                "section": section,
                "rule_text": (
                    f"Frequent failures in '{area}' area. Add dedicated review step "
                    f"and testing requirements for {area} changes."
                ),
                "confidence": confidence,
                "reason": f"{count} task failures attributed to '{area}' area",
                "evidence": {"area": area, "failure_count": count},
            })

        return suggestions

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _area_to_section(area: str) -> str:
        """Map a learning area to a rule section."""
        mapping = {
            "frontend": "coding-style",
            "backend": "coding-style",
            "infra": "architecture",
            "tests": "testing",
            "config": "integration",
            "security": "security",
            "database": "architecture",
        }
        return mapping.get(area, "validation")

    @staticmethod
    def _category_to_section(category: str) -> str:
        """Map a code pattern category to a rule section."""
        mapping = {
            "security": "security",
            "error-handling": "validation",
            "testing": "testing",
            "architecture": "architecture",
            "performance": "performance",
            "api-design": "coding-style",
        }
        return mapping.get(category, "coding-style")

    @staticmethod
    def _rule_exists_for_area(rules: list[dict[str, Any]], section: str, area: str) -> bool:
        """Check if a rule already covers a given section+area combination."""
        area_lower = area.lower()
        for rule in rules:
            if rule.get("section") == section and area_lower in rule.get("rule_text", "").lower():
                return True
        return False

    @staticmethod
    def _text_overlaps_existing(rules: list[dict[str, Any]], text: str) -> bool:
        """Check if suggested text significantly overlaps with existing rules."""
        text_lower = text.lower()
        text_words = set(text_lower.split())
        if len(text_words) < 3:
            return False

        for rule in rules:
            rule_words = set(rule.get("rule_text", "").lower().split())
            if not rule_words:
                continue
            overlap = len(text_words & rule_words) / len(text_words)
            if overlap > 0.6:
                return True
        return False

    @staticmethod
    def _deduplicate(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep only the highest-confidence suggestion per section+action."""
        best: dict[str, dict[str, Any]] = {}
        for s in suggestions:
            key = f"{s['section']}:{s['action']}"
            if key not in best or s.get("confidence", 0) > best[key].get("confidence", 0):
                best[key] = s
        return list(best.values())
