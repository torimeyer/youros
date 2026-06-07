#!/usr/bin/env bash
# Stop yourOS cleanly.
#
# Ctrl+C in the terminal that started ./start.sh only kills the shell
# running uvicorn. scripts/dev-backend.sh pairs uvicorn with
# backend_watchdog.sh, and the watchdog traps SIGTERM and re-spawns
# uvicorn, so a naive `kill` won't free port 8000. This script kills
# the watchdog FIRST (with SIGKILL so the trap can't save it), then
# the uvicorn worker, then any Vite dev server.
#
# Usage:
#   ./scripts/stop.sh
#
# Environment:
#   PORT / UVICORN_PORT  backend port (default 8000)
#   VITE_PORT            frontend dev port (default 3010)

set -u

UVICORN_PORT="${PORT:-${UVICORN_PORT:-8000}}"
VITE_PORT="${VITE_PORT:-3010}"

kill_pids() {
    local sig="$1"; shift
    local pids=("$@")
    [ "${#pids[@]}" -eq 0 ] && return 0
    kill "-$sig" "${pids[@]}" 2>/dev/null || true
}

pids_on_port() {
    lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

# On macOS after install, the backend and watchdog are launchd agents
# (com.youros.backend + com.youros.watchdog). KeepAlive=true means a plain
# kill will cause launchd to respawn them immediately. Use launchctl
# bootout first so launchd knows we want them stopped.
if [ "$(uname)" = "Darwin" ]; then
    USER_UID=$(id -u)
    for label in com.youros.watchdog com.youros.backend; do
        if launchctl print "gui/${USER_UID}/${label}" >/dev/null 2>&1; then
            echo "Stopping launchd agent: $label"
            launchctl bootout "gui/${USER_UID}/${label}" 2>/dev/null || true
            sleep 0.3
        fi
    done
fi

# 1. Watchdog (dev mode or non-launchd). It traps SIGTERM and respawns
#    uvicorn, so send SIGKILL. pkill -f matches the full command line.
watchdog_pids=$(pgrep -f 'backend_watchdog\.sh' 2>/dev/null || true)
if [ -n "$watchdog_pids" ]; then
    echo "Stopping backend watchdog: $watchdog_pids"
    # shellcheck disable=SC2086
    kill_pids 9 $watchdog_pids
    sleep 0.3
fi

# 2. Uvicorn on the backend port.
backend_pids=$(pids_on_port "$UVICORN_PORT")
if [ -n "$backend_pids" ]; then
    echo "Stopping backend on port $UVICORN_PORT: $backend_pids"
    # shellcheck disable=SC2086
    kill_pids TERM $backend_pids
    sleep 0.5
    still=$(pids_on_port "$UVICORN_PORT")
    if [ -n "$still" ]; then
        # shellcheck disable=SC2086
        kill_pids 9 $still
        sleep 0.2
    fi
fi

# 3. Vite dev server on the frontend port.
vite_pids=$(pids_on_port "$VITE_PORT")
if [ -n "$vite_pids" ]; then
    echo "Stopping frontend on port $VITE_PORT: $vite_pids"
    # shellcheck disable=SC2086
    kill_pids TERM $vite_pids
    sleep 0.3
    still=$(pids_on_port "$VITE_PORT")
    if [ -n "$still" ]; then
        # shellcheck disable=SC2086
        kill_pids 9 $still
    fi
fi

# 4. Clean up stale pid files so the next start isn't confused.
rm -f "/tmp/youros-backend-${UVICORN_PORT}.pid" 2>/dev/null || true

# 5. Report final state.
if [ -z "$(pids_on_port "$UVICORN_PORT")" ] && \
   [ -z "$(pids_on_port "$VITE_PORT")" ] && \
   [ -z "$(pgrep -f 'backend_watchdog\.sh' 2>/dev/null)" ]; then
    echo "yourOS stopped."
    exit 0
else
    echo "Warning: something is still listening; re-run to retry." >&2
    exit 1
fi
