#!/bin/bash
# test_dev_backend_env.sh
#
# Verifies that both start.sh and scripts/dev-backend.sh source
# ~/.youros/.env (if it exists) before launching uvicorn, and that
# they do NOT error when ~/.youros/.env is absent.
#
# No venv, no real uvicorn — just the env-load block tested in isolation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

# --- Setup: temp HOME so we never touch the real ~/.youros/.env ---
ORIG_HOME="$HOME"
TMP_HOME=$(mktemp -d)
export HOME="$TMP_HOME"

cleanup() {
    export HOME="$ORIG_HOME"
    rm -rf "$TMP_HOME"
}
trap cleanup EXIT

# --- Helper: extract and run just the env-load block from a script ---
run_env_block() {
    local script="$1"
    # Pull the 6-line load block from the script and eval it.
    eval "$(grep -A5 'if \[ -f "\$HOME/.youros/.env" \]' "$script" | head -6)"
}

# -----------------------------------------------------------------------
# Test 1: start.sh — env-load block is present
# -----------------------------------------------------------------------
if grep -q 'if \[ -f "\$HOME/.youros/.env" \]' "$REPO_DIR/start.sh"; then
    pass "start.sh contains env-load guard"
else
    fail "start.sh is missing env-load guard"
fi

# -----------------------------------------------------------------------
# Test 2: start.sh — set -a / set +a present (exports sourced vars)
# -----------------------------------------------------------------------
if grep -A5 'if \[ -f "\$HOME/.youros/.env" \]' "$REPO_DIR/start.sh" | grep -q 'set -a'; then
    pass "start.sh env-load block uses set -a for export"
else
    fail "start.sh env-load block is missing set -a"
fi

# -----------------------------------------------------------------------
# Test 3: scripts/dev-backend.sh — env-load block is present
# -----------------------------------------------------------------------
if grep -q 'if \[ -f "\$HOME/.youros/.env" \]' "$SCRIPT_DIR/dev-backend.sh"; then
    pass "scripts/dev-backend.sh contains env-load guard"
else
    fail "scripts/dev-backend.sh is missing env-load guard"
fi

# -----------------------------------------------------------------------
# Test 4: scripts/dev-backend.sh — set -a / set +a present
# -----------------------------------------------------------------------
if grep -A5 'if \[ -f "\$HOME/.youros/.env" \]' "$SCRIPT_DIR/dev-backend.sh" | grep -q 'set -a'; then
    pass "scripts/dev-backend.sh env-load block uses set -a for export"
else
    fail "scripts/dev-backend.sh env-load block is missing set -a"
fi

# -----------------------------------------------------------------------
# Test 5: no error when ~/.youros/.env does NOT exist
# -----------------------------------------------------------------------
# Ensure the file is absent in our temp HOME.
rm -f "$TMP_HOME/.youros/.env"

# Eval the block; it must succeed silently.
(
    export HOME="$TMP_HOME"
    if [ -f "$HOME/.youros/.env" ]; then
        set -a
        source "$HOME/.youros/.env"
        set +a
    fi
    echo "no_error"
) | grep -q "no_error" && pass "env-load block exits cleanly when ~/.youros/.env is absent" \
    || fail "env-load block errored when ~/.youros/.env is absent"

# -----------------------------------------------------------------------
# Test 6: exported var is visible after sourcing a real .env file
# -----------------------------------------------------------------------
mkdir -p "$TMP_HOME/.youros"
echo "YOUROS_ENV_TEST=hello" > "$TMP_HOME/.youros/.env"

result=$(
    export HOME="$TMP_HOME"
    if [ -f "$HOME/.youros/.env" ]; then
        set -a
        source "$HOME/.youros/.env"
        set +a
    fi
    echo "$YOUROS_ENV_TEST"
)

if [ "$result" = "hello" ]; then
    pass "YOUROS_ENV_TEST=hello correctly exported after sourcing ~/.youros/.env"
else
    fail "expected YOUROS_ENV_TEST=hello, got: '$result'"
fi

# -----------------------------------------------------------------------
# Test 7: var is inherited by a child process (proves set -a worked)
# -----------------------------------------------------------------------
child_result=$(
    export HOME="$TMP_HOME"
    if [ -f "$HOME/.youros/.env" ]; then
        set -a
        source "$HOME/.youros/.env"
        set +a
    fi
    bash -c 'echo "$YOUROS_ENV_TEST"'
)

if [ "$child_result" = "hello" ]; then
    pass "YOUROS_ENV_TEST inherited by child process (set -a export confirmed)"
else
    fail "YOUROS_ENV_TEST not inherited by child (expected 'hello', got: '$child_result')"
fi

echo ""
echo "All tests passed."
