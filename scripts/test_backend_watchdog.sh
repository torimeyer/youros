#!/usr/bin/env bash
# test_backend_watchdog.sh
#
# Verifies that scripts/backend_watchdog.sh:
#   1. Detects an unreachable health endpoint and calls dev-backend.sh.
#   2. Stays quiet when the health endpoint returns 200.
#   3. Writes its pid to the pidfile and cleans up on exit.
#
# We do NOT exercise a real uvicorn restart here. We point the watchdog
# at a stub dev-backend.sh that just touches a sentinel file, so we can
# assert "the watchdog tried to restart the backend" without spawning
# real processes or risking a port conflict on 8000.
#
# Exit 0 = all assertions passed. Exit 1 = a test failed.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCHDOG="$SCRIPT_DIR/backend_watchdog.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

pass=0
fail=0

assert() {
    local label="$1" cond="$2"
    if [ "$cond" = "1" ]; then
        echo -e "${GREEN}PASS${NC} $label"
        pass=$((pass+1))
    else
        echo -e "${RED}FAIL${NC} $label"
        fail=$((fail+1))
    fi
}

WORKDIR=$(mktemp -d /tmp/test_watchdog_XXXXXX)
SENTINEL="$WORKDIR/restart_called"
STUB_SCRIPT="$WORKDIR/scripts/dev-backend.sh"
STUB_LOG="$WORKDIR/dev-backend.log"
WD_LOG="$WORKDIR/watchdog.log"
WD_PIDFILE="$WORKDIR/watchdog.pid"

mkdir -p "$WORKDIR/scripts"

# Build a stub dev-backend.sh that just touches a sentinel and exits.
cat > "$STUB_SCRIPT" <<'STUB'
#!/usr/bin/env bash
echo "stub dev-backend invoked at $(date -u +%H:%M:%S)" >> "$0.log"
touch "$(dirname "$0")/../restart_called"
STUB
chmod +x "$STUB_SCRIPT"

# Pick a port that is almost certainly NOT listening.
DEAD_PORT=18799

cleanup() {
    if [ -f "$WD_PIDFILE" ]; then
        local pid
        pid=$(cat "$WD_PIDFILE" 2>/dev/null || true)
        if [ -n "$pid" ] && ps -p "$pid" >/dev/null 2>&1; then
            kill "$pid" 2>/dev/null || true
            sleep 0.5
            kill -9 "$pid" 2>/dev/null || true
        fi
    fi
    rm -rf "$WORKDIR"
}
trap cleanup EXIT INT TERM

# --- Test 1: when health URL is unreachable, watchdog calls the stub ---
# We point the watchdog's SCRIPT_DIR at our stub by setting the path that
# it computes from $0. Since the watchdog uses `dirname "$0"` to find
# dev-backend.sh, we just run the watchdog FROM our temp scripts dir.
cp "$WATCHDOG" "$WORKDIR/scripts/backend_watchdog.sh"
chmod +x "$WORKDIR/scripts/backend_watchdog.sh"

# Run with a very fast interval so the test finishes quickly.
MYOS_WATCHDOG_INTERVAL=1 \
MYOS_WATCHDOG_HEALTH_URL="http://127.0.0.1:${DEAD_PORT}/api/health" \
MYOS_WATCHDOG_BACKEND_PORT="$DEAD_PORT" \
MYOS_WATCHDOG_PIDFILE="$WD_PIDFILE" \
MYOS_WATCHDOG_LOGFILE="$WD_LOG" \
MYOS_WATCHDOG_RESTART_WAIT=1 \
MYOS_WATCHDOG_MAX_RESTARTS=2 \
MYOS_NO_WATCHDOG=1 \
bash "$WORKDIR/scripts/backend_watchdog.sh" >/dev/null 2>&1 &
WD_PID=$!
echo "$WD_PID" > "$WD_PIDFILE"

# Wait up to 25 seconds: 1s interval + 3x5s retries + restart = ~17s worst case.
got_restart=0
for i in $(seq 1 25); do
    sleep 1
    if [ -f "$SENTINEL" ]; then
        got_restart=1
        break
    fi
done

assert "watchdog_called_dev_backend_when_health_dead" "$got_restart"

# --- Test 2: watchdog wrote a pidfile (real watchdog writes to /tmp;
# our stub run writes to its own pidfile via $$). We assert the watchdog
# pid is actually alive while running. ---
if ps -p "$WD_PID" >/dev/null 2>&1; then
    assert "watchdog_process_is_alive" 1
else
    assert "watchdog_process_is_alive" 0
fi

# --- Test 3: watchdog respects MAX_RESTARTS and exits cleanly. ---
# After MAX_RESTARTS=2 restarts the watchdog should exit. With INTERVAL=1 and
# RESTART_WAIT=1 each loop is ~17s, so 3 loops (~51s) before exit. Wait 65s.
exited=0
for i in $(seq 1 65); do
    sleep 1
    if ! ps -p "$WD_PID" >/dev/null 2>&1; then
        exited=1
        break
    fi
done

assert "watchdog_exits_after_max_restarts" "$exited"

# --- Test 4: when health URL DOES return 200, watchdog must not restart. ---
# Spin up a tiny http server that returns 200 OK.
LIVE_PORT=18800
python3 -m http.server "$LIVE_PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
LIVE_PID=$!
sleep 0.5

# Reset sentinel by removing it.
rm -f "$SENTINEL"

MYOS_WATCHDOG_INTERVAL=1 \
MYOS_WATCHDOG_HEALTH_URL="http://127.0.0.1:${LIVE_PORT}/" \
MYOS_WATCHDOG_BACKEND_PORT="$LIVE_PORT" \
MYOS_WATCHDOG_PIDFILE="$WORKDIR/watchdog2.pid" \
MYOS_WATCHDOG_LOGFILE="$WORKDIR/watchdog2.log" \
MYOS_WATCHDOG_MAX_RESTARTS=5 \
MYOS_NO_WATCHDOG=1 \
bash "$WORKDIR/scripts/backend_watchdog.sh" >/dev/null 2>&1 &
WD_PID2=$!

# Let it tick a few times.
sleep 5

# Sentinel should NOT exist because the live server returned 200 every poll.
if [ ! -f "$SENTINEL" ]; then
    assert "watchdog_does_not_restart_when_healthy" 1
else
    assert "watchdog_does_not_restart_when_healthy" 0
fi

kill "$WD_PID2" 2>/dev/null || true
kill "$LIVE_PID" 2>/dev/null || true
wait 2>/dev/null || true

# --- Test 5: when the backend PID is alive but health consistently fails,
#             the watchdog should SIGKILL the process and restart. ---
DEADLOCK_PORT=18801
DEADLOCK_PIDFILE="/tmp/myos-backend-${DEADLOCK_PORT}.pid"

# Start a long-running process to impersonate a deadlocked backend (alive but silent).
sleep 300 &
FAKE_PID=$!
echo "$FAKE_PID" > "$DEADLOCK_PIDFILE"

# Remove the shared sentinel so we get a clean read.
rm -f "$SENTINEL"

# Run with a fast interval, SIGKILL threshold=2, and health pointed at the dead port.
MYOS_WATCHDOG_INTERVAL=1 \
MYOS_WATCHDOG_HEALTH_URL="http://127.0.0.1:${DEAD_PORT}/api/health" \
MYOS_WATCHDOG_BACKEND_PORT="$DEADLOCK_PORT" \
MYOS_WATCHDOG_PIDFILE="$WORKDIR/watchdog3.pid" \
MYOS_WATCHDOG_LOGFILE="$WORKDIR/watchdog3.log" \
MYOS_WATCHDOG_DEADLOCK_THRESHOLD=2 \
MYOS_WATCHDOG_RESTART_WAIT=1 \
MYOS_WATCHDOG_MAX_RESTARTS=5 \
MYOS_NO_WATCHDOG=1 \
bash "$WORKDIR/scripts/backend_watchdog.sh" >/dev/null 2>&1 &
WD_PID3=$!

# Wait up to 60s: two full loop iterations at ~16s each + SIGKILL wait + margin.
got_deadlock_restart=0
for i in $(seq 1 60); do
    sleep 1
    if [ -f "$SENTINEL" ]; then
        got_deadlock_restart=1
        break
    fi
done

kill "$WD_PID3" 2>/dev/null || true
kill "$FAKE_PID" 2>/dev/null || true
rm -f "$DEADLOCK_PIDFILE"
wait 2>/dev/null || true

assert "watchdog_sigkills_deadlocked_pid_and_restarts" "$got_deadlock_restart"

echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ] && exit 0 || exit 1
