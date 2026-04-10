"""
Inbox API endpoints.

Per-member/per-agent notification inbox.
Adopted from Multica inbox pattern (Workstream M-P4-06).
"""

from fastapi import APIRouter, HTTPException

from ..services.inbox_service import InboxService

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


@router.get("/{recipient_type}/{recipient_id}")
async def list_inbox(
    recipient_type: str,
    recipient_id: str,
    unread_only: bool = False,
    priority: str | None = None,
    limit: int = 50,
):
    """List inbox items for a recipient (member or agent)."""
    if recipient_type not in ("member", "agent"):
        raise HTTPException(status_code=400, detail="recipient_type must be 'member' or 'agent'")

    service = InboxService()
    success, result = service.list_items(
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        unread_only=unread_only,
        priority=priority,
        limit=limit,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/{item_id}/read")
async def mark_read(item_id: str):
    """Mark an inbox item as read."""
    service = InboxService()
    success, result = await service.mark_read(item_id)
    if not success:
        raise HTTPException(status_code=404, detail=result)
    return result


@router.post("/{recipient_type}/{recipient_id}/read-all")
async def mark_all_read(recipient_type: str, recipient_id: str):
    """Mark all unread items as read."""
    service = InboxService()
    success, result = await service.mark_all_read(recipient_type, recipient_id)
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/{item_id}/archive")
async def archive_item(item_id: str):
    """Archive an inbox item."""
    service = InboxService()
    success, result = await service.archive_item(item_id)
    if not success:
        raise HTTPException(status_code=404, detail=result)
    return result
