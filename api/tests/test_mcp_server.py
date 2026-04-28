"""Tests for the myOS MCP stdio server dispatch layer.

Covers:
  - tool registry has exactly the five required tools
  - each tool has a name and inputSchema
  - dispatching task.list returns a list-shaped result
  - dispatching an unknown tool raises ValueError
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

def test_tool_count():
    from mcp_server.tools import TOOLS
    names = {t["name"] for t in TOOLS}
    assert names == {"task.list", "task.add", "spec.list", "agent.spawn", "memory.search"}


def test_each_tool_has_input_schema():
    from mcp_server.tools import TOOLS
    for tool in TOOLS:
        assert "inputSchema" in tool, f"{tool['name']} missing inputSchema"
        assert tool["inputSchema"].get("type") == "object", f"{tool['name']} inputSchema must be object"


# ---------------------------------------------------------------------------
# Dispatch — task.list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_task_list_returns_list():
    from mcp_server.tools import dispatch

    fake_tasks = [
        {"id": "→001", "title": "Write tests", "status": "open", "priority": "P1"},
        {"id": "→002", "title": "Ship MCP server", "status": "open", "priority": "P0"},
    ]

    mock_ostk = AsyncMock()
    mock_ostk.list_tasks = AsyncMock(return_value=fake_tasks)

    with patch("mcp_server.tools.handle_task_list", wraps=None) as _:
        # Patch at the import point used inside the handler
        with patch("services.ostk.ostk", mock_ostk):
            result = await dispatch("task.list", {})

    assert "tasks" in result
    assert isinstance(result["tasks"], list)


# ---------------------------------------------------------------------------
# Dispatch — task.list with status filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_task_list_passes_filters():
    from mcp_server.tools import dispatch

    mock_ostk = AsyncMock()
    mock_ostk.list_tasks = AsyncMock(return_value=[])

    with patch("services.ostk.ostk", mock_ostk):
        result = await dispatch("task.list", {"status": "open", "priority": "P0"})

    mock_ostk.list_tasks.assert_called_once_with(status="open", priority="P0")
    assert result == {"tasks": []}


# ---------------------------------------------------------------------------
# Dispatch — unknown tool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_unknown_tool_raises():
    from mcp_server.tools import dispatch
    with pytest.raises(ValueError, match="Unknown tool"):
        await dispatch("nonexistent.tool", {})


# ---------------------------------------------------------------------------
# Server _handle — initialize
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_initialize():
    from mcp_server.server import _handle
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = await _handle(msg)
    assert resp["id"] == 1
    assert "protocolVersion" in resp["result"]
    assert "tools" in resp["result"]["capabilities"]


# ---------------------------------------------------------------------------
# Server _handle — tools/list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_tools_list():
    from mcp_server.server import _handle
    msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    resp = await _handle(msg)
    tools = resp["result"]["tools"]
    assert len(tools) == 5
    names = {t["name"] for t in tools}
    assert "task.list" in names


# ---------------------------------------------------------------------------
# Server _handle — tools/call task.list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_tools_call_task_list():
    from mcp_server.server import _handle

    fake_tasks = [{"id": "→001", "title": "Hello", "status": "open"}]
    mock_ostk = AsyncMock()
    mock_ostk.list_tasks = AsyncMock(return_value=fake_tasks)

    with patch("services.ostk.ostk", mock_ostk):
        msg = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "task.list", "arguments": {}},
        }
        resp = await _handle(msg)

    assert resp["id"] == 3
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert "tasks" in payload
    assert payload["tasks"] == fake_tasks
