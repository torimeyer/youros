#!/bin/bash
# Block native file/shell tools whenever ostk is up.
# Exit 2 returns the message to Claude, who must retry with ostk.
#
# PROBE STRATEGY (race-free):
#   We previously used `curl --connect-timeout 1 -m 2 https://127.0.0.1:8000/api/status`.
#   That timed out intermittently under load even when uvicorn was healthy,
#   causing the hook to exit 0 and allow native Bash/Read/Edit/Grep to slip through.
#
#   The new probe is process-based:
#     1. pgrep for the uvicorn backend on port 8000
#     2. pgrep for the ostk MCP kernel
#   Either present means "ostk is up, block native tools."
#   Only if BOTH are absent do we allow native (ostk truly offline).
#
#   If anyone re-introduces a curl check, treat exit code 28 (timeout) as
#   "probably up, be conservative, BLOCK" rather than "allow".
#
# TRACE LOG: /tmp/ostk-first.log gets one stamped line per invocation.
#   Rotate to /tmp/ostk-first.log.old if it exceeds 1MB.

LOG=/tmp/ostk-first.log
# Rotate at 1MB
if [ -f "$LOG" ]; then
  SIZE=$(stat -f%z "$LOG" 2>/dev/null || stat -c%s "$LOG" 2>/dev/null || echo 0)
  if [ "$SIZE" -gt 1048576 ]; then
    mv "$LOG" "${LOG}.old" 2>/dev/null
  fi
fi

trace() {
  # $1 = branch taken, $2 = tool name (optional)
  echo "$(date '+%Y-%m-%d %H:%M:%S') tool=${2:-?} branch=$1" >> "$LOG" 2>/dev/null
}

# Probe: is ostk up?
if pgrep -f "uvicorn.*:8000" >/dev/null 2>&1; then
  OSTK_UP=1
elif pgrep -f "ostk kernel serve" >/dev/null 2>&1; then
  OSTK_UP=1
else
  OSTK_UP=0
fi

INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)

if [ "$OSTK_UP" -eq 0 ]; then
  trace "ostk-offline-allowed" "$TOOL"
  exit 0
fi

case "$TOOL" in
  Bash)  EQ="mcp__ostk__shell" ;;
  Read)  EQ="mcp__ostk__fs_read" ;;
  Edit)  EQ="mcp__ostk__edit" ;;
  Write) trace "write-no-ostk-equivalent-allowed" "$TOOL"; exit 0 ;;
  Grep|Glob) EQ="mcp__ostk__search" ;;
  *) trace "non-matching-allowed" "$TOOL"; exit 0 ;;
esac

# Allow pytest / vitest / tsc / scripts/* whitelisted native paths.
CMD=$(echo "$INPUT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)
case "$CMD" in
  *pytest*|*vitest*|*tsc*|*scripts/*)
    trace "whitelist-allowed" "$TOOL"
    exit 0
    ;;
esac

trace "blocked" "$TOOL"
echo "Blocked: use $EQ instead of $TOOL. Standing rule: ostk first." >&2
echo "If the ostk tool is deferred, load it via ToolSearch('select:$EQ') then retry." >&2
exit 2
