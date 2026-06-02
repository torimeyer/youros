"""Channel intent parser and inbound iMessage poller (Wave 6, →1872).

Turns an inbound text message into a structured intent so the channel
router can dispatch it. Three intents are recognized:

  - spawn:  "spawn <agent> to <work>"  /  "spawn diagnose for Task 1654"
  - nudge:  "nudge <agent> <message>"
  - status: "status"  /  "what's the status?"

Anything else parses to {"intent": "unknown"}. The parser is pure: it has
no macOS or network dependency, so it can be unit-tested anywhere.

The InboundPoller reads new inbound iMessages from the existing iMessage
service (services.imessage) and feeds each new one to an async callback.
It tracks the last-seen message row id so it never re-processes a message.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# A task reference like "Task 1654", "task #1654", "for 1654".
_TASK_RE = re.compile(r"\btask\s*#?\s*(\d{1,6})\b", re.IGNORECASE)
# A bare "for <number>" reference (used when the word "task" is absent).
_FOR_NUM_RE = re.compile(r"\bfor\s+#?(\d{2,6})\b", re.IGNORECASE)
# A needle reference like "→1872" or "needle 1872".
_NEEDLE_ARROW_RE = re.compile(r"→\s*(\d{1,6})")
_NEEDLE_WORD_RE = re.compile(r"\bneedle\s*#?\s*(\d{1,6})\b", re.IGNORECASE)


def _empty_unknown(raw_text: str) -> dict:
    return {"intent": "unknown", "raw_text": raw_text}


def parse_intent(text: Optional[str]) -> dict:
    """Parse an inbound message into a structured intent dict.

    The result always contains at least ``intent`` and ``raw_text`` keys.
    ``intent`` is one of ``spawn``, ``nudge``, ``status``, ``unknown``.
    """
    if not text or not isinstance(text, str) or not text.strip():
        return _empty_unknown(text if isinstance(text, str) else "")

    raw_text = text
    stripped = text.strip()
    lowered = stripped.lower()
    words = stripped.split()
    first = words[0].lower() if words else ""

    # --- status -----------------------------------------------------------
    # Bare "status", or a question that contains the word "status".
    if first == "status" or lowered == "status?" or "status" in lowered and len(words) <= 6:
        if first == "status" or "status" in lowered:
            return {"intent": "status", "raw_text": raw_text}

    # --- spawn -------------------------------------------------------------
    if first == "spawn":
        return _parse_spawn(stripped, raw_text)

    # --- nudge -------------------------------------------------------------
    if first == "nudge":
        return _parse_nudge(words, raw_text)

    return _empty_unknown(raw_text)


def _parse_spawn(stripped: str, raw_text: str) -> dict:
    """Parse a spawn command.

    Forms handled:
      - "spawn <agent-ish> to <work description>"
      - "spawn diagnose for Task 1654"
      - "spawn an agent to build →1872"

    The portion after the verb is searched for a task id and a needle id.
    The free-text work description becomes ``prompt``.
    """
    # Everything after the leading "spawn".
    after = stripped[len("spawn"):].strip()

    task_id: Optional[str] = None
    needle_id: Optional[str] = None

    m = _TASK_RE.search(after)
    if m:
        task_id = m.group(1)
    else:
        m = _FOR_NUM_RE.search(after)
        if m:
            task_id = m.group(1)

    nm = _NEEDLE_ARROW_RE.search(after)
    if nm:
        needle_id = nm.group(1)
    else:
        nm = _NEEDLE_WORD_RE.search(after)
        if nm:
            needle_id = nm.group(1)

    # Work description: prefer the text after " to " (e.g. "...to fix the
    # login bug"). Otherwise use the whole tail as the prompt.
    prompt = after
    to_match = re.search(r"\bto\s+(.*)$", after, re.IGNORECASE)
    if to_match and to_match.group(1).strip():
        prompt = to_match.group(1).strip()

    return {
        "intent": "spawn",
        "raw_text": raw_text,
        "prompt": prompt,
        "task_id": task_id,
        "needle_id": needle_id,
    }


def _parse_nudge(words: list[str], raw_text: str) -> dict:
    """Parse "nudge <agent> <message...>".

    Requires both an agent name and at least one message word; otherwise
    the message is ambiguous and we fall back to ``unknown``.
    """
    # words[0] == "nudge" (any case)
    if len(words) < 3:
        return _empty_unknown(raw_text)
    agent = words[1]
    message = " ".join(words[2:]).strip()
    if not agent or not message:
        return _empty_unknown(raw_text)
    return {
        "intent": "nudge",
        "raw_text": raw_text,
        "agent": agent,
        "message": message,
    }


# ---------------------------------------------------------------------------
# Inbound poller
# ---------------------------------------------------------------------------


class InboundPoller:
    """Polls chat.db for new inbound messages and feeds them to a handler.

    The handler is an async callable ``handler(text, reply_to, chat_id)``.
    Only messages that are NOT from the user (``is_from_me`` is False) are
    delivered, so the user's own outbound texts never trigger routing.

    The poller is intentionally thin: it leans on the existing
    ``services.imessage`` for all chat.db access (read-only, thread-offloaded,
    circuit-broken). It tracks the highest message id it has seen so a
    message is processed at most once.
    """

    def __init__(
        self,
        handler: Callable[..., Awaitable[Any]],
        *,
        imessage_module: Any = None,
        poll_interval: float = 5.0,
        chat_limit: int = 20,
        message_limit: int = 10,
    ) -> None:
        self._handler = handler
        if imessage_module is None:
            from services import imessage as imessage_module  # lazy import
        self._im = imessage_module
        self._poll_interval = poll_interval
        self._chat_limit = chat_limit
        self._message_limit = message_limit
        # Highest message id processed so far. None means "establish a
        # baseline on the first poll without acting" so a backlog of old
        # messages never triggers a burst of spawns on boot.
        self._last_seen_id: Optional[int] = None
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()

    async def poll_once(self) -> int:
        """Run a single poll pass. Returns the number of messages handled.

        On the very first pass (``_last_seen_id`` is None) we only record a
        baseline and handle nothing, so historical messages are ignored.
        """
        try:
            conversations = await self._im.get_conversations(limit=self._chat_limit)
        except Exception as exc:
            logger.warning("InboundPoller: could not list conversations: %s", exc)
            return 0

        # Gather candidate inbound messages across recent conversations.
        candidates: list[tuple[int, str, str, int]] = []  # (msg_id, text, reply_to, chat_id)
        for conv in conversations:
            chat_id = conv.get("id")
            reply_to = conv.get("identifier") or ""
            if chat_id is None:
                continue
            try:
                messages = await self._im.get_messages(
                    chat_id, limit=self._message_limit
                )
            except Exception as exc:
                logger.debug("InboundPoller: get_messages(%s) failed: %s", chat_id, exc)
                continue
            for msg in messages:
                if msg.get("is_from_me"):
                    continue
                msg_id = msg.get("id")
                text = (msg.get("text") or "").strip()
                if msg_id is None or not text:
                    continue
                candidates.append((msg_id, text, reply_to, chat_id))

        if not candidates:
            return 0

        candidates.sort(key=lambda c: c[0])
        max_id = candidates[-1][0]

        # First pass: just set the baseline, do not act on the backlog.
        if self._last_seen_id is None:
            self._last_seen_id = max_id
            return 0

        handled = 0
        for msg_id, text, reply_to, chat_id in candidates:
            if msg_id <= self._last_seen_id:
                continue
            try:
                await self._handler(text=text, reply_to=reply_to, chat_id=chat_id)
                handled += 1
            except Exception as exc:
                logger.warning(
                    "InboundPoller: handler failed for message %s: %s", msg_id, exc
                )
        self._last_seen_id = max_id
        return handled

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.poll_once()
            except Exception as exc:  # never let the loop die
                logger.warning("InboundPoller loop error: %s", exc)
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=self._poll_interval
                )
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        """Start the background polling loop (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the background polling loop."""
        self._stopped.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=self._poll_interval + 1)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
