"""Smart email triage endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.google_auth import is_authenticated

router = APIRouter(tags=["gmail"])


class ApplyActionRequest(BaseModel):
    action: str  # "create_task", "draft_reply", "archive", "skip"
    message_id: str
    thread_id: Optional[str] = None


@router.post("/gmail/triage")
async def gmail_triage():
    """Run smart email triage. Classifies unread messages into categories."""
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    from services.gmail_triage import triage_inbox

    result = await triage_inbox()
    if not result.get("ok"):
        raise HTTPException(
            status_code=500,
            detail=result.get("error", "Triage failed. Try again in a moment."),
        )
    return result


@router.post("/gmail/triage/apply")
async def gmail_triage_apply(body: ApplyActionRequest):
    """Apply a triage action to a specific message.

    Supported actions: create_task, draft_reply, archive, skip.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    action = body.action
    message_id = body.message_id

    if action == "create_task":
        from services.gmail_triage import _load_last_triage, create_task_from_email

        cached = _load_last_triage()
        if not cached:
            raise HTTPException(
                status_code=404,
                detail="No triage results found. Run triage first.",
            )
        msg = next(
            (m for m in cached.get("messages", []) if m.get("id") == message_id),
            None,
        )
        if not msg:
            raise HTTPException(
                status_code=404,
                detail="Message not found in triage results.",
            )
        result = await create_task_from_email(msg)
        if not result.get("ok"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Could not create task."),
            )
        return result

    elif action == "draft_reply":
        from services.gmail_reply import draft_reply

        if not body.thread_id:
            raise HTTPException(
                status_code=400,
                detail="thread_id is required for draft_reply.",
            )
        result = await draft_reply(
            thread_id=body.thread_id,
            in_reply_to_message_id=message_id,
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Could not draft reply."),
            )
        return result

    elif action == "archive":
        from services.gmail_triage import archive_message

        result = await archive_message(message_id)
        if not result.get("ok"):
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Could not archive message."),
            )
        return result

    elif action == "skip":
        return {"ok": True, "skipped": True}

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action: {action}. Valid actions: create_task, draft_reply, archive, skip.",
        )


@router.get("/gmail/triage/last")
async def gmail_triage_last():
    """Return the cached last triage result without re-running triage."""
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    from services.gmail_triage import _load_last_triage

    result = _load_last_triage()
    if not result:
        return {"ok": True, "messages": [], "summary": {}, "triaged_at": None}
    return result
