"""Tool registry for the yourOS MCP server.

Each entry in TOOLS maps a tool name to its MCP inputSchema and a handler
coroutine that returns a plain Python value (will be JSON-serialised by the
server loop).
"""
from __future__ import annotations

import asyncio
from typing import Any

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "task.list",
        "description": "List yourOS tasks (needles). Optionally filter by status or priority.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status (open, closed, in_progress).",
                },
                "priority": {
                    "type": "string",
                    "description": "Filter by priority (P0, P1, P2, P3).",
                },
            },
        },
    },
    {
        "name": "task.add",
        "description": "Create a new yourOS task (needle).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title."},
                "priority": {
                    "type": "string",
                    "description": "Priority (P0-P3). Defaults to P1.",
                    "default": "P1",
                },
                "description": {"type": "string", "description": "Longer description."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "spec.list",
        "description": "List yourOS spec and draft documents.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "agent.spawn",
        "description": "Spawn a yourOS agent via the backend API.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Unique agent name."},
                "prompt": {"type": "string", "description": "Agent task prompt."},
                "model": {
                    "type": "string",
                    "description": "Model override (sonnet, opus, haiku).",
                },
                "description": {"type": "string", "description": "One-line description."},
            },
            "required": ["name", "prompt"],
        },
    },
    {
        "name": "memory.search",
        "description": "Search yourOS tasks by concept using semantic near-search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Concept or keyword to search for."},
            },
            "required": ["query"],
        },
    },
]

TOOL_MAP: dict[str, dict] = {t["name"]: t for t in TOOLS}

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_task_list(args: dict) -> Any:
    from services.ostk import ostk
    tasks = await ostk.list_tasks(
        status=args.get("status"),
        priority=args.get("priority"),
    )
    return {"tasks": tasks}


async def handle_task_add(args: dict) -> Any:
    from services.ostk import ostk
    result = await ostk.add_task(
        args["title"],
        priority=args.get("priority", "P1"),
        description=args.get("description", ""),
    )
    return {"result": result}


async def handle_spec_list(_args: dict) -> Any:
    from services.ostk import ostk
    docs = await ostk.list_docs()
    return {"docs": docs}


async def handle_agent_spawn(args: dict) -> Any:
    import httpx
    payload = {
        "name": args["name"],
        "prompt": args["prompt"],
        "model": args.get("model", "sonnet"),
        "description": args.get("description", args["prompt"][:80]),
    }
    async with httpx.AsyncClient(verify=False, timeout=10) as client:
        resp = await client.post("https://127.0.0.1:8000/api/agents", json=payload)
        resp.raise_for_status()
        return resp.json()


async def handle_memory_search(args: dict) -> Any:
    from services.ostk import ostk
    result = await ostk.search_near(args["query"])
    return result


HANDLERS = {
    "task.list": handle_task_list,
    "task.add": handle_task_add,
    "spec.list": handle_spec_list,
    "agent.spawn": handle_agent_spawn,
    "memory.search": handle_memory_search,
}


async def dispatch(tool_name: str, args: dict) -> Any:
    handler = HANDLERS.get(tool_name)
    if handler is None:
        raise ValueError(f"Unknown tool: {tool_name}")
    return await handler(args)
