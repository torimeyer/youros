#!/bin/bash
# Regression test for →977: hooks must source deny.sh via BASH_SOURCE[0],
# not CLAUDE_PROJECT_DIR or git rev-parse, so they work in temp dirs and
# outside any git repo.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="${SCRIPT_DIR}/.."

# ── Setup: temp dir that is NOT a git repo ───────────────────────────────────
TMPDIR_HOOK=$(mktemp -d)
trap 'rm -rf "$TMPDIR_HOOK"' EXIT

# Copy hook + its lib/deny.sh into the temp dir (simulates a temp-dir invocation)
cp "$HOOKS_DIR/ostk-first.sh" "$TMPDIR_HOOK/ostk-first.sh"
chmod +x "$TMPDIR_HOOK/ostk-first.sh"
mkdir -p "$TMPDIR_HOOK/lib"
cp "$HOOKS_DIR/lib/deny.sh" "$TMPDIR_HOOK/lib/deny.sh"

# Working dir with no git repo and no ostk socket
TMPDIR_WORK=$(mktemp -d)
trap 'rm -rf "$TMPDIR_HOOK" "$TMPDIR_WORK"' EXIT

# ── Run: no CLAUDE_PROJECT_DIR, cwd outside git, no ostk socket ─────────────
INPUT=$(python3 -c "import json; print(json.dumps({'tool_name':'Bash','tool_input':{'command':'echo hello'}}))")

output=$(cd "$TMPDIR_WORK" && unset CLAUDE_PROJECT_DIR && echo "$INPUT" | bash "$TMPDIR_HOOK/ostk-first.sh" 2>&1)
exit_code=$?

# ── Assert: no "No such file" error from deny.sh sourcing ───────────────────
if echo "$output" | grep -q "No such file"; then
  echo "FAIL: deny.sh source error detected. Output: $output"
  exit 1
fi

# Hook exits 0 when no ostk socket is present (falls through)
if [ "$exit_code" -ne 0 ]; then
  echo "FAIL: expected exit 0 (no socket, fall-through), got exit $exit_code. Output: $output"
  exit 1
fi

# ── Assert: init_deny_traps is callable (sourced correctly) ─────────────────
callable_output=$(bash -c "
  source '$TMPDIR_HOOK/lib/deny.sh'
  init_deny_traps
  echo 'init_deny_traps: ok'
" 2>&1)
if ! echo "$callable_output" | grep -q "init_deny_traps: ok"; then
  echo "FAIL: init_deny_traps not callable after source. Output: $callable_output"
  exit 1
fi

echo "PASS: deny.sh sourced correctly via BASH_SOURCE[0] in temp dir outside git repo"
exit 0
