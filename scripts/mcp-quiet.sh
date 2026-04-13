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
exec "$@"
