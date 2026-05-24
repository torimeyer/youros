"""Gmail reply endpoints.

POST /api/gmail/reply           Send a plain-text reply in a thread.
POST /api/gmail/draft_reply     Ask Claude to draft a reply.
GET  /api/gmail/send_capability Report whether the stored token can send.

Kept in a dedicated router so it does not collide with edits the perf
agent is making in api/routers/gmail.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import gmail_reply as gmail_reply_service
from services.google_auth import CREDENTIALS_PATH, is_authenticated

router = APIRouter(tags=["gmail-reply"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SendReplyBody(BaseModel):
    thread_id: str
    in_reply_to_message_id: str
    body: str
    reply_all: bool = False


class DraftReplyBody(BaseModel):
    thread_id: str
    in_reply_to_message_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/gmail/reply")
async def gmail_reply(body: SendReplyBody) -> dict:
    """Send a plain-text reply to the given Gmail thread."""
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    result = await gmail_reply_service.send_reply(
        thread_id=body.thread_id,
        in_reply_to_message_id=body.in_reply_to_message_id,
        body=body.body,
        reply_all=body.reply_all,
    )
    return result


@router.post("/gmail/draft_reply")
async def gmail_draft_reply(body: DraftReplyBody) -> dict:
    """Draft a plain-text reply with Claude."""
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Gmail.")

    result = await gmail_reply_service.draft_reply(
        thread_id=body.thread_id,
        in_reply_to_message_id=body.in_reply_to_message_id,
    )
    return result


@router.get("/gmail/send_capability")
async def gmail_send_capability() -> dict:
    """Return whether the connected Google token can send Gmail.

    Shape:
        {
          "has_send_scope": bool,
          "reauth_url": <str or null>
        }

    When ``has_send_scope`` is False, the frontend should show a
    reconnect Gmail CTA and open ``reauth_url`` directly. ``reauth_url``
    is the real ``https://accounts.google.com/o/oauth2/...`` consent URL
    (NOT an API endpoint path), so wiring it into an anchor ``href``
    lands the browser on Google's sign-in screen. We reuse the same
    state persistence and scope list the existing drive/gmail auth
    start flow uses, so the upgraded scope list including gmail.send
    gets requested on the same consent screen.
    """
    has_scope = gmail_reply_service.has_send_scope()

    reauth_url: str | None = None
    if not has_scope and CREDENTIALS_PATH.exists():
        # Build the real Google OAuth consent URL. Delegate to the
        # existing drive_auth_url_for_gmail handler so there is a single
        # source of truth for the state map and redirect target.
        from routers.drive import drive_auth_url_for_gmail

        try:
            resp = await drive_auth_url_for_gmail()
            reauth_url = resp.get("url") if isinstance(resp, dict) else None
        except Exception:
            # If the consent URL cannot be built (for example, a broken
            # credentials file), leave reauth_url as None so the frontend
            # shows the plain fallback copy instead of a broken link.
            reauth_url = None

    return {
        "has_send_scope": has_scope,
        "reauth_url": reauth_url,
    }
