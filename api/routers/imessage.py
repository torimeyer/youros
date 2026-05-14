"""iMessage integration endpoints.

Provides access to iMessage conversations on macOS by reading
~/Library/Messages/chat.db (SQLite, read-only) and sending messages
via AppleScript.
"""

from __future__ import annotations

import asyncio
import platform as _platform

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from services import imessage as imessage_service
from services import imessage_contacts as contacts_service

router = APIRouter(tags=["imessage"])


def _require_macos():
    if _platform.system().lower() != "darwin":
        raise HTTPException(
            status_code=503,
            detail="iMessage is only available on macOS.",
        )


class SendMessageRequest(BaseModel):
    recipient: str
    text: str


class ReplyRequest(BaseModel):
    text: str


class SaveContactRequest(BaseModel):
    identifier: str
    name: str


@router.get("/imessage/status")
async def imessage_status():
    """Return whether iMessage integration is available on this machine.

    - available: True if the iMessage database exists and is readable
    - reason: explanation if not available (e.g., need Full Disk Access)
    """
    _require_macos()
    return imessage_service.is_available()


@router.get("/imessage/conversations")
async def imessage_conversations(limit: int = Query(50, ge=1, le=200)):
    """Return recent iMessage conversations sorted by last message date.

    Each conversation includes the contact name (or phone number),
    a preview of the last message, the message count, and the unread count.
    """
    _require_macos()
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
    _require_macos()
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


@router.get("/imessage/attachment")
async def imessage_attachment(path: str = Query(...)):
    """Serve an iMessage attachment file by its local path.

    Only serves files from the ~/Library/Messages/Attachments directory
    to prevent path traversal outside the iMessage store.
    """
    _require_macos()
    resolved = Path(path).resolve()
    allowed = Path.home() / "Library" / "Messages" / "Attachments"
    if not str(resolved).startswith(str(allowed)):
        raise HTTPException(status_code=403, detail="Access denied.")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Attachment not found.")
    return FileResponse(str(resolved))


@router.post("/imessage/send")
async def imessage_send(body: SendMessageRequest):
    """Send an iMessage to a phone number or email address.

    Uses AppleScript to send through the Messages app. The Messages app
    must be running (it will be launched automatically if not).
    """
    _require_macos()
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


@router.post("/imessage/conversations/{chat_id}/reply")
async def imessage_reply(chat_id: int, body: ReplyRequest):
    """Reply to an existing conversation by its ID.

    Works for both direct messages (phone/email) and group chats (UUID).
    Looks up the conversation in chat.db and sends using the appropriate method.
    """
    _require_macos()
    status = imessage_service.is_available()
    if not status["available"]:
        raise HTTPException(status_code=503, detail=status["reason"])

    try:
        result = await imessage_service.reply_to_chat(
            chat_id=chat_id,
            text=body.text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Sending the reply timed out. Make sure the Messages app is running.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not send the reply: {exc}",
        ) from exc

    imessage_service.invalidate_conversations_cache()

    return result


@router.get("/imessage/resolve-contact")
async def imessage_resolve_contact(phrase: str = Query(..., min_length=2)):
    """Resolve a contact name and message from a natural-language phrase.

    Used by the chat panel when the user types something like
    'tell lil oatmeal goodnight'. Tries different word splits and fuzzy-matches
    each candidate against known contacts.

    Returns { identifier, display_name, message_text } on success.
    Returns 404 if no matching contact is found.
    """
    _require_macos()
    status = imessage_service.is_available()
    if not status["available"]:
        raise HTTPException(status_code=503, detail=status["reason"])

    try:
        result = await imessage_service.resolve_contact_phrase(phrase)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Contact resolution timed out. Try again.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Contact resolution failed: {exc}",
        ) from exc

    return result


@router.get("/imessage/contacts/search")
async def imessage_contacts_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=20),
):
    """Search contacts by name (case-insensitive substring match).

    Returns up to `limit` matches as [{name, phone, email, identifier}].
    Returns an empty list on non-macOS platforms — no 503.
    identifier is the primary phone or email used to address a message.
    """
    try:
        results = await asyncio.to_thread(contacts_service.search_by_prefix, q, limit)
    except Exception:
        results = []
    return {"contacts": results, "query": q}


@router.post("/imessage/contacts/save")
async def imessage_save_contact(body: SaveContactRequest):
    """Save a name for a phone number or email to the local contacts cache.

    Does NOT modify macOS Contacts. The name is stored in
    ~/.myos/imessage_cache/contacts.json and takes effect on the next
    conversations refresh.
    """
    name = body.name.strip()
    identifier = body.identifier.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    if not identifier:
        raise HTTPException(status_code=400, detail="Identifier cannot be empty.")
    imessage_service.save_contact(identifier=identifier, name=name)
    return {"ok": True, "identifier": identifier, "name": name}


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
