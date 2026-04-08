"""Gmail integration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.google_auth import get_email, is_authenticated
from services import gmail as gmail_service

router = APIRouter(tags=["gmail"])


@router.get("/gmail/auth/status")
async def gmail_auth_status():
    """Return whether the user has connected Gmail.

    - authenticated: True if a Google token exists.
    - needs_reauth: True if the token exists but the Gmail scope is missing.
    - email: the connected account email, if available.
    - unread_count: number of unread messages, or 0 if not authenticated.
    """
    authed = is_authenticated()
    email = get_email() if authed else None
    reauth = False
    unread_count = 0

    if authed:
        try:
            reauth = await gmail_service.needs_reauth()
        except Exception:
            reauth = False

        if not reauth:
            try:
                messages = await gmail_service.get_unread_summary()
                unread_count = len(messages)
            except Exception:
                unread_count = 0

    return {
        "authenticated": authed,
        "needs_reauth": reauth,
        "email": email,
        "unread_count": unread_count,
    }


@router.get("/gmail/messages")
async def gmail_messages():
    """Return unread inbox message summaries.

    Returns 401 if not authenticated. Returns 403 with needs_reauth=true if
    the Gmail scope is missing on the current token.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    try:
        messages = await gmail_service.get_unread_summary()
    except Exception as exc:
        msg = str(exc).lower()
        if "insufficient" in msg or "403" in msg:
            raise HTTPException(
                status_code=403,
                detail={"needs_reauth": True, "message": str(exc)},
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"Could not load Gmail messages: {exc}",
        ) from exc

    return {"messages": messages}


@router.post("/gmail/sync")
async def gmail_sync():
    """Clear the inbox cache and re-fetch from Gmail."""
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    gmail_service._clear_cache()

    try:
        messages = await gmail_service.get_unread_summary()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Sync failed: {exc}",
        ) from exc

    return {"ok": True, "count": len(messages)}


@router.post("/gmail/messages/{message_id}/read")
async def gmail_mark_read(message_id: str):
    """Mark a single Gmail message as read."""
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    try:
        await gmail_service.mark_read(message_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not mark message as read: {exc}",
        ) from exc

    return {"ok": True}
