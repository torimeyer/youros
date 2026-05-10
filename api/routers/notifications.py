"""Notifications router.

Provides persistent notifications stored in ~/.myos/notifications.json.
These survive app restarts. The bell in the UI polls the unread count
and fetches the full list when opened. A WebSocket endpoint pushes
real-time snapshots and deltas to connected clients.
"""

import asyncio

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from services import recent_deletes
from services.notifications import notifications_service
from services.notifications_events import bus as _notifications_bus

router = APIRouter(tags=["notifications"])


def _to_ws_dict(n) -> dict:
    return {
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "message": n.body,
        "created_at": n.created_at,
        "read": n.read,
        "action_label": n.action_label,
        "action_url": n.action_url,
    }


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
    from fastapi import HTTPException
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
    from fastapi import HTTPException
    found = notifications_service.delete(notification_id)
    if not found:
        raise HTTPException(status_code=404, detail="Notification not found")
    recent_deletes.record_id(f"notification:{notification_id}")
    return {"result": "ok"}


@router.websocket("/ws/notifications")
async def notifications_ws(websocket: WebSocket):
    """Push notification snapshots and deltas to connected clients.

    On connect: sends one snapshot frame with all current notifications.
    On domain event: sends a delta frame with the changed notification.
    """
    await websocket.accept()
    try:
        snapshot = [_to_ws_dict(n) for n in notifications_service.list_all()]
        await websocket.send_json({"snapshot": snapshot})

        async with _notifications_bus.subscribe() as q:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                await websocket.send_json({"delta": event.payload})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
