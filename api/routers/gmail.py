"""Gmail integration endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.google_auth import get_email, is_authenticated
from services import gmail as gmail_service
from services import connections_cache

router = APIRouter(tags=["gmail"])

# Cache key for the status payload. The cache TTL is short, and any
# connect/disconnect flow invalidates every google_* key so a user sees
# state change immediately.
_GMAIL_STATUS_CACHE_KEY = "gmail_auth_status"


class EmailToTaskRequest(BaseModel):
    message_id: str


class BatchDeleteRequest(BaseModel):
    ids: list[str]
    permanent: bool = False


class BatchMarkReadRequest(BaseModel):
    ids: list[str]


def _compute_gmail_status() -> dict:
    """Pure function that resolves the Gmail status payload from disk."""
    authed = is_authenticated()
    email = get_email() if authed else None
    reauth = False
    unread_count = 0

    if authed:
        # Fast path: check scope by validating the token, not by calling
        # the Gmail API. The unread count is fetched by /gmail/messages
        # anyway, so we skip the duplicate round trip here.
        try:
            from services.google_auth import has_gmail_scope, TOKEN_PATH
            import json as _json
            if not has_gmail_scope():
                reauth = True
            else:
                # Check the revoked flag written by _refresh_if_needed when
                # Google rejects the refresh token with invalid_grant. Lets
                # the status endpoint report needs_reauth without any network call.
                try:
                    _tok = _json.loads(TOKEN_PATH.read_text())
                    if _tok.get("revoked"):
                        reauth = True
                except Exception:
                    pass
        except Exception:
            pass

    return {
        "authenticated": authed,
        "needs_reauth": reauth,
        "email": email,
        "unread_count": unread_count,
    }


@router.get("/gmail/auth/status")
async def gmail_auth_status():
    """Return whether the user has connected Gmail.

    - authenticated: True if a Google token exists.
    - needs_reauth: True if the token exists but the Gmail scope is missing.
    - email: the connected account email, if available.
    - unread_count: number of unread messages, or 0 if not authenticated.

    Payload is served from an in-memory TTL cache so repeat polls within
    the same minute return in sub-millisecond time. Any connect, disconnect,
    or scope-update path must call
    ``connections_cache.invalidate(_GMAIL_STATUS_CACHE_KEY)`` so the UI sees
    the change on its next poll.
    """
    return connections_cache.get_or_compute(
        _GMAIL_STATUS_CACHE_KEY,
        _compute_gmail_status,
    )


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
    except asyncio.TimeoutError as exc:
        # Gmail API took longer than our 25 second ceiling and we have
        # no stale cache to fall back to. Tell the user something
        # useful instead of a generic 500.
        raise HTTPException(
            status_code=504,
            detail="Gmail is slow to respond right now. Try again in a moment.",
        ) from exc
    except Exception as exc:
        msg = str(exc).lower()
        if "invalid_grant" in msg:
            # Refresh token was revoked by Google. Invalidate the cached auth
            # status so the next poll reflects reality and show a reconnect
            # prompt instead of a generic error.
            connections_cache.invalidate(_GMAIL_STATUS_CACHE_KEY)
            raise HTTPException(
                status_code=403,
                detail={"needs_reauth": True, "message": "Your Google account connection has expired. Reconnect to see your inbox."},
            ) from exc
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
        # Preserve any non-empty exception message; fall back to a
        # generic hint when the exception chain provides nothing
        # useful (empty-string TimeoutError, for example).
        reason = str(exc).strip() or "Gmail did not respond."
        raise HTTPException(
            status_code=500,
            detail=f"Could not load Gmail messages: {reason}",
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


@router.post("/gmail/to-task")
async def gmail_to_task(body: EmailToTaskRequest):
    """Convert a Gmail message into a task.

    Reads the email content, uses Claude to extract a concise task title
    and description, then creates the task via ostk.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    try:
        email = await gmail_service.get_message_content(body.message_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not read the email: {exc}",
        ) from exc

    # Use Claude to extract a task title and description from the email.
    from services.meeting_prep import _call_claude

    subject = email.get("subject", "(no subject)")
    sender = email.get("from_name") or email.get("from_email", "")
    body_text = email.get("body") or email.get("snippet", "")
    # Truncate very long emails so the prompt stays reasonable.
    if len(body_text) > 2000:
        body_text = body_text[:2000] + "..."

    prompt = (
        f"I received an email from {sender} with subject: {subject}\n\n"
        f"Email body:\n{body_text}\n\n"
        "Extract a clear, actionable task from this email. Return EXACTLY two lines:\n"
        "Line 1: A short task title (under 80 characters)\n"
        "Line 2: A one-sentence description of what needs to be done\n\n"
        "No bullet points, no numbering, no labels. Just the title on line 1 "
        "and the description on line 2. Plain language, no jargon."
    )

    try:
        result = await _call_claude(prompt)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not generate task from email: {exc}",
        ) from exc

    # Parse the two-line response.
    lines = [l.strip() for l in result.strip().splitlines() if l.strip()]
    title = lines[0] if lines else f"Follow up on: {subject}"
    if len(title) > 80:
        title = title[:77].rstrip() + "..."
    description = lines[1] if len(lines) > 1 else f"Email from {sender} about: {subject}"

    # Create the task via ostk.
    from services.ostk import ostk, OstkError
    from services.task_labeling import schedule_auto_labels, extract_task_id

    try:
        add_result = await ostk.add_task(title, "P1", description=description)
    except OstkError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not create task: {exc}",
        ) from exc

    task_id = extract_task_id(add_result)
    schedule_auto_labels(task_id, title, description)

    return {
        "ok": True,
        "task_id": task_id,
        "title": title,
        "description": description,
    }


@router.delete("/gmail/messages/{message_id}")
async def gmail_delete_message(message_id: str, permanent: bool = False):
    """Move a Gmail message to Trash, or permanently delete it.

    Default behavior matches Gmail's own UI: the message moves to the
    Trash label and Gmail will auto purge it after 30 days. Pass
    ``permanent=true`` only when the user has explicitly asked for a
    permanent delete (for example by saying "delete forever"). There is
    no undo for a permanent delete.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    try:
        if permanent:
            await gmail_service.permanent_delete_message(message_id)
        else:
            await gmail_service.trash_message(message_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not delete message: {exc}",
        ) from exc

    return {"ok": True, "permanent": permanent, "id": message_id}


@router.post("/gmail/messages/batch-mark-read")
async def gmail_batch_mark_read(body: BatchMarkReadRequest):
    """Mark a list of Gmail messages as read.

    Each id is processed independently. A failure on one id does not stop
    the others.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    if not body.ids:
        raise HTTPException(status_code=400, detail="No message ids provided.")

    succeeded: list[str] = []
    failed: list[dict] = []

    for message_id in body.ids:
        try:
            await gmail_service.mark_read(message_id)
            succeeded.append(message_id)
        except Exception as exc:
            failed.append({"id": message_id, "error": str(exc)})

    return {
        "ok": True,
        "succeeded": succeeded,
        "failed": failed,
        "count": len(succeeded),
    }


@router.post("/gmail/messages/batch-delete")
async def gmail_batch_delete(body: BatchDeleteRequest):
    """Move a list of Gmail messages to Trash, or permanently delete them.

    Each id is processed independently. A failure on one id does not stop
    the others. The response reports how many succeeded and the ids that
    failed so the UI can show a targeted error instead of a blanket one.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    if not body.ids:
        raise HTTPException(status_code=400, detail="No message ids provided.")

    succeeded: list[str] = []
    failed: list[dict] = []

    for message_id in body.ids:
        try:
            if body.permanent:
                await gmail_service.permanent_delete_message(message_id)
            else:
                await gmail_service.trash_message(message_id)
            succeeded.append(message_id)
        except Exception as exc:
            failed.append({"id": message_id, "error": str(exc)})

    return {
        "ok": True,
        "permanent": body.permanent,
        "succeeded": succeeded,
        "failed": failed,
        "count": len(succeeded),
    }
