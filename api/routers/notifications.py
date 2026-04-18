"""Notifications router.

Provides persistent notifications stored in ~/.myos/notifications.json.
These survive app restarts. The bell in the UI polls the unread count
and fetches the full list when opened.
"""

from fastapi import APIRouter, HTTPException

from services import recent_deletes
from services.notifications import notifications_service

router = APIRouter(tags=["notifications"])


@router.get("/notifications")
async def list_notifications():
    """Return all notifications, most recent first."""
    return [n.to_dict() for n in notifications_service.list_all()]


@router.get("/notifications/unread/count")
async def unread_count():
    """Return the number of unread notifications."""
    return {"count": len(notifications_service.list_unread())}


@router.post("/notifications/{notification_id}/read")
async def mark_read(notification_id: str):
    """Mark a single notification as read."""
    found = notifications_service.mark_read(notification_id)
    if not found:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"result": "ok"}


@router.post("/notifications/read-all")
async def mark_all_read():
    """Mark all notifications as read."""
    notifications_service.mark_all_read()
    return {"result": "ok"}


@router.delete("/notifications/{notification_id}")
async def delete_notification(notification_id: str):
    """Delete a single notification permanently."""
    found = notifications_service.delete(notification_id)
    if not found:
        raise HTTPException(status_code=404, detail="Notification not found")
    recent_deletes.record_id(f"notification:{notification_id}")
    return {"result": "ok"}
