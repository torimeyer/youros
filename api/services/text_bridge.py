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

from services.ai_backend import get_ai_client
from services.atomic_io import atomic_write_json
from services.settings_store import settings_store
from services.chat_interactions import append_chat_interaction

logger = logging.getLogger(__name__)

MYOS_HOME = Path.home() / ".myos"
STATE_PATH = MYOS_HOME / "text_bridge_state.json"


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"cursor": 0.0, "pending_confirmations": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"cursor": 0.0, "pending_confirmations": {}}


def _save_state(state: dict) -> None:
    atomic_write_json(STATE_PATH, state)


def is_trusted_sender(sender_id: str) -> bool:
    """Return True if the sender is authorized to command myOS.
    
    Trusts:
    1. Any identifier containing 'vmeyer' (case-insensitive) - e.g. handle, email.
    2. A specific identifier configured in Settings -> Text yourOS.
    """
    if not sender_id:
        return False
    
    sender_lower = sender_id.lower()
    
    # 1. Identity match (vmeyer) - broad trust for user's own identity
    if "vmeyer" in sender_lower:
        return True
    
    # 2. Settings match
    config = settings_store.get("text_bridge", {})
    trusted = config.get("trusted_contacts", [])
    if sender_id in trusted:
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

    from services.tool_executor import TOOL_DEFINITIONS
    allowed_tools = ["create_task", "run_command", "spawn_agent"]
    tools = [t for t in TOOL_DEFINITIONS if t["name"] in allowed_tools]

    system_prompt = (
        "You are the Text yourOS bridge. You receive messages from the user's phone "
        "and must decide whether to create a task, run a command, spawn a background "
        "agent, or just have a conversation.\n\n"
        "Rules:\n"
        "1. If the user asks to do something immediate or technical, use run_command.\n"
        "2. If it's a long-running project or research, use spawn_agent.\n"
        "3. If it's a to-do item for later, use create_task.\n"
        "4. If they are just asking a question or chatting, do NOT use any tools. "
        "Just reply as an assistant.\n\n"
        "The user is Tori. Your identity is postk (personal ostk)."
    )

    try:
        resp = await client.messages.create(
            model="claude-3-5-sonnet-latest",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
            tools=tools,
            tool_choice="auto",
        )
        
        # Check for tool use
        tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
        if tool_use:
            # Task creation is safe to do immediately
            if tool_use.name == "create_task":
                from services.tool_executor import execute_tool
                await execute_tool(tool_use.name, tool_use.input)
                return f"Task created: {tool_use.input.get('title')}"
            
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
                
            return f"Requested to {desc}. Reply YES to proceed or NO to cancel."

        # No tool use -> Chat path
        reply = resp.content[0].text
        return reply

    except Exception as exc:
        logger.error("TextBridge: classification failed: %s", exc)
        return f"Error processing your request: {exc}"


class TextBridge:
    """Background service that polls for inbound messages."""
    
    def __init__(self) -> None:
        self._poller: Optional[Any] = None
        self._state = _load_state()

    def start(self) -> None:
        config = settings_store.get("text_bridge", {})
        enabled = config.get("enabled") or settings_store.get("inbound_imessage_routing_enabled", False)
        
        if not enabled:
            logger.debug("TextBridge: disabled in settings")
            return

        from services.channel_intent_parser import InboundPoller
        self._poller = InboundPoller(self.handle_inbound_message)
        if self._state.get("cursor"):
            self._poller._cursor = self._state["cursor"]
            
        self._poller.start()
        logger.info("TextBridge: background poller started at cursor %s", self._poller._cursor)

    async def handle_inbound_message(self, msg: dict) -> None:
        """Handler for the InboundPoller."""
        sender = msg.get("sender", "")
        text = msg.get("text", "")
        
        if not is_trusted_sender(sender):
            logger.debug("TextBridge: ignoring untrusted sender: %s", sender)
            return

        logger.info("TextBridge: processing message from %s: %s", sender, text[:50])
        
        # Mirror user message to chat history
        append_chat_interaction("user", text)
        
        reply_text = await classify_and_dispatch(text, sender)
        
        # Mirror reply to chat history
        append_chat_interaction("assistant", reply_text)
        
        # Advance state cursor
        if msg.get("date"):
            self._state["cursor"] = msg["date"]
            _save_state(self._state)

        # Send reply back via the originating service
        chat_id = msg.get("chat_id")
        if chat_id and msg.get("service") == "iMessage":
            try:
                from services.imessage import reply_to_chat
                await asyncio.to_thread(reply_to_chat, chat_id, reply_text)
            except Exception as exc:
                logger.error("TextBridge: could not send iMessage reply: %s", exc)


text_bridge = TextBridge()
