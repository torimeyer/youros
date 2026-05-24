#!/bin/bash
# Tests for →990: ostk-first.sh advisory mode behaviour.
# Since Erik PR #1 the hook returns exit 0 with a stderr hint when
# ostk MCP is up and a native tool is called. It never blocks (exit 2).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="${SCRIPT_DIR}/.."
HOOK="$HOOKS_DIR/ostk-first.sh"

tmp_dir=$(mktemp -d)
nc_pid=""
trap 'rm -rf "$tmp_dir"; [ -n "${nc_pid:-}" ] && kill "$nc_pid" 2>/dev/null; true' EXIT

pass_count=0
fail_count=0

# Create a real Unix-domain socket so the hook passes both the file-existence
# check (-S) and the liveness probe (python3 connect). nc -lkU keeps accepting
# connections across multiple hook invocations.
SOCK_DIR="$tmp_dir/.ostk"
SOCK_PATH="$SOCK_DIR/ostk.sock"
mkdir -p "$SOCK_DIR"
nc -lkU "$SOCK_PATH" </dev/null &>/dev/null &
nc_pid=$!

# Wait up to 1s for nc to bind the socket
for _i in 1 2 3 4 5; do
  [ -S "$SOCK_PATH" ] && break
  sleep 0.2
done
if [ ! -S "$SOCK_PATH" ]; then
  echo "FAIL: setup - nc -lkU did not create socket at $SOCK_PATH; cannot run advisory tests"
  exit 1
fi

# JSON input that simulates a native Bash tool call
BASH_INPUT=$(python3 -c "import json; print(json.dumps({'tool_name':'Bash','tool_input':{'command':'echo hello'}}))")

# Run the hook from $tmp_dir (non-git cwd) so git rev-parse returns empty,
# disabling the worktree escape hatch that would exit early with a different
# message. CLAUDE_PROJECT_DIR=$tmp_dir directs the socket search to our mock.
(
  cd "$tmp_dir"
  echo "$BASH_INPUT" \
    | CLAUDE_PROJECT_DIR="$tmp_dir" bash "$HOOK" \
    >/dev/null 2>"$tmp_dir/hook_stderr.txt"
  echo $? >"$tmp_dir/hook_exit.txt"
)

hook_exit=$(cat "$tmp_dir/hook_exit.txt")
hook_stderr=$(cat "$tmp_dir/hook_stderr.txt")

# ── Test 1: advisory_mode_returns_exit_0_for_native_bash ─────────────────────
if [ "$hook_exit" -eq 0 ]; then
  echo "PASS: advisory_mode_returns_exit_0_for_native_bash"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: advisory_mode_returns_exit_0_for_native_bash - expected exit 0, got $hook_exit"
  fail_count=$((fail_count + 1))
fi

# ── Test 2: advisory_mode_writes_hint_to_stderr ───────────────────────────────
if echo "$hook_stderr" | grep -qi "ostk\|prefer"; then
  echo "PASS: advisory_mode_writes_hint_to_stderr"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: advisory_mode_writes_hint_to_stderr - expected 'ostk' or 'prefer' in stderr, got: $hook_stderr"
  fail_count=$((fail_count + 1))
fi

# ── Test 3: advisory_mode_does_not_block_when_socket_alive ───────────────────
if [ "$hook_exit" -ne 2 ]; then
  echo "PASS: advisory_mode_does_not_block_when_socket_alive"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: advisory_mode_does_not_block_when_socket_alive - hook returned exit 2 (deny)"
  fail_count=$((fail_count + 1))
fi

echo ""
echo "passed: $pass_count / failed: $fail_count"
[ "$fail_count" -eq 0 ] || exit 1
