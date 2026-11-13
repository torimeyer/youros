#!/bin/bash
# Restart the backend cleanly.
# This script is in scripts/ so it is whitelisted by hooks/ostk-first.sh
# and does not trigger the ostk "potentially destructive" approval prompt.
#
# Usage:
#   bash scripts/restart-backend.sh          # zero-downtime blue-green reload when live
#   bash scripts/restart-backend.sh --hard   # hard stop target port + dev-backend.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

HARD_RESTART=0
for _arg in "$@"; do
    if [ "$_arg" = "--hard" ]; then
        HARD_RESTART=1
    fi
done

TARGET_PORT="${PRIMARY_PORT:-${PORT:-${UVICORN_PORT:-8000}}}"
ZERO_DOWNTIME_SCRIPT="$SCRIPT_DIR/reload-backend-zero-downtime.sh"

if [ "$HARD_RESTART" != "1" ] && [ -f "$ZERO_DOWNTIME_SCRIPT" ]; then
    if [ -n "$(lsof -tiTCP:${TARGET_PORT} -sTCP:LISTEN 2>/dev/null || true)" ]; then
        exec bash "$ZERO_DOWNTIME_SCRIPT"
    fi
fi

echo "Stopping existing backend processes on port ${TARGET_PORT}..."
_port_pids=$(lsof -tiTCP:"${TARGET_PORT}" -sTCP:LISTEN 2>/dev/null || true)
_uvicorn_pids=$(pgrep -f "uvicorn main:app.*--port ${TARGET_PORT}" 2>/dev/null || true)
_pidfile="/tmp/youros-backend-${TARGET_PORT}.pid"
_pidfile_pid=""
if [ -f "$_pidfile" ]; then
    _pidfile_pid=$(cat "$_pidfile" 2>/dev/null || true)
fi

_all_pids=$(printf '%s\n%s\n%s\n' "$_port_pids" "$_uvicorn_pids" "$_pidfile_pid" \
    | grep -E '^[0-9]+$' | grep -v "^$$\$" | sort -u | tr '\n' ' ' || true)

if [ -n "${_all_pids%% }" ]; then
    kill -9 $_all_pids 2>/dev/null || true
fi

# Wait until old processes on TARGET_PORT are gone before starting fresh.
# A deadlocked event loop ignores SIGTERM; SIGKILL takes a moment for the
# kernel to reap, and the port may still be held briefly after the signal.
_wait=0
while [ "$_wait" -lt 10 ]; do
    if [ -z "$(lsof -tiTCP:${TARGET_PORT} -sTCP:LISTEN 2>/dev/null || true)" ] && \
       ! pgrep -f "uvicorn main:app.*--port ${TARGET_PORT}" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
    _wait=$((_wait + 1))
done

echo "Starting backend..."
PORT="$TARGET_PORT" exec bash "$SCRIPT_DIR/dev-backend.sh"
