#!/usr/bin/env bash
HOOK_NAME=$(basename "$0")
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL:-?} exit=$?" >> /tmp/hook-trace.log' EXIT
# PreToolUse hook: blocks Monitor misuse as a file-read substitute.
# Streaming commands (tail -f, inotifywait -m, while-true loops) pass through.
# One-shot file I/O (open/read heredocs, bare cat/head/tail) is blocked.

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
cat > "$TMP"

python3 << GUARD_PY
import json, re, sys

BLOCK_MSG = (
    "Blocked: Monitor is for streaming events, not file reads. "
    "Use mcp__ostk__read for files (or native Read if mcp__ostk__read is not "
    "loaded; load it via ToolSearch(query='select:mcp__ostk__read')). "
    "Monitor commands should use tail -f, inotifywait -m, or while-true "
    "polling loops, not one-shot file I/O."
)

try:
    with open("$TMP") as _f:
        data = json.load(_f)
except Exception:
    sys.exit(0)

cmd = data.get("tool_input", {}).get("command", "")
if not cmd:
    sys.exit(0)

# ALLOW streaming patterns first
streaming = [
    r"tail\s+.*-[a-zA-Z]*f",
    r"tail\s+-[a-zA-Z]*f",
    r"inotifywait\s+.*-m",
    r"while\s+(true|:|\[)",
]
for pat in streaming:
    if re.search(pat, cmd, re.IGNORECASE | re.DOTALL):
        sys.exit(0)

# BLOCK: python3 (heredoc or -c) with open/read file I/O
if re.search(r"python3", cmd, re.IGNORECASE):
    if re.search(r"open\s*\(", cmd, re.DOTALL):
        if re.search(r"\.(read|readlines)\s*\(\)|open\s*\([^)]*['\"]w['\"]", cmd, re.DOTALL):
            print(BLOCK_MSG, file=sys.stderr)
            sys.exit(2)

# BLOCK: one-shot cat
if re.match(r"\s*cat\s+\S", cmd):
    print(BLOCK_MSG, file=sys.stderr)
    sys.exit(2)

# BLOCK: one-shot head
if re.match(r"\s*head\s+", cmd):
    print(BLOCK_MSG, file=sys.stderr)
    sys.exit(2)

# BLOCK: bare tail without -f
if re.match(r"\s*tail\s+", cmd) and not re.search(r"-[a-zA-Z]*f\s|--follow", cmd):
    print(BLOCK_MSG, file=sys.stderr)
    sys.exit(2)

# BLOCK: wc -l (one-shot file size)
if re.match(r"\s*wc\s+-l", cmd):
    print(BLOCK_MSG, file=sys.stderr)
    sys.exit(2)

sys.exit(0)
GUARD_PY
