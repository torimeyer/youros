"""Channel routing for inbound iMessage commands (Wave 6, →1872).

Takes a parsed intent (see services.channel_intent_parser) and dispatches it
against a routing rules table:

    intent      action
    ------      ------
    spawn   ->  spawn an agent, reply "Started <name>"
    nudge   ->  nudge a running agent, reply with delivery status
    status  ->  query active agents, reply with a one-line summary
    unknown ->  reply with a short list of supported commands

On completion every branch replies to the sender via the EXISTING
POST /api/imessage/send (services.imessage.send_message). Side effects are
injected through ``RoutingDispatchers`` so the router can be unit-tested
without touching the agents API, chat.db, or AppleScript.

Spawning goes through the existing spawn API; this module does NOT modify
the spawn internals (api/routers/agents.py is owned by another stream).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["channel-routing"])


# ---------------------------------------------------------------------------
# Dispatchers (injectable side effects)
# ---------------------------------------------------------------------------


@dataclass
class RoutingDispatchers:
    """The four side-effecting callables the router depends on.

    Each is async. They are injected so tests can pass AsyncMocks and the
    production wiring can pass the real spawn/nudge/status/send functions.

      - spawn(intent: dict) -> dict        # starts an agent
      - nudge(intent: dict) -> dict        # messages a running agent
      - status(intent: dict) -> dict       # returns {"running": N, "agents": [...]}
      - send_reply(recipient: str, text: str) -> dict
    """

    spawn: Callable[[dict], Awaitable[dict]]
    nudge: Callable[[dict], Awaitable[dict]]
    status: Callable[[dict], Awaitable[dict]]
    send_reply: Callable[[str, str], Awaitable[dict]]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class ChannelRouter:
    """Dispatches a parsed intent and replies over the channel."""

    def __init__(self, dispatchers: RoutingDispatchers) -> None:
        self._d = dispatchers
        # The routing rules table: intent -> branch handler.
        self._rules: dict[str, Callable[[dict, str], Awaitable[dict]]] = {
            "spawn": self._handle_spawn,
            "nudge": self._handle_nudge,
            "status": self._handle_status,
            "unknown": self._handle_unknown,
        }

    async def handle_inbound_message(
        self, text: str, reply_to: str, chat_id: Optional[int] = None
    ) -> dict:
        """Single entry point for the inbound poller: parse, then route."""
        from services.channel_intent_parser import parse_intent

        intent = parse_intent(text)
        return await self.route(intent, reply_to=reply_to, chat_id=chat_id)

    async def route(
        self, intent: dict, reply_to: str, chat_id: Optional[int] = None
    ) -> dict:
        """Dispatch one parsed intent and reply to ``reply_to``.

        Returns a result dict describing what happened, including
        ``reply_sent`` (bool) and, for dispatched intents, ``dispatch_result``.
        """
        kind = intent.get("intent", "unknown")
        handler = self._rules.get(kind, self._handle_unknown)
        return await handler(intent, reply_to)

    # --- branch handlers --------------------------------------------------

    async def _handle_spawn(self, intent: dict, reply_to: str) -> dict:
        dispatch_result = await self._d.spawn(intent)
        name = (dispatch_result or {}).get("name", "an agent")
        reply = f"Started {name}."
        if intent.get("task_id"):
            reply = f"Started {name} on Task {intent['task_id']}."
        elif intent.get("needle_id"):
            reply = f"Started {name} on →{intent['needle_id']}."
        reply_sent = await self._reply(reply_to, reply)
        return {
            "intent": "spawn",
            "dispatch_result": dispatch_result,
            "reply_sent": reply_sent,
        }

    async def _handle_nudge(self, intent: dict, reply_to: str) -> dict:
        dispatch_result = await self._d.nudge(intent)
        delivery = (dispatch_result or {}).get("delivery", "sent")
        agent = intent.get("agent", "the agent")
        reply = f"Nudged {agent} ({delivery})."
        reply_sent = await self._reply(reply_to, reply)
        return {
            "intent": "nudge",
            "dispatch_result": dispatch_result,
            "reply_sent": reply_sent,
        }

    async def _handle_status(self, intent: dict, reply_to: str) -> dict:
        dispatch_result = await self._d.status(intent)
        running = (dispatch_result or {}).get("running", 0)
        agents = (dispatch_result or {}).get("agents", []) or []
        if running:
            names = ", ".join(str(a) for a in agents[:5])
            reply = f"{running} agent(s) working"
            if names:
                reply += f": {names}"
            reply += "."
        else:
            reply = "No agents are working right now."
        reply_sent = await self._reply(reply_to, reply)
        return {
            "intent": "status",
            "dispatch_result": dispatch_result,
            "reply_sent": reply_sent,
        }

    async def _handle_unknown(self, intent: dict, reply_to: str) -> dict:
        reply = (
            "I did not recognize that. Try:\n"
            '"spawn <name> to <task>", "nudge <agent> <message>", or "status".'
        )
        reply_sent = await self._reply(reply_to, reply)
        return {"intent": "unknown", "dispatch_result": None, "reply_sent": reply_sent}

    # --- reply helper -----------------------------------------------------

    async def _reply(self, recipient: str, text: str) -> bool:
        """Send a reply, swallowing send failures so dispatch is never lost."""
        if not recipient:
            return False
        try:
            await self._d.send_reply(recipient, text)
            return True
        except Exception as exc:
            logger.warning("ChannelRouter: reply to %s failed: %s", recipient, exc)
            return False


# ---------------------------------------------------------------------------
# Production dispatchers (wire to the existing APIs/services)
# ---------------------------------------------------------------------------


async def _default_spawn(intent: dict) -> dict:
    """Spawn an agent through the EXISTING spawn endpoint.

    Calls api.routers.agents.spawn_agent with an AgentSpawn body built from
    the parsed intent. We do NOT modify the spawn internals here.
    """
    from models.schemas import AgentSpawn
    from routers.agents import spawn_agent

    prompt = intent.get("prompt") or intent.get("raw_text") or ""
    # Derive a stable, readable agent name from the task/needle/prompt.
    if intent.get("task_id"):
        name = f"imsg-task-{intent['task_id']}"
    elif intent.get("needle_id"):
        name = f"imsg-needle-{intent['needle_id']}"
    else:
        slug = "-".join(prompt.lower().split()[:4]) or "agent"
        name = f"imsg-{slug}"[:48]

    body = AgentSpawn(
        name=name,
        prompt=prompt,
        description=f"Spawned from iMessage: {intent.get('raw_text', '')}"[:200],
        task_id=intent.get("task_id"),
        needle_id=intent.get("needle_id"),
        source="imessage",
    )
    result = await spawn_agent(body, request=None)
    out = result if isinstance(result, dict) else {"ok": True}
    out.setdefault("name", name)
    return out


async def _default_nudge(intent: dict) -> dict:
    """Nudge a running agent through the EXISTING nudge endpoint."""
    from models.schemas import AgentNudge
    from routers.agents import nudge_agent

    agent = intent.get("agent", "")
    message = intent.get("message", "")
    try:
        result = await nudge_agent(agent, AgentNudge(message=message))
        if isinstance(result, dict):
            return result
        return {"ok": True, "delivery": "sent"}
    except HTTPException as exc:
        return {"ok": False, "delivery": "unavailable", "error": exc.detail}


async def _default_status(intent: dict) -> dict:
    """Summarize running agents via the EXISTING agents list endpoint."""
    from routers.agents import list_agents

    try:
        data = await list_agents()
    except Exception as exc:
        logger.warning("status dispatcher: list_agents failed: %s", exc)
        return {"running": 0, "agents": []}

    agents = []
    if isinstance(data, dict):
        agents = data.get("agents", []) or data.get("running", []) or []
    elif isinstance(data, list):
        agents = data

    running_names: list[str] = []
    for a in agents:
        if not isinstance(a, dict):
            continue
        st = (a.get("status") or "").lower()
        if st in ("running", "active", "working", "in_progress", "spawned"):
            running_names.append(a.get("name") or a.get("id") or "agent")
    return {"running": len(running_names), "agents": running_names}


async def _default_send_reply(recipient: str, text: str) -> dict:
    """Reply via the EXISTING iMessage send service."""
    from services import imessage as imessage_service

    return await imessage_service.send_message(recipient, text)


def build_default_router() -> ChannelRouter:
    """Construct a ChannelRouter wired to the real production dispatchers."""
    return ChannelRouter(
        RoutingDispatchers(
            spawn=_default_spawn,
            nudge=_default_nudge,
            status=_default_status,
            send_reply=_default_send_reply,
        )
    )


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


class RouteRequest(BaseModel):
    text: str
    reply_to: str
    chat_id: Optional[int] = None


@router.post("/channel-routing/route")
async def channel_routing_route(body: RouteRequest):
    """Parse an inbound text and dispatch it.

    This is the HTTP surface the inbound poller (or a manual test) calls. The
    body carries the raw inbound text and the sender to reply to.
    """
    if not body.reply_to:
        raise HTTPException(status_code=400, detail="reply_to is required")
    router_instance = build_default_router()
    result = await router_instance.handle_inbound_message(
        text=body.text, reply_to=body.reply_to, chat_id=body.chat_id
    )
    return result


@router.post("/channel-routing/parse")
async def channel_routing_parse(body: RouteRequest):
    """Parse without dispatching. Useful for the Settings panel preview."""
    from services.channel_intent_parser import parse_intent

    return parse_intent(body.text)


@router.get("/channel-routing/rules")
async def channel_routing_rules():
    """Return the routing rules table so the UI can show what is supported."""
    return {
        "rules": [
            {
                "intent": "spawn",
                "example": "spawn diagnose for Task 1654",
                "action": "Starts an agent on the task and texts you back.",
            },
            {
                "intent": "nudge",
                "example": "nudge builder please hurry",
                "action": "Sends a message to a running agent.",
            },
            {
                "intent": "status",
                "example": "status",
                "action": "Texts back how many agents are working.",
            },
        ]
    }
