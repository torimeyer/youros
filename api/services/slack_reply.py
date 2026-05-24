"""Slack reply drafting and sending.

draft_reply fetches thread context and asks Gemini Enterprise to draft a
reply. Gemini is required. No fallback to Anthropic.

send_reply posts text as a thread reply via the existing Slack bot token.
"""

from __future__ import annotations

from services import slack as _slack
from services import gemini_drafter

_SYSTEM_PROMPT = (
    "You're drafting a Slack reply for the user. "
    "Match Slack tone. "
    "Keep under 4 sentences unless the question demands more. "
    "No preamble."
)

_MAX_THREAD_CHARS = 4000


async def _fetch_thread_text(channel_id: str, ts: str) -> str:
    """Return the thread as a plain text block for the prompt."""
    try:
        data = await _slack._slack_get(
            "conversations.replies",
            {"channel": channel_id, "ts": ts, "limit": "20"},
        )
        messages = data.get("messages", [])
    except Exception:
        messages_raw = await _slack.fetch_messages(channel_id, limit=10)
        messages = [m for m in messages_raw if m.get("ts") == ts]

    lines = []
    for msg in messages:
        user = msg.get("user") or msg.get("username") or "unknown"
        text = (msg.get("text") or "").strip()
        if text:
            lines.append(f"{user}: {text}")

    full = "\n".join(lines)
    if len(full) > _MAX_THREAD_CHARS:
        full = full[:_MAX_THREAD_CHARS] + "\n...(truncated)"
    return full


async def draft_reply(channel_id: str, ts: str) -> str:
    """Fetch the Slack thread and return a Gemini-drafted reply string.

    Raises RuntimeError("Gemini Enterprise not connected") if Gemini creds
    are not available.
    """
    thread_text = await _fetch_thread_text(channel_id, ts)
    user_prompt = (
        "Draft a reply to the Slack thread below.\n\n"
        f"{thread_text}\n\n"
        "Return only the reply text. No preamble, no sign-off unless it feels natural."
    )
    return await gemini_drafter.draft(user_prompt, system=_SYSTEM_PROMPT)


async def send_reply(channel_id: str, ts: str, text: str) -> dict:
    """Post text as a threaded Slack reply.

    Returns {"ok": True, "ts": ..., "permalink": ...}.
    """
    data = await _slack._slack_post(
        "chat.postMessage",
        {"channel": channel_id, "text": text, "thread_ts": ts},
    )
    return {
        "ok": True,
        "ts": data.get("ts", ""),
        "permalink": "",
    }
