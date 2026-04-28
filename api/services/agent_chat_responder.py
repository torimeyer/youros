"""Conversational reply generator for inline agent chat (needle 857).

When a user sends a nudge to a running agent, instead of posting a canned
"On it, give me a sec." acknowledgment the system can generate a real
answer by making an Anthropic LLM call with the full conversation context.
This is "option 2" from the needle: pause-and-chat.

The real subagent is not paused. It keeps running. This module is a
server-side wrapper that reads the nudge/reply history for the agent and
asks Claude to respond as the agent would. The reply lands in the nudge
replies stream just like any other reply, tagged ``kind="conversational"``
so the UI can style it differently from the ack bot's canned receipts.

Cost contract:
- One non-streaming ``messages.create`` call per nudge.
- Max 400 input tokens for conversation history (recent turns only).
- Max 300 output tokens for the reply.
- Haiku model by default (fast, cheap).
- Falls back silently to no-op if the API key is unavailable or the
  call fails. The ack bot still covers the 2-second receipt promise.

Opt-in: only fires when the agent's metadata has
``"chat_mode": "conversational"``. Agents without this flag keep the
old ack-bot-only behavior. The spawn path sets this flag for every new
claude-code agent registered after this feature lands.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Token budget. These are intentionally small. The goal is a short,
# warm, direct answer to a specific question. Not an essay.
_MAX_HISTORY_TURNS = 8   # nudge+reply pairs to include as context
_MAX_REPLY_TOKENS = 300
_REPLY_MODEL = "claude-haiku-4-5"

# Timeout for the LLM call. If Anthropic takes longer than this the
# ack bot has already covered the 2-second receipt promise, so we
# just drop the conversational reply rather than leaving the user
# waiting indefinitely.
_LLM_TIMEOUT_SECONDS = 20.0

# Kind tag written to the reply record. The UI uses this to show
# the "answered by AI" badge.
CONVERSATIONAL_KIND = "conversational"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_conversation_messages(
    agent_name: str,
    user_message: str,
    nudge_history: list[dict],
    reply_history: list[dict],
) -> list[dict]:
    """Build the messages list for the Anthropic call.

    Interleaves the recent nudge/reply history as user/assistant turns,
    ending with the new user message. Caps at ``_MAX_HISTORY_TURNS``
    pairs so the prompt stays small. Sorts by timestamp ascending.
    """
    # Combine and sort by timestamp
    events: list[tuple[str, str, str]] = []  # (ts, role, text)
    for n in nudge_history:
        if not isinstance(n, dict):
            continue
        ts = str(n.get("timestamp", ""))
        msg = str(n.get("message", "")).strip()
        if msg:
            events.append((ts, "user", msg))
    for r in reply_history:
        if not isinstance(r, dict):
            continue
        # Skip ack-bot receipts from context. They are noise for the LLM.
        if r.get("kind") == "ack":
            continue
        ts = str(r.get("timestamp", ""))
        msg = str(r.get("message", "")).strip()
        if msg:
            events.append((ts, "assistant", msg))

    events.sort(key=lambda e: e[0])

    # Keep only the tail to respect the history limit. Each pair is one
    # user turn and one assistant turn, so cap at 2 * _MAX_HISTORY_TURNS
    # events. We want to preserve conversation alternation, so we pull
    # the last N pairs.
    if len(events) > _MAX_HISTORY_TURNS * 2:
        events = events[-(  _MAX_HISTORY_TURNS * 2):]

    messages: list[dict] = []
    for _, role, text in events:
        # Ensure alternation: if two consecutive same-role events appear
        # (e.g. two user nudges before a reply), merge them.
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n\n" + text
        else:
            messages.append({"role": role, "content": text})

    # The new user message is the turn we are replying to.
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n\n" + user_message
    else:
        messages.append({"role": "user", "content": user_message})

    return messages


def _build_system_prompt(agent_name: str, agent_task: str) -> str:
    """Return the system prompt for the conversational reply.

    Instructs the LLM to respond as the agent would: brief, direct,
    honest about what it is doing.
    """
    task_line = f"Your current task: {agent_task}" if agent_task else ""
    return (
        f"You are {agent_name}, a background AI agent. "
        f"{task_line}\n"
        "The user has just sent you a message through the inline chat. "
        "Reply directly and briefly. Two or three sentences at most. "
        "Be honest: if you are mid-task and do not know the answer yet, "
        "say so plainly. Do not invent status updates. "
        "No em-dashes. Plain language only."
    )


async def generate_reply(
    agent_name: str,
    user_message: str,
    nudge_history: list[dict],
    reply_history: list[dict],
    agent_task: str = "",
) -> Optional[str]:
    """Generate a conversational reply for ``user_message``.

    Returns the reply text, or ``None`` if the call failed or no API
    key is available. The caller decides what to do with ``None``.
    """
    try:
        from services.chat_providers import (
            _resolve_api_key,
            _get_anthropic_client,
            _anthropic_retry_call,
        )
    except ImportError as exc:
        logger.debug("agent_chat_responder: import failed: %s", exc)
        return None

    try:
        api_key = await _resolve_api_key("anthropic_api_key")
    except Exception as exc:
        logger.debug("agent_chat_responder: key lookup failed: %s", exc)
        return None
    if not api_key:
        return None

    messages = _build_conversation_messages(
        agent_name, user_message, nudge_history, reply_history
    )
    system = _build_system_prompt(agent_name, agent_task)

    try:
        client = _get_anthropic_client(api_key)
        response = await asyncio.wait_for(
            _anthropic_retry_call(
                lambda: client.messages.create(
                    model=_REPLY_MODEL,
                    max_tokens=_MAX_REPLY_TOKENS,
                    system=system,
                    messages=messages,
                ),
                op_name="agent_chat_responder.generate_reply",
            ),
            timeout=_LLM_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.debug(
            "agent_chat_responder: LLM call timed out for %s", agent_name
        )
        return None
    except Exception as exc:
        logger.debug(
            "agent_chat_responder: LLM call failed for %s: %s", agent_name, exc
        )
        return None

    try:
        # Extract text from the first content block.
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                return str(text).strip()
    except Exception as exc:
        logger.debug(
            "agent_chat_responder: response parse failed for %s: %s",
            agent_name, exc,
        )
    return None


async def reply_to_nudge(
    agent_name: str,
    user_message: str,
    nudge_timestamp: str,
    nudge_history: list[dict],
    reply_history: list[dict],
    agent_task: str = "",
) -> bool:
    """Generate a reply and post it via the agents router.

    Returns True when a reply was successfully generated and posted,
    False on any failure. Failures are non-fatal: the ack bot still
    covers the receipt promise, so a silent failure here is just a
    degraded-but-working experience.
    """
    text = await generate_reply(
        agent_name,
        user_message,
        nudge_history,
        reply_history,
        agent_task=agent_task,
    )
    if not text:
        return False

    try:
        from routers import agents as agents_router

        reply_data = await agents_router.ostk.append_nudge_reply(
            agent_name,
            text,
            in_reply_to=nudge_timestamp or None,
            kind=CONVERSATIONAL_KIND,
        )
        reply_data["kind"] = CONVERSATIONAL_KIND
        reply_data["source"] = "conversational_responder"
        agents_router.nudge_replies.setdefault(agent_name, []).append(reply_data)
        agents_router._wake_nudge_waiters(agent_name)
        return True
    except Exception as exc:
        logger.debug(
            "agent_chat_responder: reply post failed for %s: %s",
            agent_name, exc,
        )
        return False
