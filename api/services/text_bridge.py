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
    
    Trusts:
    1. Any identifier containing 'vmeyer' (case-insensitive) - for iMessage.
    2. A specific identifier configured in Settings -> Text yourOS.
    """
    if not sender_id:
        return False
    
    sender_lower = sender_id.lower()
    
    # 1. Broad identity match for iMessage
    if service == "iMessage" and "vmeyer" in sender_lower:
        return True
    
    # 2. Settings match (works for both iMessage and Telegram)
    config = settings_store.get("text_bridge", {})
    trusted = config.get("trusted_contacts", [])
    if sender_id in trusted:
        return True
    
    # 3. Telegram specific chat_id trust (if configured)
    telegram_config = settings_store.get("telegram", {})
    if service == "Telegram" and sender_id == str(telegram_config.get("chat_id")):
        return True
        
    return False


async def classify_and_dispatch(text: str, sender_id: str) -> str:
    """Use AI to classify the text and execute the corresponding action."""
    state = _load_state()
    
    # Handle pending confirmation
    pending = state.get("pending_confirmations", {}).get(sender_id)
    if pending:
        normalized = text.strip().upper()
        if normalized in ("YES", "Y", "OK", "PROCEED"):
            # Execute the held action
            tool_name = pending["tool_name"]
            tool_input = pending["tool_input"]
            
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
                "expires_at": time.time() + 600 # 10 min
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


class TextBridge:
    """Background service that polls for inbound messages."""
    
    def __init__(self) -> None:
        self._imessage_poller: Optional[Any] = None
        self._telegram_poller: Optional[Any] = None
        self._state = _load_state()

    def start(self) -> None:
        config = settings_store.get("text_bridge", {})
        enabled = config.get("enabled") or settings_store.get("inbound_imessage_routing_enabled", False)
        
        if not enabled:
            logger.debug("TextBridge: disabled in settings")
            return

        # Determine the user's self-handle so the poller can accept note-to-self texts.
        trusted = config.get("trusted_contacts", [])
        self_handle = trusted[0] if trusted else ""

        # Start iMessage Poller
        from services.channel_intent_parser import InboundPoller
        self._imessage_poller = InboundPoller(self.handle_inbound_message, self_handle=self_handle)
        if self._state.get("cursor"):
            self._imessage_poller._cursor = self._state["cursor"]
        self._imessage_poller.start()
        logger.info("TextBridge: iMessage poller started at cursor %s (self_handle=%s)",
                    self._imessage_poller._cursor, self_handle or "none")

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

        # For is_from_me messages in the self-chat, the DB sets sender="me" which
        # won't match trusted_contacts. Use the configured self_handle instead.
        is_self_text = (
            msg.get("is_from_me")
            and self._imessage_poller is not None
            and bool(self._imessage_poller._self_handle)
            and msg.get("chat_identifier") == self._imessage_poller._self_handle
        )
        effective_sender = self._imessage_poller._self_handle if is_self_text else sender

        if not is_trusted_sender(effective_sender, service):
            logger.debug("TextBridge: ignoring untrusted sender from %s: %s", service, effective_sender)
            return

        logger.info("TextBridge: processing %s message from %s: %s", service, sender, text[:50])
        
        # Mirror user message to chat history
        append_chat_interaction("user", f"[{service}] {text}")
        
        reply_text = await classify_and_dispatch(text, sender)
        
        # Mirror reply to chat history
        append_chat_interaction("assistant", reply_text)
        
        # Advance state cursor
        if service == "iMessage" and msg.get("date"):
            self._state["cursor"] = msg["date"]
            _save_state(self._state)
        elif service == "Telegram":
            # Offset is handled by the poller itself, but we could persist it here if needed
            # For now, the poller manages it in memory.
            pass

        # Send reply back via the originating service
        chat_id = msg.get("chat_id")
        if not chat_id:
            return

        if service == "iMessage":
            try:
                from services.imessage import reply_to_chat_sync
                await asyncio.to_thread(reply_to_chat_sync, chat_id, reply_text)
            except Exception as exc:
                logger.error("TextBridge: could not send iMessage reply: %s", exc)
            # Loop guard: record this reply so the self-chat poller skips it.
            if is_self_text and self._imessage_poller is not None:
                self._imessage_poller.mark_sent(reply_text)
        elif service == "Telegram" and self._telegram_poller:
            await self._telegram_poller.send_message(chat_id, reply_text)



text_bridge = TextBridge()
