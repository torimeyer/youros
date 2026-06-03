"""Channel routing: map parsed intents to backend actions.

The live iMessage poller is guarded behind CHANNEL_ROUTING_LIVE_POLLER_ENABLED
which defaults to False. Nothing auto-runs unattended until you explicitly
set that env var to "1" or "true".

Exposed HTTP endpoints (for testing and manual triggering):
  POST /channel/route  - route a parsed intent and return the action payload
  POST /channel/inbound - parse + route a raw text message (for manual testing)
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from services.channel_intent_parser import parse_intent

router = APIRouter(tags=["channels"])

# Default OFF. Set CHANNEL_ROUTING_LIVE_POLLER_ENABLED=1 to activate.
LIVE_POLLER_ENABLED = os.getenv("CHANNEL_ROUTING_LIVE_POLLER_ENABLED", "").lower() in (
    "1", "true", "yes"
)


class RoutedAction(BaseModel):
    intent: dict
    endpoint: str
    payload: dict
    skipped: bool = False
    skip_reason: str = ""


def build_action(intent: dict[str, Any]) -> RoutedAction:
    """Map a parsed intent to the backend endpoint + payload it should call."""
    action = intent.get("action")

    if action == "spawn":
        return RoutedAction(
            intent=intent,
            endpoint="POST /api/agents/spawn",
            payload={
                "name": intent.get("agent_name", "channel-agent"),
                "task": intent.get("task", ""),
                "source": "channel",
            },
        )

    if action == "nudge":
        target = intent.get("target", "")
        return RoutedAction(
            intent=intent,
            endpoint=f"POST /api/agents/{target}/nudge",
            payload={"message": intent.get("message", "")},
        )

    if action == "status":
        return RoutedAction(
            intent=intent,
            endpoint="GET /api/agents",
            payload={},
        )

    return RoutedAction(
        intent=intent,
        endpoint="",
        payload={},
        skipped=True,
        skip_reason=f"unknown action: {intent.get('action')}",
    )


def build_reply_payload(recipient: str, text: str) -> dict:
    """Build the payload for POST /api/imessage/send."""
    return {"recipient": recipient, "text": text}


class InboundMessage(BaseModel):
    text: str
    sender: str = ""


@router.post("/channel/route")
async def route_intent(intent: dict) -> RoutedAction:
    """Route a pre-parsed intent dict to its backend action."""
    return build_action(intent)


@router.post("/channel/inbound")
async def handle_inbound(msg: InboundMessage) -> dict:
    """Parse + route a raw inbound text message. Does NOT send iMessage replies live."""
    intent = parse_intent(msg.text)
    action = build_action(intent)
    reply_payload = build_reply_payload(
        recipient=msg.sender,
        text=_summarize_action(action),
    )
    return {
        "intent": intent,
        "action": action.dict(),
        "reply_payload": reply_payload,
        "live_poller_enabled": LIVE_POLLER_ENABLED,
    }


def _summarize_action(action: RoutedAction) -> str:
    if action.skipped:
        return f"Sorry, I didn't understand that. ({action.skip_reason})"
    if action.intent.get("action") == "spawn":
        name = action.intent.get("agent_name", "")
        task = action.intent.get("task", "")
        return f"Spawning {name} to: {task}"
    if action.intent.get("action") == "nudge":
        return f"Nudging {action.intent.get('target', '')}..."
    if action.intent.get("action") == "status":
        return "Checking agent status..."
    return "Done."
