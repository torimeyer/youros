"""
Regression test for →1199 (third fix): [loadavg] call site in dispatch.rs.

Root cause: generate_loadavg_line() was added to helpers.rs by e9909d7 but
never wired up as a caller in dispatch.rs. The MCP tool response assembly did
not include it, so the binary continued using the per-session cached register
from the now-deleted src/kernel/init_full.rs (always 0).

This test verifies the call site by starting ostk in MCP serve mode with a
controlled fake .ostk/ state (N open needles, M active agents) and asserting
that the tool response includes a [loadavg] line with the correct counts.

Fails BEFORE fix: [loadavg] shows "0 open" (cached register) or is absent.
Passes AFTER fix: [loadavg] shows "2 open" (live disk read).
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

OSTK_BIN = os.environ.get("OSTK_BIN", "ostk")
NOW_ISO = "2099-01-01T00:00:00Z"  # far-future last_seen → always alive


def _write_issues(issues_path: Path, entries: list[dict]) -> None:
    issues_path.parent.mkdir(parents=True, exist_ok=True)
    issues_path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


def _write_agents(agents_path: Path, entries: list[dict]) -> None:
    agents_path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


def _mcp_line(obj: dict) -> str:
    return json.dumps(obj) + "\n"


def _start_ostk_mcp(cwd: str) -> subprocess.Popen:
    return subprocess.Popen(
        [OSTK_BIN, "kernel", "serve"],
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )


def _mcp_initialize(proc: subprocess.Popen) -> None:
    req = _mcp_line({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    })
    proc.stdin.write(req)
    proc.stdin.flush()
    proc.stdout.readline()  # consume initialize response
    # Required by MCP spec: client must send initialized notification before calling tools
    notif = _mcp_line({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    proc.stdin.write(notif)
    proc.stdin.flush()


def _mcp_tool_call(proc: subprocess.Popen, call_id: int, name: str, args: dict) -> dict:
    req = _mcp_line({
        "jsonrpc": "2.0", "id": call_id, "method": "tools/call",
        "params": {"name": name, "arguments": args},
    })
    proc.stdin.write(req)
    proc.stdin.flush()
    raw = proc.stdout.readline()
    return json.loads(raw)


@pytest.mark.integration
def test_loadavg_line_present_and_nonzero():
    """[loadavg] must appear in MCP tool responses and show non-zero needle count.

    This is the call-site regression: dispatch.rs must call generate_loadavg_line()
    so the [loadavg] line is emitted with live disk values instead of the stale
    per-session cached register (always 0).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        ostk_dir = Path(tmpdir) / ".ostk"

        # 2 open needles (one P0, one P1) + 1 in_progress + 1 closed
        _write_issues(ostk_dir / "needles" / "issues.jsonl", [
            {"id": "→T01", "status": "open",        "priority": "P0", "title": "critical"},
            {"id": "→T02", "status": "open",        "priority": "P1", "title": "normal"},
            {"id": "→T03", "status": "in_progress", "priority": "P2", "title": "wip"},
            {"id": "→T04", "status": "closed",      "priority": "P1", "title": "done"},
        ])

        # 2 alive agents (far-future last_seen bypasses 90s staleness filter)
        _write_agents(ostk_dir / "agents.jsonl", [
            {"name": "agent-a", "status": "active", "last_seen": NOW_ISO},
            {"name": "agent-b", "status": "active", "last_seen": NOW_ISO},
        ])

        proc = _start_ostk_mcp(tmpdir)
        try:
            _mcp_initialize(proc)
            # Any tool call triggers the footer
            response = _mcp_tool_call(proc, 2, "bash", {"cmd": "echo probe"})
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    assert "result" in response, f"MCP error: {response}"
    content = response["result"]["content"]
    assert content, "empty content in response"
    text = content[0]["text"]

    # Find the [loadavg] line
    loadavg_line = next(
        (line for line in text.splitlines() if line.startswith("[loadavg]")),
        None,
    )
    assert loadavg_line is not None, (
        f"[loadavg] line missing from MCP tool response.\n"
        f"dispatch.rs must call generate_loadavg_line() — see →1199.\n"
        f"Full response text:\n{text}"
    )

    # count_active_needles counts open+in_progress = 3 (2 open + 1 in_progress)
    # The format is: [loadavg] needles: N open (M P0) | fleet: A/T alive | nudges: K
    assert "3 open" in loadavg_line or "2 open" in loadavg_line, (
        f"Expected 2 or 3 open needles in [loadavg] (open or open+in_progress).\n"
        f"Got: {loadavg_line!r}\n"
        "The live disk read must show non-zero counts."
    )
    assert "0 open" not in loadavg_line, (
        f"[loadavg] shows 0 open — cached register still in use.\n"
        f"Got: {loadavg_line!r}"
    )


@pytest.mark.integration
def test_loadavg_fleet_nonzero():
    """[loadavg] fleet count must reflect alive agents from agents.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ostk_dir = Path(tmpdir) / ".ostk"

        _write_issues(ostk_dir / "needles" / "issues.jsonl", [
            {"id": "→T01", "status": "open", "priority": "P1", "title": "a"},
        ])
        _write_agents(ostk_dir / "agents.jsonl", [
            {"name": "fleet-1", "status": "active", "last_seen": NOW_ISO},
            {"name": "fleet-2", "status": "active", "last_seen": NOW_ISO},
            {"name": "fleet-3", "status": "active", "last_seen": NOW_ISO},
        ])

        proc = _start_ostk_mcp(tmpdir)
        try:
            _mcp_initialize(proc)
            response = _mcp_tool_call(proc, 2, "bash", {"cmd": "echo probe"})
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    text = response.get("result", {}).get("content", [{}])[0].get("text", "")
    loadavg_line = next(
        (l for l in text.splitlines() if l.startswith("[loadavg]")), None
    )
    assert loadavg_line is not None, f"[loadavg] missing: {text}"
    assert "fleet: 0/0" not in loadavg_line, (
        f"[loadavg] fleet is 0/0 — agents.jsonl is not being read.\n"
        f"Got: {loadavg_line!r}"
    )
