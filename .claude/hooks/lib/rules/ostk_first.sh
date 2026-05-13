#!/usr/bin/env bash
# Rule: ostk_first
# Replaces: ostk-first.sh
# Called from: pre-tool-guard.sh on Bash|Read|Edit|Write|Grep|Glob
# Assumes: load-rule.sh, log-fire.sh, deny.sh already sourced by caller.
# Currently advisory-only (exits 0 with hint). "enforce" mode not wired yet.

_ostk_first_check() {
  local tool="$1" cmd="${2:-}"
  local mode
  mode=$(rule_param "ostk_first.mode" "advisory")

  local LOG=/tmp/ostk-first.log
  # Rotate at 1MB
  if [ -f "$LOG" ]; then
    local SIZE
    SIZE=$(stat -f%z "$LOG" 2>/dev/null || stat -c%s "$LOG" 2>/dev/null || echo 0)
    if [ "${SIZE:-0}" -gt 1048576 ]; then
      mv "$LOG" "${LOG}.old" 2>/dev/null
    fi
  fi

  # Probe: socket-existence check.
  local PROJ_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
  local SOCK="${PROJ_DIR}/.ostk/ostk.sock"

  # Search up to 3 levels if not found
  if [ ! -S "$SOCK" ]; then
    local SEARCH_ROOT="${PROJ_DIR:-$HOME/claude}"
    SOCK=$(find "$SEARCH_ROOT" -maxdepth 3 -name "ostk.sock" 2>/dev/null | head -1)
  fi

  # Worktree escape hatch
  local _GIT_LOCAL_ROOT
  _GIT_LOCAL_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
  if [ -n "$_GIT_LOCAL_ROOT" ] && [ "$_GIT_LOCAL_ROOT" != "$PROJ_DIR" ]; then
    local _LOCAL_SOCK="${_GIT_LOCAL_ROOT}/.ostk/ostk.sock"
    if [ ! -S "$_LOCAL_SOCK" ]; then
      echo "ostk socket absent in worktree root $_GIT_LOCAL_ROOT, native fallback allowed" >&2
      log_rule_fire "ostk_first" "$tool" "allow" "worktree no-local-sock"
      return 0
    fi
  fi

  if [ ! -S "$SOCK" ]; then
    echo "ostk MCP offline, native fallback allowed" >&2
    log_rule_fire "ostk_first" "$tool" "allow" "ostk offline"
    return 0
  fi

  # Liveness probe
  if ! python3 -c "
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(0.3)
try:
    s.connect('$SOCK')
    s.close()
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    echo "ostk MCP socket present but daemon not responding, native fallback allowed" >&2
    log_rule_fire "ostk_first" "$tool" "allow" "ostk socket dead"
    return 0
  fi

  local EQ
  case "$tool" in
    Bash) EQ="mcp__ostk__bash" ;;
    Read) EQ="mcp__ostk__read" ;;
    Edit) EQ="mcp__ostk__fs_ops" ;;
    Write) EQ="mcp__ostk__fs_ops" ;;
    Grep|Glob) EQ="mcp__ostk__search" ;;
    *)
      log_rule_fire "ostk_first" "$tool" "allow" "non-matching tool"
      return 0
      ;;
  esac

  # Allow pytest/vitest/tsc/scripts/* via whitelist
  case "$cmd" in
    *pytest*|*vitest*|*tsc*|*scripts/*)
      log_rule_fire "ostk_first" "$tool" "allow" "whitelisted command"
      return 0
      ;;
  esac

  # Advisory only: emit hint, allow through
  echo "Hint: prefer $EQ over $tool when ostk is fully wired." >&2
  log_rule_fire "ostk_first" "$tool" "allow" "advisory hint emitted"
  return 0
}
