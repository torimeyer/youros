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

    Uses get_unread_summary as the single probe. A successful call means the
    scope is fine and gives us the unread count for free. A scope error means
    needs_reauth. One Gmail round trip on cold, zero on warm cache.
    """
    authed = is_authenticated()
    email = get_email() if authed else None
    reauth = False
    unread_count = 0

    if authed:
        try:
            messages = await gmail_service.get_unread_summary()
            unread_count = len(messages)
        except Exception as exc:
            msg = str(exc).lower()
            # API-not-enabled is a GCP setup issue, not a scope problem.
            if "accessnotconfigured" in msg or "has not been used" in msg:
                reauth = False
            elif "insufficientpermissions" in msg or "insufficient authentication scopes" in msg:
                reauth = True
            else:
                reauth = False

    return {
        "authenticated": authed,
        "needs_reauth": reauth,
        "email": email,
        "unread_count": unread_count,
    }


@router.get("/gmail/messages")
async def gmail_messages():
    """Return recent inbox message summaries (read AND unread).

    Pulls the full inbox up to ``FULL_INBOX_CAP`` messages and returns
    them newest first. The response shape is ``{"messages": [...]}`` and
    each message includes ``is_unread`` so the client can render a badge
    or dot for unread items.

    Returns 401 if not authenticated. Returns 403 with needs_reauth=true
    if the Gmail scope is missing on the current token. Never silently
    swallows errors into an empty list.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    try:
        messages = await gmail_service.get_inbox_messages()
    except Exception as exc:
        msg = str(exc).lower()
        if "accessnotconfigured" in msg or "has not been used" in msg:
            raise HTTPException(
                status_code=403,
                detail={"needs_reauth": False, "api_not_enabled": True, "message": "Gmail API is not enabled in your Google Cloud project. Enable it in Google Cloud Console, then wait a minute and reload."},
            ) from exc
        if "insufficientpermissions" in msg or "insufficient authentication scopes" in msg:
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
    """Clear the inbox caches and re-fetch from Gmail.

    Invalidates both the unread-summary cache and the full inbox cache so
    the next fetch round trip is guaranteed to hit the Gmail API.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    gmail_service._clear_cache()
    gmail_service.invalidate_full_inbox_cache()

    try:
        messages = await gmail_service.get_inbox_messages()
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
