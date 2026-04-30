#!/bin/bash
# Block native file/shell tools whenever ostk is up.
# Exit 2 returns the message to Claude, who must retry with ostk.
#
# PROBE STRATEGY:
#   Check for the kernel socket at .ostk/ostk.sock.  If the socket file is
#   absent, ostk MCP is offline and we exit 0 to allow native tools rather
#   than deadlocking.  Socket present → ostk is up → block and redirect.
#
#   Previous approach used pgrep for uvicorn/ostk processes; replaced because
#   it gave false-positives when processes were up but MCP was unreachable.
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

# Probe: socket-existence check.
# If .ostk/ostk.sock is absent ostk MCP is offline — fall through to native
# tools rather than deadlocking on an unreachable MCP server.
PROJ_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
SOCK="${PROJ_DIR}/.ostk/ostk.sock"

# Fallback: when Claude is opened from a parent directory (e.g. ~/claude
# instead of ~/claude/torios), PROJ_DIR won't contain the socket. Search
# up to 3 levels deep so the hook fires regardless of launch directory.
if [ ! -S "$SOCK" ]; then
  SEARCH_ROOT="${PROJ_DIR:-$HOME/claude}"
  SOCK=$(find "$SEARCH_ROOT" -maxdepth 3 -name "ostk.sock" 2>/dev/null | head -1)
fi

INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)

# Worktree escape hatch: if cwd is a git worktree under .claude/worktrees/
# and no local .ostk/ostk.sock exists in the worktree root, allow native
# fallback. This handles the spawn race where the symlink hasn't been
# created yet, or the main daemon is restarting.
_GIT_LOCAL_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -n "$_GIT_LOCAL_ROOT" ] && [ "$_GIT_LOCAL_ROOT" != "$PROJ_DIR" ]; then
  _LOCAL_SOCK="${_GIT_LOCAL_ROOT}/.ostk/ostk.sock"
  if [ ! -S "$_LOCAL_SOCK" ]; then
    trace "worktree-no-local-sock-allowed" "$TOOL"
    echo "ostk socket absent in worktree root $_GIT_LOCAL_ROOT, native fallback allowed" >&2
    exit 0
  fi
fi

if [ ! -S "$SOCK" ]; then
  trace "ostk-offline-allowed" "$TOOL"
  echo "ostk MCP offline, native fallback allowed" >&2
  exit 0
fi

case "$TOOL" in
  Bash) EQ="mcp__ostk__bash" ;;
  Read) EQ="mcp__ostk__read" ;;
  Edit) EQ="mcp__ostk__fs_ops" ;;
  Write) EQ="mcp__ostk__fs_ops" ;;
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
