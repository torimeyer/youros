#!/bin/bash
# Verify ostk-first.sh fails open when the socket file exists but the
# daemon is not actually responding. Without this fail-open, subagent
# worktrees that inherit a stale .ostk/ostk.sock get all native tools
# blocked while their MCP-ostk tools also aren't loaded — total deadlock.

set -u
HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/ostk-first.sh"
PASS=0
FAIL=0

assert_eq() {
  if [ "$2" = "$3" ]; then
    echo "  PASS: $1"; PASS=$((PASS+1))
  else
    echo "  FAIL: $1 — expected '$2', got '$3'"; FAIL=$((FAIL+1))
  fi
}

assert_contains() {
  case "$3" in
    *"$2"*) echo "  PASS: $1"; PASS=$((PASS+1)) ;;
    *) echo "  FAIL: $1 — needle not in haystack"; echo "    needle: $2"; echo "    actual: $3"; FAIL=$((FAIL+1)) ;;
  esac
}

# --------- Case 1: socket file exists but no listener (daemon dead) -----
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
mkdir -p "$SCRATCH/.ostk"
python3 -c "
import socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind('$SCRATCH/.ostk/ostk.sock')
s.close()  # bind without listen — socket file remains, no listener
"
[ -S "$SCRATCH/.ostk/ostk.sock" ] || { echo "FAIL: could not create stale socket"; exit 1; }

INPUT='{"tool_name":"Bash","tool_input":{"command":"ls"}}'
OUT=$(printf '%s' "$INPUT" | CLAUDE_PROJECT_DIR="$SCRATCH" bash "$HOOK" 2>&1)
RC=$(printf '%s' "$INPUT" | CLAUDE_PROJECT_DIR="$SCRATCH" bash "$HOOK" >/dev/null 2>/dev/null; echo $?)
assert_eq "stale socket: exit 0 (fallback allowed)" "0" "$RC"
assert_contains "stale socket: stderr says 'daemon not responding'" "daemon not responding" "$OUT"

# --------- Case 2: no socket file at all (existing fail-open path) -----
SCRATCH2=$(mktemp -d)
RC=$(printf '%s' "$INPUT" | CLAUDE_PROJECT_DIR="$SCRATCH2" bash "$HOOK" >/dev/null 2>/dev/null; echo $?)
assert_eq "no socket file: exit 0 (existing fallback)" "0" "$RC"
rm -rf "$SCRATCH2"

# --------- Case 3: live listener: still blocks ---------
SCRATCH3=$(mktemp -d)
mkdir -p "$SCRATCH3/.ostk"
LIVE_SOCK="$SCRATCH3/.ostk/ostk.sock"
# Spin up a tiny background unix listener
python3 -c "
import socket, signal, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind('$LIVE_SOCK')
s.listen(1)
# Print PID so we can kill on cleanup
import os
print(os.getpid())
sys.stdout.flush()
# Block forever
while True:
    try:
        c, _ = s.accept()
        c.close()
    except KeyboardInterrupt:
        break
" &
LISTENER_PID=$!
trap 'rm -rf "$SCRATCH" "$SCRATCH3"; kill $LISTENER_PID 2>/dev/null || true' EXIT

# Wait briefly for the listener to bind
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -S "$LIVE_SOCK" ] && python3 -c "
import socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(0.2)
s.connect('$LIVE_SOCK')
s.close()
" 2>/dev/null && break
  sleep 0.1
done

OUT=$(printf '%s' "$INPUT" | CLAUDE_PROJECT_DIR="$SCRATCH3" bash "$HOOK" 2>&1)
RC=$(printf '%s' "$INPUT" | CLAUDE_PROJECT_DIR="$SCRATCH3" bash "$HOOK" >/dev/null 2>/dev/null; echo $?)
assert_eq "live socket + Bash: exit 2 (blocks)" "2" "$RC"
assert_contains "live socket: stderr suggests mcp__ostk__bash" "mcp__ostk__bash" "$OUT"

echo
echo "passed: $PASS / failed: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
