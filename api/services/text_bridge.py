"""Text yourOS bridge: classify and dispatch inbound channel messages.

Supports iMessage and Telegram (outbound polling). This service runs a
background loop that polls for new messages from trusted senders,
classifies them using the auth-aware AI client, and dispatches them to
tasks, commands, or chat history.

Trusted-sender gate: trusts any sender with 'vmeyer' in their identifier,
or a specific contact configured in Settings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from services.ai_backend import get_ai_client, resolve_ai_backend
from services.atomic_io import atomic_write_json
from services.settings_store import settings_store
from services.chat_interactions import append_chat_interaction
from services.youros_paths import youros_home

logger = logging.getLogger(__name__)

YOUROS_HOME = youros_home()
STATE_PATH = YOUROS_HOME / "text_bridge_state.json"


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"cursor": 0.0, "pending_confirmations": {}, "telegram_offset": 0}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"cursor": 0.0, "pending_confirmations": {}, "telegram_offset": 0}


def _save_state(state: dict) -> None:
    atomic_write_json(STATE_PATH, state)


def is_trusted_sender(sender_id: str, service: str = "iMessage") -> bool:
    """Return True if the sender is authorized to command yourOS.
    
    Trusts only identifiers configured in Settings -> Text yourOS (trusted_contacts).
    """
    if not sender_id:
        return False
    
    # Settings match (works for both iMessage and Telegram)
    config = settings_store.get("text_bridge", {})
    trusted = config.get("trusted_contacts", [])
    if sender_id in trusted:
        return True
    
    # 3. Telegram specific chat_id trust (if configured)
    telegram_config = settings_store.get("telegram", {})
    if service == "Telegram" and sender_id == str(telegram_config.get("chat_id")):
        return True
        
    return False


async def classify_and_dispatch(text: str, sender_id: str, chat_id: Optional[int] = None) -> str:
    """Use AI to classify the text and execute the corresponding action."""
    state = _load_state()
    
    # Handle pending confirmation
    pending = state.get("pending_confirmations", {}).get(sender_id)
    if pending:
        normalized = text.strip().upper()
        if normalized in ("YES", "Y", "OK", "PROCEED"):
            # Execute the held action
            tool_name = pending["tool_name"]
            tool_input = dict(pending["tool_input"])
            
            # For spawn_agent: inject notify so the agent texts back on completion.
            _pending_chat_id = pending.get("chat_id")
            if tool_name == "spawn_agent" and _pending_chat_id is not None:
                tool_input["notify"] = {"kind": "imessage", "chat_id": _pending_chat_id}
            
            # Clear pending first
            del state["pending_confirmations"][sender_id]
            _save_state(state)
            
            try:
                from services.tool_executor import execute_tool
                await execute_tool(tool_name, tool_input)
                return f"Confirmed. Executing {tool_name}."
            except Exception as exc:
                return f"Confirmation failed: {exc}"
        elif normalized in ("NO", "N", "CANCEL", "STOP"):
            del state["pending_confirmations"][sender_id]
            _save_state(state)
            return "Cancelled. No action taken."
        else:
            return "Still waiting for confirmation. Reply YES to proceed or NO to cancel."

    # Normal classification
    client = await get_ai_client()
    if client is None:
        return "Sorry, no AI backend available to process this request."

    backend = await resolve_ai_backend()
    model = settings_store.get("model", "claude-sonnet-4-5")

    from services.tool_executor import TOOL_DEFINITIONS
    allowed_tools = ["create_task", "run_command", "spawn_agent", "list_tasks"]
    tools = [t for t in TOOL_DEFINITIONS if t["name"] in allowed_tools]

    system_prompt = (
        "You are the Text yourOS bridge. You receive messages from the user's phone "
        "and must decide whether to create a task, list tasks, run a command, spawn a background "
        "agent, or just have a conversation.\n\n"
        "Rules:\n"
        "1. If the user asks to see their tasks, what they have to do, or their backlog, use list_tasks.\n"
        "2. If the user asks to do something immediate or technical (e.g. 'run', 'execute', 'summarize my day'), use run_command.\n"
        "3. If it's a long-running project or research (e.g. 'build a feature', 'investigate a bug'), use spawn_agent.\n"
        "4. If it's a to-do item for later (e.g. 'remind me', 'add a task', 'don't forget'), use create_task.\n"
        "5. If they are just asking a question or chatting, do NOT use any tools. Just reply helpfully.\n\n"
        "The user is Tori. Your identity is postk (personal ostk)."
    )

    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
            tools=tools,
            tool_choice={"type": "auto"},
        )
        
        # Extract preamble text if any
        preamble = "".join(b.text for b in resp.content if b.type == "text").strip()
        
        # Check for tool use
        tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
        if tool_use:
            # Safe/Read-only tools are executed immediately
            if tool_use.name in ("create_task", "list_tasks"):
                from services.tool_executor import execute_tool
                result = await execute_tool(tool_use.name, tool_use.input)
                
                if tool_use.name == "create_task":
                    return f"Task created: {tool_use.input.get('title')}"
                else:
                    # For list_tasks, the result is the list
                    return f"{preamble}\n\n{result}" if preamble else result
            
            # Others require confirmation
            state["pending_confirmations"][sender_id] = {
                "tool_name": tool_use.name,
                "tool_input": tool_use.input,
                "expires_at": time.time() + 600,  # 10 min
                "chat_id": chat_id,  # persisted so YES handler can inject notify
            }
            _save_state(state)
            
            if tool_use.name == "run_command":
                desc = f"run command: {tool_use.input.get('command')}"
            else:
                desc = f"spawn agent '{tool_use.input.get('name')}'"
                
            reply = f"Requested to {desc}. Reply YES to proceed or NO to cancel."
            return f"{preamble}\n\n{reply}" if preamble else reply

        # No tool use -> Chat path
        return preamble if preamble else "I'm not sure how to help with that."

    except Exception as exc:
        logger.error("TextBridge: classification failed: %s", exc)
        return f"Error processing your request: {exc}"


SELF_REPLY_WINDOW_S = 60
SELF_REPLY_MAX = 5


class TextBridge:
    """Background service that polls for inbound messages."""
    
    def __init__(self) -> None:
        self._imessage_poller: Optional[Any] = None
        self._telegram_poller: Optional[Any] = None
        self._state = _load_state()
        self._self_reply_times: list[float] = []
        self._breaker_latched = False

    def start(self) -> None:
        config = settings_store.get("text_bridge", {})
        enabled = config.get("enabled") or settings_store.get("inbound_imessage_routing_enabled", False)
        
        if not enabled:
            logger.debug("TextBridge: disabled in settings")
            return

        # Idempotent: never stack a second poller on top of a live one. Two
        # pollers keep independent sent-reply guards and would reply to each
        # other's messages.
        if self._imessage_poller is not None or self._telegram_poller is not None:
            self.stop()
        self._breaker_latched = False

        # The self-chat can be keyed to ANY trusted identifier (phone or
        # email), so the poller gets the whole list (→2505 — keying to
        # trusted[0] left every loop guard attached to the wrong conversation).
        trusted = config.get("trusted_contacts", [])

        # Start iMessage Poller
        from services.channel_intent_parser import InboundPoller
        self._imessage_poller = InboundPoller(self.handle_inbound_message, self_handles=trusted)
        if self._state.get("cursor"):
            self._imessage_poller._cursor = self._state["cursor"]
        self._imessage_poller.start()
        logger.info("TextBridge: iMessage poller started at cursor %s (self_handles=%s)",
                    self._imessage_poller._cursor, trusted or "none")

        # Start Telegram Poller if configured
        telegram_config = settings_store.get("telegram", {})
        if telegram_config.get("token"):
            from services.telegram_channel import TelegramPoller
            self._telegram_poller = TelegramPoller(
                token=telegram_config["token"],
                handler=self.handle_inbound_message
            )
            if self._state.get("telegram_offset"):
                self._telegram_poller._offset = self._state["telegram_offset"]
            self._telegram_poller.start()
            logger.info("TextBridge: Telegram poller started")

    async def handle_inbound_message(self, msg: dict) -> None:
        """Handler for the InboundPollers."""
        service = msg.get("service", "iMessage")
        sender = msg.get("sender", "")
        text = msg.get("text", "")

        # in_self_chat: any message in the self-conversation — includes the is_from_me=False
        # received echo that iMessage creates alongside every is_from_me=True sent row.
        # Matched against every trusted contact, not just the first (→2505).
        poller = self._imessage_poller
        in_self_chat = poller is not None and poller.is_self_chat(msg.get("chat_identifier"))
        # is_self_text: the DB sends sender="me" for is_from_me rows in the self-chat.
        # Use the chat identifier (a trusted contact) so the trust check passes.
        is_self_text = bool(msg.get("is_from_me")) and in_self_chat
        effective_sender = msg.get("chat_identifier") if is_self_text else sender

        if not is_trusted_sender(effective_sender, service):
            logger.debug("TextBridge: ignoring untrusted sender from %s: %s", service, effective_sender)
            return

        chat_id = msg.get("chat_id")

        logger.info("TextBridge: processing %s message from %s: %s", service, sender, text[:50])
        
        # Mirror user message to chat history
        append_chat_interaction("user", f"[{service}] {text}")
        
        reply_text = await classify_and_dispatch(text, sender, chat_id=chat_id)
        
        # Mirror reply to chat history
        append_chat_interaction("assistant", reply_text)
        
        # Advance state cursor
        if service == "iMessage" and msg.get("date"):
            self._state["cursor"] = msg["date"]
            _save_state(self._state)

        # Send reply back via the originating service
        if not chat_id:
            return

        if service == "iMessage":
            # Latched breaker: the bridge disabled itself after a runaway; never
            # send again until it is explicitly re-enabled (start() resets this).
            if self._breaker_latched:
                logger.warning("TextBridge: breaker latched — dropping reply")
                return
            # Circuit breaker: more than SELF_REPLY_MAX replies to the self-chat within
            # SELF_REPLY_WINDOW_S seconds means the loop guard missed something. A sliding
            # window alone lets a poll-cadence loop run at ~5/min forever, so tripping
            # LATCHES: persist enabled=false and stop the poller (→2505).
            if in_self_chat:
                now = time.time()
                self._self_reply_times = [
                    t for t in self._self_reply_times if t > now - SELF_REPLY_WINDOW_S
                ]
                if len(self._self_reply_times) >= SELF_REPLY_MAX:
                    logger.warning(
                        "TextBridge: circuit breaker tripped — %d self-chat replies in %ds; "
                        "latching off and disabling the bridge",
                        len(self._self_reply_times),
                        SELF_REPLY_WINDOW_S,
                    )
                    self._breaker_latched = True
                    breaker_config = settings_store.get("text_bridge", {})
                    breaker_config["enabled"] = False
                    settings_store.update({"text_bridge": breaker_config})
                    self.stop()
                    return
                self._self_reply_times.append(now)
                # Pre-register BEFORE sending so the guard is armed before the message
                # lands in chat.db and covers both the is_from_me=True and the received echo.
                if poller is not None:
                    poller.mark_sent(reply_text)

            try:
                from services.imessage import reply_to_chat_sync
                await asyncio.to_thread(reply_to_chat_sync, chat_id, reply_text)
            except Exception as exc:
                logger.error("TextBridge: could not send iMessage reply: %s", exc)
        elif service == "Telegram" and self._telegram_poller:
            await self._telegram_poller.send_message(chat_id, reply_text)

    def stop(self) -> None:
        """Stop all live pollers immediately (e.g. when enabled toggled off in Settings)."""
        if self._imessage_poller is not None:
            self._imessage_poller.stop()
            self._imessage_poller = None
        if self._telegram_poller is not None:
            self._telegram_poller = None



text_bridge = TextBridge()
