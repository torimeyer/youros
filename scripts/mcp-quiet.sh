#!/bin/bash
# Wrapper for MCP server commands that suppresses shell job-control
# noise ([N] PID lines) leaking into the terminal on session start.
#
# Usage in .mcp.json:
#   "command": "scripts/mcp-quiet.sh",
#   "args": ["ostk", "kernel", "serve"]
#
# How it works: `set +m` disables job monitoring in this shell so
# any child-process creation stays silent. `exec` replaces this
# shell with the real command so there is no extra process in the
# tree and MCP stdin/stdout pass through cleanly.

set +m 2>/dev/null

# Warm-up the ostk daemon before handing off to the MCP server.
#
# Why: ostk kernel serve calls the daemon for every operation, including the
# initial tools/list handshake Claude Code fires at session start. If the
# daemon is cold (just restarted after the 30-minute idle timeout, or under
# brief connection pressure), that tools/list round-trip can be slow enough
# that Claude Code either times out (-32000 Connection closed) or accepts a
# truncated response and loses tools like bash/read/fs_ops for the session.
#
# A single "ostk work list" call here costs ~4 ms when the daemon is hot and
# ~2-3 s when it needs to restart. Either way the daemon is ready before the
# MCP server starts, so the tools/list handshake sees a fast path.
if [[ "$*" == *"ostk"*"kernel"*"serve"* ]]; then
    ostk work list --json >/dev/null 2>&1 || true
fi

exec "$@"
