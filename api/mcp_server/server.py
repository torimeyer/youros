"""MCP stdio server — JSON-RPC 2.0 over stdin/stdout per the MCP spec.

Supported MCP methods:
  initialize          → server capabilities + tool list
  tools/list          → list registered tools
  tools/call          → dispatch a tool call
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from .tools import TOOLS, dispatch

MCP_VERSION = "2024-11-05"
SERVER_INFO = {"name": "myos", "version": "1.0.0"}


def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": MCP_VERSION,
            "serverInfo": SERVER_INFO,
            "capabilities": {"tools": {}},
        })

    if method == "notifications/initialized":
        return None  # notification — no response

    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if not tool_name:
            return _err(req_id, -32602, "Missing tool name")
        try:
            result = await dispatch(tool_name, arguments)
            return _ok(req_id, {
                "content": [{"type": "text", "text": json.dumps(result)}],
                "isError": False,
            })
        except ValueError as exc:
            return _err(req_id, -32601, str(exc))
        except Exception as exc:  # noqa: BLE001
            return _ok(req_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })

    return _err(req_id, -32601, f"Method not found: {method}")


async def _run_loop(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
        try:
            line = await reader.readline()
        except asyncio.IncompleteReadError:
            break
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = _err(None, -32700, "Parse error")
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            continue

        response = await _handle(msg)
        if response is not None:
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()


def start_stdio_server() -> None:
    """Entry point: wire stdin/stdout as asyncio streams and run the loop."""

    async def _main() -> None:
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        read_proto = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: read_proto, sys.stdin)

        write_transport, write_proto = await loop.connect_write_pipe(
            asyncio.BaseProtocol, sys.stdout
        )
        writer = asyncio.StreamWriter(write_transport, write_proto, reader, loop)

        await _run_loop(reader, writer)

    asyncio.run(_main())


if __name__ == "__main__":
    start_stdio_server()
