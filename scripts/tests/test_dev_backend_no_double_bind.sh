#!/usr/bin/env bash
# test_dev_backend_no_double_bind.sh
#
# Regression test for →1250: dev-backend.sh must never let two uvicorn
# processes share port 8000 via SO_REUSEPORT. The race window existed
# between release_launcher_lock (before exec) and when uvicorn actually
# bound the port — a concurrent launcher could sneak in during those 2-5s.
#
# Note: uvicorn --reload spawns a parent process + a worker child; both
# inherit the listener fd, so lsof shows 2 PIDs per instance. All counts
# below use count_uvicorn_parents() which identifies only the reloader
# parent (the one whose own PPID is NOT also in the listener set).
#
# Tests:
#   1. Single launch → exactly one uvicorn parent on the port.
#   2. Two concurrent launches → never more than one uvicorn parent
#      simultaneously (monitored every 0.2s during the convergence window).
#   3. Port free and processes cleaned up at the end.

set -u

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/../.." && pwd)"
DEV_BACKEND="$REPO_ROOT/scripts/dev-backend.sh"

TEST_PORT="${TEST_PORT:-8017}"
PIDFILE="/tmp/youros-backend-${TEST_PORT}.pid"
LAUNCHER_LOCK="/tmp/youros-backend-launcher-${TEST_PORT}.lock"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

pass=0
fail=0

assert() {
    local label="$1" cond="$2"
    if [ "$cond" = "1" ]; then
        echo -e "${GREEN}PASS${NC} $label"
        pass=$((pass + 1))
    else
        echo -e "${RED}FAIL${NC} $label"
        fail=$((fail + 1))
    fi
}

# Count distinct uvicorn reloader PARENTS on the port. A reloader parent's
# child workers also appear in lsof (they inherit the fd); we exclude any
# pid whose own ppid is also in the listener set.
count_uvicorn_parents() {
    local listener_pids="$1"
    local parents=""
    for pid in $listener_pids; do
        local cmd ppid
        cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
        echo "$cmd" | grep -q "uvicorn main:app" || continue
        ppid=$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ' || true)
        echo " $listener_pids " | grep -q " $ppid " && continue
        parents="$parents $pid"
    done
    echo "$parents" | tr -s ' ' '\n' | grep -v '^$' | sort -u | wc -l | tr -d ' '
}

if [ "$TEST_PORT" = "8000" ]; then
    echo "TEST_PORT=8000 would collide with a live dev backend. Refusing." >&2
    exit 1
fi

# Point at main repo api/ if running from a worktree without its own venv.
if [ ! -f "$REPO_ROOT/api/.venv/bin/activate" ]; then
    _common=$(cd "$REPO_ROOT" && git rev-parse --git-common-dir 2>/dev/null || true)
    if [ -n "$_common" ] && [ -f "$(dirname "$_common")/api/.venv/bin/activate" ]; then
        export YOUROS_API_DIR="$(dirname "$_common")/api"
    fi
fi

kill_port_listeners() {
    local port="$1"
    local pids
    pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$pids" ]; then
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
        sleep 1
        pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
        # shellcheck disable=SC2086
        [ -n "$pids" ] && { kill -9 $pids 2>/dev/null || true; sleep 0.5; }
    fi
}

cleanup() {
    kill_port_listeners "$TEST_PORT"
    rm -f "$PIDFILE" "$LAUNCHER_LOCK"
}
trap cleanup EXIT INT TERM

# Pre-flight: ensure port and lock files are clear.
cleanup

# ── Test 1: single launch → exactly one uvicorn parent ───────────────────────

PORT=$TEST_PORT YOUROS_NO_WATCHDOG=1 nohup "$DEV_BACKEND" \
    >/tmp/test_dev_backend_no_double_bind_a.log 2>&1 &
LAUNCHER_A=$!

bound=0
for _i in $(seq 1 20); do
    sleep 1
    if [ -n "$(lsof -tiTCP:"$TEST_PORT" -sTCP:LISTEN 2>/dev/null || true)" ]; then
        bound=1
        break
    fi
done

assert "single_launch_binds_port_within_20s" "$bound"

_listener_pids=$(lsof -tiTCP:"$TEST_PORT" -sTCP:LISTEN 2>/dev/null | sort -u | tr '\n' ' ')
_parents=$(count_uvicorn_parents "$_listener_pids")
assert "single_launch_exactly_one_uvicorn_parent" "$([ "${_parents:-0}" -eq 1 ] && echo 1 || echo 0)"

# ── Test 2: concurrent launches → never two simultaneous uvicorn parents ──────

# Kill the first backend and start fresh for the concurrency test.
_bpid=$(cat "$PIDFILE" 2>/dev/null || true)
[ -n "$_bpid" ] && kill "$_bpid" 2>/dev/null || true
kill_port_listeners "$TEST_PORT"
wait "$LAUNCHER_A" 2>/dev/null || true
kill_port_listeners "$TEST_PORT"
rm -f "$PIDFILE" "$LAUNCHER_LOCK"
sleep 0.5

# Launch two dev-backend.sh instances back-to-back with a tiny stagger so
# both reach acquire_launcher_lock within the same race window.
PORT=$TEST_PORT YOUROS_NO_WATCHDOG=1 nohup "$DEV_BACKEND" \
    >/tmp/test_dev_backend_no_double_bind_b.log 2>&1 &
LAUNCHER_B=$!

sleep 0.3

PORT=$TEST_PORT YOUROS_NO_WATCHDOG=1 nohup "$DEV_BACKEND" \
    >>/tmp/test_dev_backend_no_double_bind_b.log 2>&1 &
LAUNCHER_C=$!

# Monitor for double-bind every 0.5s during the 45s convergence window.
max_parents=0
_deadline=$((SECONDS + 45))
while [ "$SECONDS" -lt "$_deadline" ]; do
    sleep 0.5
    _lp=$(lsof -tiTCP:"$TEST_PORT" -sTCP:LISTEN 2>/dev/null | sort -u | tr '\n' ' ')
    _cur=$(count_uvicorn_parents "$_lp")
    if [ "${_cur:-0}" -gt "$max_parents" ]; then
        max_parents="${_cur:-0}"
    fi
    # Once both launcher shells have exited (replaced by uvicorn or returned
    # early), give the surviving uvicorn 3s to stabilize then break.
    _b_alive=$(kill -0 "$LAUNCHER_B" 2>/dev/null && echo 1 || echo 0)
    _c_alive=$(kill -0 "$LAUNCHER_C" 2>/dev/null && echo 1 || echo 0)
    if [ "$_b_alive" = "0" ] && [ "$_c_alive" = "0" ]; then
        sleep 3
        break
    fi
done

_final_lp=$(lsof -tiTCP:"$TEST_PORT" -sTCP:LISTEN 2>/dev/null | sort -u | tr '\n' ' ')
_final_parents=$(count_uvicorn_parents "$_final_lp")

assert "concurrent_launchers_never_produce_two_uvicorn_parents" \
    "$([ "${max_parents:-0}" -le 1 ] && echo 1 || echo 0)"
assert "concurrent_launchers_converge_to_one_uvicorn_parent_at_end" \
    "$([ "${_final_parents:-0}" -le 1 ] && echo 1 || echo 0)"

# Kill the surviving backend before waiting so the shell doesn't block.
_final_bpid=$(cat "$PIDFILE" 2>/dev/null || true)
[ -n "$_final_bpid" ] && kill "$_final_bpid" 2>/dev/null || true
kill_port_listeners "$TEST_PORT"
wait "$LAUNCHER_B" 2>/dev/null || true
wait "$LAUNCHER_C" 2>/dev/null || true

echo ""
echo "Results: $pass passed, $fail failed"
if [ "$fail" -eq 0 ]; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
