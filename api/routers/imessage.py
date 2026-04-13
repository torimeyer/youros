"""iMessage integration endpoints.

Provides access to iMessage conversations on macOS by reading
~/Library/Messages/chat.db (SQLite, read-only) and sending messages
via AppleScript.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services import imessage as imessage_service

router = APIRouter(tags=["imessage"])


class SendMessageRequest(BaseModel):
    recipient: str
    text: str


@router.get("/imessage/status")
async def imessage_status():
    """Return whether iMessage integration is available on this machine.

    - available: True if the iMessage database exists and is readable
    - reason: explanation if not available (e.g., need Full Disk Access)
    """
    return imessage_service.is_available()


@router.get("/imessage/conversations")
async def imessage_conversations(limit: int = Query(50, ge=1, le=200)):
    """Return recent iMessage conversations sorted by last message date.

    Each conversation includes the contact name (or phone number),
    a preview of the last message, the message count, and the unread count.
    """
    status = imessage_service.is_available()
    if not status["available"]:
        raise HTTPException(status_code=503, detail=status["reason"])

    try:
        conversations = await imessage_service.get_conversations(limit=limit)
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Reading iMessage conversations took too long. Try again.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not load iMessage conversations: {exc}",
        ) from exc

    return {"conversations": conversations}


@router.get("/imessage/conversations/{chat_id}/messages")
async def imessage_messages(chat_id: int, limit: int = Query(100, ge=1, le=500)):
    """Return messages in a specific conversation.

    Messages are sorted oldest first (natural reading order).
    """
    status = imessage_service.is_available()
    if not status["available"]:
        raise HTTPException(status_code=503, detail=status["reason"])

    try:
        messages = await imessage_service.get_messages(chat_id=chat_id, limit=limit)
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Reading messages took too long. Try again.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not load messages: {exc}",
        ) from exc

    return {"messages": messages}


@router.post("/imessage/send")
async def imessage_send(body: SendMessageRequest):
    """Send an iMessage to a phone number or email address.

    Uses AppleScript to send through the Messages app. The Messages app
    must be running (it will be launched automatically if not).
    """
    status = imessage_service.is_available()
    if not status["available"]:
        raise HTTPException(status_code=503, detail=status["reason"])

    try:
        result = await imessage_service.send_message(
            recipient=body.recipient,
            text=body.text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Sending the message timed out. Make sure the Messages app is running.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not send the message: {exc}",
        ) from exc

    # Invalidate conversations cache so the sent message shows up
    imessage_service.invalidate_conversations_cache()

    return result


@router.get("/imessage/search")
async def imessage_search(q: str = Query(..., min_length=2), limit: int = Query(50, ge=1, le=200)):
    """Search iMessage history by text content.

    Returns matching messages with their conversation context.
    """
    status = imessage_service.is_available()
    if not status["available"]:
        raise HTTPException(status_code=503, detail=status["reason"])

    try:
        results = await imessage_service.search_messages(query=q, limit=limit)
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Search took too long. Try a more specific search term.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {exc}",
        ) from exc

    return {"results": results, "query": q}
