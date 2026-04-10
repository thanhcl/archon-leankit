"""
Inbox Service for Archon.

Per-member/per-agent notification inbox. Subscribes to internal event bus
to automatically create inbox items for assignments, reviews, blockers, etc.

Adopted from Multica inbox pattern (Workstream M-P4-06).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.server.utils import get_supabase_client

from ..config.logfire_config import get_logger
from .event_bus import (
    TOPIC_ASSIGNMENT_CHANGED,
    TOPIC_COMMENT_CREATED,
    TOPIC_MENTION,
    TOPIC_TASK_COMPLETED,
    TOPIC_TASK_FAILED,
    BusEvent,
    EventBus,
)

logger = get_logger(__name__)


ITEM_TYPE_ASSIGNMENT = "assignment"
ITEM_TYPE_MENTION = "mention"
ITEM_TYPE_REVIEW_REQUEST = "review_request"
ITEM_TYPE_BLOCKER = "blocker"
ITEM_TYPE_PROGRESS = "progress_update"
ITEM_TYPE_COMPLETION = "completion"
ITEM_TYPE_FAILURE = "failure"
ITEM_TYPE_ESCALATION = "escalation"
ITEM_TYPE_COMMENT = "comment"
ITEM_TYPE_SYSTEM = "system"

PRIORITY_ACTION = "action_required"
PRIORITY_ATTENTION = "attention"
PRIORITY_INFO = "info"


class InboxService:
    """Service for managing inbox notifications."""

    TABLE = "archon_inbox_items"

    def __init__(self, supabase_client: Any = None) -> None:
        self.supabase_client = supabase_client or get_supabase_client()

    async def create_item(
        self,
        recipient_type: str,
        recipient_id: str,
        item_type: str,
        title: str,
        body: str | None = None,
        priority: str = PRIORITY_INFO,
        source_task_id: str | None = None,
        source_run_id: str | None = None,
        source_project_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Create an inbox notification item."""
        try:
            record = {
                "id": str(uuid4()),
                "recipient_type": recipient_type,
                "recipient_id": recipient_id,
                "item_type": item_type,
                "title": title,
                "body": body,
                "priority": priority,
                "source_task_id": source_task_id,
                "source_run_id": source_run_id,
                "source_project_id": source_project_id,
                "data": data or {},
            }

            result = self.supabase_client.table(self.TABLE).insert(record).execute()
            if not result.data:
                return False, {"error": "Failed to create inbox item"}

            return True, {"item": result.data[0]}

        except Exception as e:
            logger.error(f"Inbox item creation failed: {e}", exc_info=True)
            return False, {"error": str(e)}

    def list_items(
        self,
        recipient_type: str,
        recipient_id: str,
        unread_only: bool = False,
        priority: str | None = None,
        limit: int = 50,
    ) -> tuple[bool, dict[str, Any]]:
        """List inbox items for a recipient."""
        try:
            query = (
                self.supabase_client.table(self.TABLE)
                .select("*")
                .eq("recipient_type", recipient_type)
                .eq("recipient_id", recipient_id)
                .is_("archived_at", "null")
                .order("created_at", desc=True)
                .limit(limit)
            )

            if unread_only:
                query = query.is_("read_at", "null")
            if priority:
                query = query.eq("priority", priority)

            result = query.execute()
            items = result.data or []

            return True, {
                "items": items,
                "total_count": len(items),
                "unread_count": sum(1 for i in items if i.get("read_at") is None),
            }

        except Exception as e:
            logger.error(f"Inbox list failed: {e}", exc_info=True)
            return False, {"error": str(e)}

    async def mark_read(self, item_id: str) -> tuple[bool, dict[str, Any]]:
        """Mark an inbox item as read."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            result = (
                self.supabase_client.table(self.TABLE)
                .update({"read_at": now})
                .eq("id", item_id)
                .execute()
            )
            if not result.data:
                return False, {"error": f"Item {item_id} not found"}
            return True, {"item": result.data[0]}
        except Exception as e:
            return False, {"error": str(e)}

    async def mark_all_read(
        self, recipient_type: str, recipient_id: str
    ) -> tuple[bool, dict[str, Any]]:
        """Mark all unread items as read for a recipient."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            result = (
                self.supabase_client.table(self.TABLE)
                .update({"read_at": now})
                .eq("recipient_type", recipient_type)
                .eq("recipient_id", recipient_id)
                .is_("read_at", "null")
                .execute()
            )
            return True, {"marked_count": len(result.data or [])}
        except Exception as e:
            return False, {"error": str(e)}

    async def archive_item(self, item_id: str) -> tuple[bool, dict[str, Any]]:
        """Archive an inbox item."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            result = (
                self.supabase_client.table(self.TABLE)
                .update({"archived_at": now})
                .eq("id", item_id)
                .execute()
            )
            if not result.data:
                return False, {"error": f"Item {item_id} not found"}
            return True, {"item": result.data[0]}
        except Exception as e:
            return False, {"error": str(e)}


class InboxEventListener:
    """Listens to EventBus and creates inbox items automatically.

    Register with:
        listener = InboxEventListener()
        listener.register(bus)
    """

    def __init__(self, inbox_service: InboxService | None = None) -> None:
        self._inbox = inbox_service or InboxService()

    def register(self, bus: EventBus) -> None:
        """Register handlers for relevant events."""
        bus.on(TOPIC_ASSIGNMENT_CHANGED, self._on_assignment)
        bus.on(TOPIC_TASK_COMPLETED, self._on_task_completed)
        bus.on(TOPIC_TASK_FAILED, self._on_task_failed)
        bus.on(TOPIC_MENTION, self._on_mention)
        bus.on(TOPIC_COMMENT_CREATED, self._on_comment)

    async def _on_assignment(self, event: BusEvent) -> None:
        """Create inbox item when task is assigned."""
        data = event.data
        assignee_type = data.get("assignee_type", "")
        assignee_id = data.get("assignee_id")

        if not assignee_id or assignee_type == "unassigned":
            return

        recipient_type = "agent" if assignee_type == "agent" else "member"
        title = data.get("title", "Untitled task")

        await self._inbox.create_item(
            recipient_type=recipient_type,
            recipient_id=assignee_id,
            item_type=ITEM_TYPE_ASSIGNMENT,
            title=f"Task assigned: {title}",
            priority=PRIORITY_ACTION,
            source_task_id=data.get("task_id"),
            data=data,
        )

    async def _on_task_completed(self, event: BusEvent) -> None:
        """Notify assignee on task completion."""
        data = event.data
        assignee_id = data.get("assignee_id")
        if not assignee_id:
            return

        await self._inbox.create_item(
            recipient_type=data.get("assignee_type", "member"),
            recipient_id=assignee_id,
            item_type=ITEM_TYPE_COMPLETION,
            title=f"Task completed: {data.get('title', '?')}",
            priority=PRIORITY_INFO,
            source_task_id=data.get("task_id"),
            data=data,
        )

    async def _on_task_failed(self, event: BusEvent) -> None:
        """Notify assignee on task failure."""
        data = event.data
        assignee_id = data.get("assignee_id")
        if not assignee_id:
            return

        await self._inbox.create_item(
            recipient_type=data.get("assignee_type", "member"),
            recipient_id=assignee_id,
            item_type=ITEM_TYPE_FAILURE,
            title=f"Task failed: {data.get('title', '?')}",
            priority=PRIORITY_ATTENTION,
            source_task_id=data.get("task_id"),
            data=data,
        )

    async def _on_mention(self, event: BusEvent) -> None:
        """Create inbox item for mentions."""
        data = event.data
        mentioned_id = data.get("mentioned_id")
        if not mentioned_id:
            return

        await self._inbox.create_item(
            recipient_type=data.get("mentioned_type", "member"),
            recipient_id=mentioned_id,
            item_type=ITEM_TYPE_MENTION,
            title=f"You were mentioned in {data.get('context', 'a discussion')}",
            priority=PRIORITY_ATTENTION,
            source_task_id=data.get("task_id"),
            data=data,
        )

    async def _on_comment(self, event: BusEvent) -> None:
        """Create inbox item for comments on assigned tasks."""
        data = event.data
        task_assignee_id = data.get("task_assignee_id")
        comment_author_id = data.get("author_id")

        # Don't notify the comment author about their own comment
        if not task_assignee_id or task_assignee_id == comment_author_id:
            return

        await self._inbox.create_item(
            recipient_type=data.get("task_assignee_type", "member"),
            recipient_id=task_assignee_id,
            item_type=ITEM_TYPE_COMMENT,
            title=f"New comment on {data.get('task_title', 'your task')}",
            priority=PRIORITY_INFO,
            source_task_id=data.get("task_id"),
            data=data,
        )
