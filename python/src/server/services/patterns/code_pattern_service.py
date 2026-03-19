"""
Code Pattern Service

Manages expert-level code patterns extracted from completed tasks.
Supports CRUD, similarity search by pattern_key, and usage tracking.
"""

from datetime import datetime
from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

TABLE = "archon_code_patterns"


class CodePatternService:
    """Service class for code pattern operations."""

    def __init__(self, supabase_client=None):
        """Initialize with optional supabase client for dependency injection."""
        self.supabase_client = supabase_client or get_supabase_client()

    # ============================================================================
    # CREATE
    # ============================================================================

    def create_pattern(
        self,
        project_id: str,
        pattern_name: str,
        pattern_key: str,
        category: str,
        code_example: str,
        context: str,
        *,
        language: str = "java",
        anti_pattern: str | None = None,
        source_task_ids: list[str] | None = None,
        source_files: list[str] | None = None,
        confidence: float = 0.7,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Create a new code pattern.

        Returns:
            Tuple of (success, result_dict)
        """
        try:
            valid_categories = {"security", "error-handling", "testing", "architecture", "performance", "api-design"}
            if category not in valid_categories:
                return False, {"error": f"Invalid category '{category}'. Must be one of: {', '.join(sorted(valid_categories))}"}

            if not pattern_name or not pattern_key or not code_example or not context:
                return False, {"error": "pattern_name, pattern_key, code_example, and context are required"}

            now = datetime.now().isoformat()
            pattern_data = {
                "project_id": project_id,
                "pattern_name": pattern_name.strip(),
                "pattern_key": pattern_key.strip(),
                "category": category,
                "language": language,
                "code_example": code_example,
                "context": context,
                "anti_pattern": anti_pattern,
                "source_task_ids": source_task_ids or [],
                "source_files": source_files or [],
                "confidence": confidence,
                "status": "pending",
                "usage_count": 1,
                "created_at": now,
                "updated_at": now,
            }

            response = self.supabase_client.table(TABLE).insert(pattern_data).execute()

            if not response.data:
                logger.error("Supabase returned empty data for pattern insert")
                return False, {"error": "Failed to create pattern"}

            return True, {"pattern": response.data[0]}

        except Exception as e:
            logger.error(f"Error creating pattern: {e}", exc_info=True)
            return False, {"error": f"Database error: {str(e)}"}

    # ============================================================================
    # READ
    # ============================================================================

    def find_similar_pattern(self, pattern_key: str) -> tuple[bool, dict[str, Any]]:
        """
        Find patterns matching the given pattern_key.

        Args:
            pattern_key: The dedup key (e.g., security.java.pkcs11-session-management)

        Returns:
            Tuple of (success, result_dict with 'patterns' list)
        """
        try:
            response = (
                self.supabase_client.table(TABLE)
                .select("*")
                .eq("pattern_key", pattern_key)
                .order("usage_count", desc=True)
                .execute()
            )

            return True, {"patterns": response.data or [], "count": len(response.data or [])}

        except Exception as e:
            logger.error(f"Error finding similar pattern: {e}", exc_info=True)
            return False, {"error": f"Database error: {str(e)}"}

    def list_patterns(
        self,
        project_id: str | None = None,
        category: str | None = None,
        status: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """
        List patterns with optional filters.

        Args:
            project_id: Filter by project
            category: Filter by category
            status: Filter by status
        """
        try:
            query = self.supabase_client.table(TABLE).select("*")

            if project_id:
                query = query.eq("project_id", project_id)
            if category:
                query = query.eq("category", category)
            if status:
                query = query.eq("status", status)

            response = query.order("usage_count", desc=True).execute()

            return True, {"patterns": response.data or [], "count": len(response.data or [])}

        except Exception as e:
            logger.error(f"Error listing patterns: {e}", exc_info=True)
            return False, {"error": f"Database error: {str(e)}"}

    # ============================================================================
    # UPDATE
    # ============================================================================

    def increment_usage(self, pattern_id: str) -> tuple[bool, dict[str, Any]]:
        """
        Increment usage_count for a pattern and update last_used_at.

        Args:
            pattern_id: UUID of the pattern to increment
        """
        try:
            # Fetch current usage_count
            get_response = (
                self.supabase_client.table(TABLE)
                .select("id, usage_count")
                .eq("id", pattern_id)
                .execute()
            )

            if not get_response.data:
                return False, {"error": f"Pattern with ID {pattern_id} not found"}

            current_count = get_response.data[0].get("usage_count", 0)
            now = datetime.now().isoformat()

            update_response = (
                self.supabase_client.table(TABLE)
                .update({
                    "usage_count": current_count + 1,
                    "last_used_at": now,
                    "updated_at": now,
                })
                .eq("id", pattern_id)
                .execute()
            )

            if update_response.data:
                return True, {"pattern": update_response.data[0]}

            # Fetch to confirm
            confirm = (
                self.supabase_client.table(TABLE)
                .select("*")
                .eq("id", pattern_id)
                .execute()
            )
            if confirm.data:
                return True, {"pattern": confirm.data[0]}

            return False, {"error": "Failed to increment usage"}

        except Exception as e:
            logger.error(f"Error incrementing usage: {e}", exc_info=True)
            return False, {"error": f"Database error: {str(e)}"}

    # ============================================================================
    # STATS
    # ============================================================================

    def get_stats(self, project_id: str | None = None) -> tuple[bool, dict[str, Any]]:
        """
        Get aggregate statistics for code patterns.

        Args:
            project_id: Optional project filter
        """
        try:
            query = self.supabase_client.table(TABLE).select("*")

            if project_id:
                query = query.eq("project_id", project_id)

            response = query.execute()
            patterns = response.data or []

            # Calculate stats locally to avoid N+1
            stats: dict[str, Any] = {
                "total": len(patterns),
                "by_category": {},
                "by_status": {},
                "by_language": {},
                "avg_confidence": 0.0,
                "total_usage": 0,
            }

            if patterns:
                for p in patterns:
                    cat = p.get("category", "unknown")
                    stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1

                    st = p.get("status", "unknown")
                    stats["by_status"][st] = stats["by_status"].get(st, 0) + 1

                    lang = p.get("language", "unknown")
                    stats["by_language"][lang] = stats["by_language"].get(lang, 0) + 1

                    stats["total_usage"] += p.get("usage_count", 0)

                confidences = [p.get("confidence", 0) for p in patterns]
                stats["avg_confidence"] = round(sum(confidences) / len(confidences), 2)

            return True, {"stats": stats}

        except Exception as e:
            logger.error(f"Error getting stats: {e}", exc_info=True)
            return False, {"error": f"Database error: {str(e)}"}
