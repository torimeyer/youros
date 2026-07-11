#!/usr/bin/env bash
# Resilient runner for the dev frontend. This is THE DEFAULT way to serve
# the frontend for anything long-lived (demos, agent browser tests,
# leaving it running between sessions). →2726
#
# Two failure classes are covered:
#
#   1. Crash: vite exits (config-reload race, compile error, OOM kill).
#      The loop relaunches it within RESTART_DELAY seconds.
#
#   2. Wedge (→2764): the process is alive and the port is LISTENing but
#      the Node event loop is blocked, so nothing is ever accepted and
#      CPU sits at zero. A restart-on-exit watcher can never catch this,
#      because nothing exits. So while the child is alive the loop also
#      probes the port over HTTPS:
#        - at startup: every second until the first answer, giving up
#          (and force-restarting) after PROBE_STARTUP_TIMEOUT seconds
#        - steady state: every PROBE_INTERVAL seconds, force-restarting
#          after PROBE_MAX_FAILS consecutive failures
#
# Vite's own output goes to ~/.youros/logs/vite-dev.log; that redirect is
# owned by dev-frontend.sh (→2764) so no caller can wire vite's unbounded
# output to a blockable pipe. Watcher state is appended to
# ~/.youros/logs/frontend-watcher.log. This script writes to stdout only
# a strictly bounded number of lines, so backgrounding it without
# redirects can never wedge the caller either.
#
# The →287 zombie-port cleanup lives in dev-frontend.sh and runs before
# every (re)launch, so a force-killed wedged vite never strands port 3010.
#
# A clean SIGTERM / SIGINT kills the child tree and exits without
# restarting.
#
# Usage:
#   scripts/watch-frontend.sh          # foreground (Ctrl-C to stop)
#   nohup scripts/watch-frontend.sh > /dev/null 2>&1 < /dev/null & disown
#   mcp__ostk__spawn(alias="frontend", cmd="scripts/watch-frontend.sh",
#                    wait_for="ready on port 3010")
#
# See also:
#   scripts/dev-frontend.sh    # one-shot launcher (called by this script)
#   scripts/health.sh --fix    # auto-start this watcher when frontend is down

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LOG_DIR="$HOME/.youros/logs"
mkdir -p "$LOG_DIR"
WATCH_LOG="${WATCH_LOG:-$LOG_DIR/frontend-watcher.log}"
VITE_LOG="${VITE_DEV_LOG:-$LOG_DIR/vite-dev.log}"
VITE_DEV_LOG_MAX_BYTES="${VITE_DEV_LOG_MAX_BYTES:-10485760}"  # 10 MB

FRONTEND_URL="${FRONTEND_URL:-https://127.0.0.1:3010/}"
PROBE_INTERVAL="${PROBE_INTERVAL:-30}"                    # seconds between steady-state probes
PROBE_MAX_FAILS="${PROBE_MAX_FAILS:-3}"                   # consecutive failures before restart
PROBE_STARTUP_TIMEOUT="${PROBE_STARTUP_TIMEOUT:-60}"      # seconds to first answer
RESTART_DELAY="${RESTART_DELAY:-1}"
# Test hooks: WATCH_MAX_ATTEMPTS > 0 exits after N launches so a harness
# can run one bounded iteration; FRONTEND_CMD swaps in a stub launcher.
WATCH_MAX_ATTEMPTS="${WATCH_MAX_ATTEMPTS:-0}"
FRONTEND_CMD="${FRONTEND_CMD:-$SCRIPT_DIR/dev-frontend.sh}"

CHILD_PID=""
STOP_REQUESTED=0

wlog() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [watcher] $*" >> "$WATCH_LOG"
}

probe_once() {
    local code
    code=$(curl -sk -o /dev/null -w '%{http_code}' --connect-timeout 2 -m 3 "$FRONTEND_URL" 2>/dev/null || true)
    case "$code" in
        2*|3*) return 0 ;;
    esac
    return 1
}

kill_child_tree() {
    # dev-frontend.sh is a shell wrapper whose direct child is node/vite.
    # Kill the wrapper's children first, then the wrapper. A wedged node
    # cannot run its graceful SIGTERM handler (the event loop is blocked),
    # so escalate to SIGKILL, which the kernel enforces regardless. The
    # →287 port cleanup in dev-frontend.sh is the final safety net for
    # anything that survives.
    [ -n "$CHILD_PID" ] || return 0
    kill -0 "$CHILD_PID" 2>/dev/null || return 0
    pkill -TERM -P "$CHILD_PID" 2>/dev/null || true
    kill -TERM "$CHILD_PID" 2>/dev/null || true
    for _ in 1 2 3 4; do
        kill -0 "$CHILD_PID" 2>/dev/null || return 0
        sleep 0.5
    done
    pkill -KILL -P "$CHILD_PID" 2>/dev/null || true
    kill -KILL "$CHILD_PID" 2>/dev/null || true
}

rotate_vite_log_if_needed() {
    # →2764 cap the shared vite log mid-run. copy-then-truncate is safe
    # because dev-frontend.sh opens the log in append mode (O_APPEND),
    # so writes continue at the new end after truncation.
    local size
    size=$(stat -f%z "$VITE_LOG" 2>/dev/null || stat -c%s "$VITE_LOG" 2>/dev/null || echo 0)
    if [ "${size:-0}" -ge "$VITE_DEV_LOG_MAX_BYTES" ]; then
        cp -f "$VITE_LOG" "${VITE_LOG}.1" 2>/dev/null || true
        : > "$VITE_LOG"
        wlog "rotated $VITE_LOG ($size bytes >= cap)"
    fi
}

cleanup() {
    STOP_REQUESTED=1
    kill_child_tree
    wlog "stopped by signal"
    exit 0
}
trap cleanup SIGTERM SIGINT

wlog "start (startup timeout ${PROBE_STARTUP_TIMEOUT}s, steady probe every ${PROBE_INTERVAL}s, restart after ${PROBE_MAX_FAILS} consecutive probe failures)"

ATTEMPT=0
while true; do
    if [ "$STOP_REQUESTED" = "1" ]; then
        wlog "stop flag set, exiting loop"
        exit 0
    fi

    ATTEMPT=$(( ATTEMPT + 1 ))
    wlog "attempt $ATTEMPT: launching vite"

    "$FRONTEND_CMD" >> "$VITE_LOG" 2>&1 < /dev/null &
    CHILD_PID=$!

    # --- Phase A: startup. Wait for the port to answer (1s cadence). ---
    READY=0
    ELAPSED=0
    while kill -0 "$CHILD_PID" 2>/dev/null; do
        [ "$STOP_REQUESTED" = "1" ] && break
        if probe_once; then
            READY=1
            wlog "attempt $ATTEMPT: frontend answering on $FRONTEND_URL"
            if [ "$ATTEMPT" -le 5 ]; then
                # Bounded stdout (→2764): at most 5 of these lines ever,
                # so they can never fill an undrained pipe. Lets
                # spawn-style callers use wait_for="ready on port 3010".
                echo "[watch-frontend] ready on port 3010"
            fi
            break
        fi
        ELAPSED=$(( ELAPSED + 1 ))
        if [ "$ELAPSED" -ge "$PROBE_STARTUP_TIMEOUT" ]; then
            wlog "attempt $ATTEMPT: no answer after ${PROBE_STARTUP_TIMEOUT}s with pid $CHILD_PID alive: wedged at startup (→2764). Force-restarting."
            kill_child_tree
            break
        fi
        sleep 1
    done

    # --- Phase B: steady state. `wait` alone can never catch a wedged-
    # but-alive process (→2726), so probe the port on a fixed cadence and
    # force-restart after PROBE_MAX_FAILS consecutive failures. ---
    FAILS=0
    TICK=0
    while [ "$READY" = "1" ] && kill -0 "$CHILD_PID" 2>/dev/null; do
        [ "$STOP_REQUESTED" = "1" ] && break
        sleep 1
        TICK=$(( TICK + 1 ))
        if [ "$TICK" -lt "$PROBE_INTERVAL" ]; then
            continue
        fi
        TICK=0
        rotate_vite_log_if_needed
        if probe_once; then
            if [ "$FAILS" -gt 0 ]; then
                wlog "probe recovered after $FAILS failure(s)"
            fi
            FAILS=0
        else
            FAILS=$(( FAILS + 1 ))
            wlog "probe failed ($FAILS/$PROBE_MAX_FAILS) with pid $CHILD_PID alive"
            if [ "$FAILS" -ge "$PROBE_MAX_FAILS" ]; then
                wlog "process alive but port dead: wedged (→2764). Force-restarting."
                kill_child_tree
                break
            fi
        fi
    done

    wait "$CHILD_PID" 2>/dev/null
    EXIT_CODE=$?
    CHILD_PID=""

    if [ "$STOP_REQUESTED" = "1" ]; then
        wlog "clean stop, not restarting"
        exit 0
    fi

    wlog "vite exited code=$EXIT_CODE; restarting in ${RESTART_DELAY}s"
    if [ -t 1 ]; then
        echo "[watch-frontend] vite exited (code $EXIT_CODE), restarting..."
    fi

    if [ "$WATCH_MAX_ATTEMPTS" -gt 0 ] && [ "$ATTEMPT" -ge "$WATCH_MAX_ATTEMPTS" ]; then
        wlog "max attempts ($WATCH_MAX_ATTEMPTS) reached, exiting (test hook)"
        exit 0
    fi

    sleep "$RESTART_DELAY"
done
