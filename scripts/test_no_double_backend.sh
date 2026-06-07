#!/usr/bin/env bash
# test_no_double_backend.sh
#
# Regression guard for the demo-fragility race described in diagnose
# "backend double-spawn race". Before the fix, two parallel invocations
# of scripts/dev-backend.sh could briefly result in two uvicorn
# processes binding the same port (via SO_REUSEADDR), producing empty
# responses until one lost the race.
#
# This script verifies, on a non-production port:
#   1. dev-backend.sh starts a single uvicorn reloader parent and no
#      orphaned children survive after the parent dies.
#   2. A concurrent second invocation of dev-backend.sh waits for the
#      in-flight launcher lock rather than racing into exec uvicorn.
#   3. backend_watchdog.sh, when dev-backend.sh is still alive but the
#      health probe misses, refuses to spawn a second dev-backend.
#
# Exit 0 = all assertions passed. Exit 1 = a test failed.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEV_BACKEND="$SCRIPT_DIR/dev-backend.sh"
WATCHDOG="$SCRIPT_DIR/backend_watchdog.sh"

# dev-backend.sh needs a working api/.venv. If this test runs from a git
# worktree (no venv of its own) point at the main repo's api directory.
# The main repo is always a sibling of .claude/worktrees/<name> via
# .git/worktrees, so we locate it through `git rev-parse --git-common-dir`.
if [ ! -f "$REPO_DIR/api/.venv/bin/activate" ]; then
    COMMON_GIT=$(cd "$REPO_DIR" && git rev-parse --git-common-dir 2>/dev/null || true)
    if [ -n "$COMMON_GIT" ] && [ -f "$(dirname "$COMMON_GIT")/api/.venv/bin/activate" ]; then
        export YOUROS_API_DIR="$(dirname "$COMMON_GIT")/api"
    fi
fi

TEST_PORT="${TEST_PORT:-8002}"
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
        pass=$((pass+1))
    else
        echo -e "${RED}FAIL${NC} $label"
        fail=$((fail+1))
    fi
}

# Guardrails: if TEST_PORT is 8000 the operator made a mistake. Refuse.
if [ "$TEST_PORT" = "8000" ]; then
    echo "TEST_PORT=8000 would interfere with a live dev backend. Refusing." >&2
    exit 1
fi

kill_port_listeners() {
    local port="$1"
    local pids
    pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$pids" ]; then
        kill $pids 2>/dev/null || true
        sleep 1
        pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
        if [ -n "$pids" ]; then
            kill -9 $pids 2>/dev/null || true
            sleep 0.5
        fi
    fi
}

cleanup() {
    kill_port_listeners "$TEST_PORT"
    rm -f "$PIDFILE" "$LAUNCHER_LOCK"
}
trap cleanup EXIT INT TERM

# Pre-flight: ensure test port is free, no stale pidfile/lock.
cleanup

# --- Test 1: dev-backend.sh starts exactly one uvicorn parent ---
PORT=$TEST_PORT YOUROS_NO_WATCHDOG=1 nohup "$DEV_BACKEND" > /tmp/test_no_double_backend.log 2>&1 &
LAUNCHER_PID=$!

# Wait up to 20s for uvicorn to bind the port.
bound=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    sleep 1
    if [ -n "$(lsof -tiTCP:"$TEST_PORT" -sTCP:LISTEN 2>/dev/null || true)" ]; then
        bound=1
        break
    fi
done

assert "uvicorn_binds_test_port_within_20s" "$bound"

# Record the parent PID (may be the launcher shell PID since exec
# replaces it with uvicorn).
backend_pid=$(cat "$PIDFILE" 2>/dev/null || true)
if [ -n "$backend_pid" ] && kill -0 "$backend_pid" 2>/dev/null; then
    assert "backend_pidfile_points_to_live_pid" 1
else
    assert "backend_pidfile_points_to_live_pid" 0
fi

# --- Test 2: launcher lock is released after exec (so a future
# legitimate re-run is not blocked indefinitely). ---
if [ ! -f "$LAUNCHER_LOCK" ]; then
    assert "launcher_lock_released_after_exec" 1
else
    lock_contents=$(cat "$LAUNCHER_LOCK" 2>/dev/null || true)
    echo "  (launcher lock still present with contents: $lock_contents)"
    assert "launcher_lock_released_after_exec" 0
fi

# --- Test 3: kill parent, confirm no orphan child remains bound. ---
if [ -n "$backend_pid" ]; then
    kill "$backend_pid" 2>/dev/null || true
fi
# Allow up to 5 seconds for graceful shutdown.
orphan=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.5
    if [ -z "$(lsof -tiTCP:"$TEST_PORT" -sTCP:LISTEN 2>/dev/null || true)" ]; then
        orphan=0
        break
    fi
    orphan=1
done
if [ "$orphan" = "0" ]; then
    assert "no_orphan_child_after_parent_kill" 1
else
    echo "  (listener pids still present: $(lsof -tiTCP:"$TEST_PORT" -sTCP:LISTEN 2>/dev/null))"
    assert "no_orphan_child_after_parent_kill" 0
fi

# Wait for launcher shell to fully exit so the next test has a clean slate.
wait "$LAUNCHER_PID" 2>/dev/null || true
rm -f "$PIDFILE"

# --- Test 4: concurrent invocations serialize via the launcher lock ---
# Start two dev-backend.sh launchers back-to-back. The second must wait
# for the first; both invocations must converge on exactly one uvicorn
# reloader parent (the second replaces the first).
PORT=$TEST_PORT YOUROS_NO_WATCHDOG=1 nohup "$DEV_BACKEND" >> /tmp/test_no_double_backend.log 2>&1 &
LAUNCHER_A=$!
# Tiny stagger so A can claim the lock before B enters acquire_launcher_lock.
sleep 0.2
PORT=$TEST_PORT YOUROS_NO_WATCHDOG=1 nohup "$DEV_BACKEND" >> /tmp/test_no_double_backend.log 2>&1 &
LAUNCHER_B=$!

# Wait up to 30s for convergence. The second launcher must wait up to
# 15s for the first to release the lock, then itself kill and replace
# the running uvicorn, which takes another ~5s to boot.
# A reloader parent's child workers also show up in lsof (they share the
# inherited listener fd). We count distinct "uvicorn main:app" processes
# whose parent PID is NOT itself in the listener-pid set; that excludes
# reload worker children and multiprocessing resource trackers.
count_uvicorn_parents() {
    local listener_pids="$1"
    local parents=""
    for pid in $listener_pids; do
        cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
        if ! echo "$cmd" | grep -q "uvicorn main:app"; then
            continue
        fi
        ppid=$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ' || true)
        # Parent PID is in the listener set means this is a child worker
        # (its ppid is the reloader parent); skip.
        if echo " $listener_pids " | grep -q " $ppid "; then
            continue
        fi
        parents="$parents $pid"
    done
    echo "$parents" | tr -s ' ' '\n' | grep -v '^$' | sort -u | wc -l | tr -d ' '
}

uvicorn_parents=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
    sleep 1
    listener_pids=$(lsof -tiTCP:"$TEST_PORT" -sTCP:LISTEN 2>/dev/null | sort -u | tr '\n' ' ')
    if [ -z "$(echo "$listener_pids" | tr -d ' ')" ]; then
        continue
    fi
    uvicorn_parents=$(count_uvicorn_parents "$listener_pids")
    if [ "$uvicorn_parents" -ge "1" ]; then
        # Stabilize: let any racing exec finish, then re-count.
        sleep 3
        listener_pids=$(lsof -tiTCP:"$TEST_PORT" -sTCP:LISTEN 2>/dev/null | sort -u | tr '\n' ' ')
        uvicorn_parents=$(count_uvicorn_parents "$listener_pids")
        break
    fi
done

if [ "$uvicorn_parents" = "1" ]; then
    assert "concurrent_launchers_converge_to_one_uvicorn" 1
else
    echo "  (expected exactly 1 uvicorn parent, got $uvicorn_parents)"
    lsof -iTCP:"$TEST_PORT" 2>/dev/null | head -10
    assert "concurrent_launchers_converge_to_one_uvicorn" 0
fi

# Clean up the backend before the watchdog test.
backend_pid=$(cat "$PIDFILE" 2>/dev/null || true)
if [ -n "$backend_pid" ]; then
    kill "$backend_pid" 2>/dev/null || true
fi
kill_port_listeners "$TEST_PORT"
rm -f "$PIDFILE" "$LAUNCHER_LOCK"

# --- Test 5: watchdog skips restart when backend pid is alive but
# health URL is unreachable (the common false-alarm during reloads). ---
# Use a sleep-forever subshell so we have a guaranteed-live pid that
# does not collide with the test runner's own $$. Point BACKEND_PIDFILE
# at that pid, then run the watchdog's core decision loop once.
( sleep 120 ) &
DUMMY_PID=$!
echo "$DUMMY_PID" > "$PIDFILE"

# Point at a dead health URL. backend_pid_alive() should short-circuit
# and emit the "still alive" skip-log line instead of relaunching.
REAL_WD_LOG="/tmp/youros-backend-watchdog.log"
rm -f "$REAL_WD_LOG"
YOUROS_WATCHDOG_INTERVAL=1 \
YOUROS_WATCHDOG_HEALTH_URL="http://127.0.0.1:18797/api/health" \
YOUROS_WATCHDOG_MAX_RESTARTS=1 \
YOUROS_WATCHDOG_BACKEND_PORT="$TEST_PORT" \
YOUROS_NO_WATCHDOG=1 \
bash "$WATCHDOG" > /dev/null 2>&1 &
WD_PID=$!

# initial sleep=INTERVAL (1s) + 3 retries (5s each) = ~16s before the
# first restart decision fires.
sleep 20

# Stop the watchdog so it doesn't loop forever.
kill "$WD_PID" 2>/dev/null || true
wait "$WD_PID" 2>/dev/null || true
kill "$DUMMY_PID" 2>/dev/null || true

if [ -f "$REAL_WD_LOG" ] && grep -q "still alive; skipping restart" "$REAL_WD_LOG"; then
    assert "watchdog_skips_restart_when_backend_pid_alive" 1
else
    echo "  (watchdog log tail:)"
    tail -10 "$REAL_WD_LOG" 2>/dev/null | sed 's/^/    /'
    assert "watchdog_skips_restart_when_backend_pid_alive" 0
fi

echo ""
echo "Results: $pass passed, $fail failed"
if [ "$fail" -eq 0 ]; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
