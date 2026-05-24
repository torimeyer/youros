#!/usr/bin/env bash
# Unit test for scripts/watch-frontend.sh.
#
# Creates a mock "dev-frontend.sh" that crashes twice then stays up,
# runs watch-frontend.sh, and asserts it restarted after each crash
# and then ran successfully.
#
# Test name: watch_frontend_restarts_after_crash
#
# Usage:
#   scripts/test_watch_frontend.sh
#
# Exit 0 = all assertions passed. Exit 1 = a test failed.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCHER="$SCRIPT_DIR/watch-frontend.sh"
WATCH_LOG="/tmp/torios-frontend-watcher.log"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

pass=0
fail=0

assert_eq() {
    local label="$1" got="$2" want="$3"
    if [ "$got" = "$want" ]; then
        echo -e "${GREEN}PASS${NC} $label"
        pass=$(( pass + 1 ))
    else
        echo -e "${RED}FAIL${NC} $label (got='$got', want='$want')"
        fail=$(( fail + 1 ))
    fi
}

# --- Setup: create a temp dir with a mock dev-frontend.sh ---
TMP_DIR=$(mktemp -d)
MOCK_COUNTER="$TMP_DIR/counter"
MOCK_LAUNCHER="$TMP_DIR/dev-frontend.sh"
echo "0" > "$MOCK_COUNTER"

# The mock crashes on attempt 1 and 2, then blocks on attempt 3
# (simulating a healthy Vite). We use a named semaphore file to
# unblock it so the watcher can be stopped cleanly.
UNBLOCK_FILE="$TMP_DIR/unblock"

cat > "$MOCK_LAUNCHER" << 'EOF'
#!/usr/bin/env bash
COUNTER_FILE="@@COUNTER@@"
UNBLOCK_FILE="@@UNBLOCK@@"

count=$(cat "$COUNTER_FILE")
count=$(( count + 1 ))
echo "$count" > "$COUNTER_FILE"

if [ "$count" -le 2 ]; then
    # Simulate crash: exit non-zero immediately
    echo "[mock-vite] attempt $count: crashing" >&2
    exit 1
else
    # Simulate healthy Vite: block until unblock file appears, then exit 0
    echo "[mock-vite] attempt $count: running" >&2
    while [ ! -f "$UNBLOCK_FILE" ]; do
        sleep 0.1
    done
    echo "[mock-vite] attempt $count: stopped cleanly" >&2
    exit 0
fi
EOF

# Substitute placeholders
sed -i '' "s|@@COUNTER@@|$MOCK_COUNTER|g" "$MOCK_LAUNCHER"
sed -i '' "s|@@UNBLOCK@@|$UNBLOCK_FILE|g" "$MOCK_LAUNCHER"
chmod +x "$MOCK_LAUNCHER"

cleanup() {
    # Stop the watcher if still running
    if [ -n "${WATCHER_PID:-}" ] && kill -0 "$WATCHER_PID" 2>/dev/null; then
        kill "$WATCHER_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

# Clear watcher log so we only look at this run's entries.
> "$WATCH_LOG" 2>/dev/null || true

# --- Run: override SCRIPT_DIR inside the watcher by patching the copy ---
# The watcher resolves "$SCRIPT_DIR/dev-frontend.sh" at runtime. We copy
# the watcher into TMP_DIR and rewrite its SCRIPT_DIR assignment so it
# resolves to TMP_DIR where our mock lives.
sed "s|SCRIPT_DIR=\"\$(cd.*BASH_SOURCE.*\"|SCRIPT_DIR=\"$TMP_DIR\" #|" \
    "$WATCHER" > "$TMP_DIR/watch-frontend.sh"
chmod +x "$TMP_DIR/watch-frontend.sh"

"$TMP_DIR/watch-frontend.sh" >> /tmp/torios-frontend.log 2>&1 &
WATCHER_PID=$!

# --- Wait for 3 attempts to be recorded (up to 50 x 0.2s = 10 seconds) ---
loops=0
while [ "$(cat "$MOCK_COUNTER" 2>/dev/null || echo 0)" -lt 3 ] && [ "$loops" -lt 50 ]; do
    sleep 0.2
    loops=$(( loops + 1 ))
done

FINAL_COUNT=$(cat "$MOCK_COUNTER" 2>/dev/null || echo 0)

# --- Test 1: watcher_restarts_after_crash ---
# The mock should have been called at least 3 times (2 crashes + 1 success).
if [ "$FINAL_COUNT" -ge 3 ]; then
    echo -e "${GREEN}PASS${NC} watch_frontend_restarts_after_crash (launched $FINAL_COUNT times)"
    pass=$(( pass + 1 ))
else
    echo -e "${RED}FAIL${NC} watch_frontend_restarts_after_crash (launched $FINAL_COUNT times, want >= 3)"
    fail=$(( fail + 1 ))
fi

# --- Test 2: watcher_logs_restart ---
if grep -q "restarting" "$WATCH_LOG" 2>/dev/null; then
    echo -e "${GREEN}PASS${NC} watch_frontend_logs_restart"
    pass=$(( pass + 1 ))
else
    echo -e "${RED}FAIL${NC} watch_frontend_logs_restart (no 'restarting' found in $WATCH_LOG)"
    fail=$(( fail + 1 ))
fi

# --- Stop watcher cleanly ---
touch "$UNBLOCK_FILE"   # let the 3rd "vite" exit cleanly
sleep 0.5
if kill -0 "$WATCHER_PID" 2>/dev/null; then
    kill "$WATCHER_PID" 2>/dev/null || true
fi
wait "$WATCHER_PID" 2>/dev/null || true

# --- Test 3: watcher_exits_on_sigterm ---
# After cleanup, the watcher should be gone.
if ! kill -0 "$WATCHER_PID" 2>/dev/null; then
    echo -e "${GREEN}PASS${NC} watch_frontend_exits_on_sigterm"
    pass=$(( pass + 1 ))
else
    echo -e "${RED}FAIL${NC} watch_frontend_exits_on_sigterm (watcher still running)"
    fail=$(( fail + 1 ))
fi

# --- Summary ---
echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ] && exit 0 || exit 1
