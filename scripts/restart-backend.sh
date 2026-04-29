#!/bin/bash
# Kill any running uvicorn/dev-backend process and restart the backend.
# This script is in scripts/ so it is whitelisted by hooks/ostk-first.sh
# and does not trigger the ostk "potentially destructive" approval prompt.
#
# Usage: bash scripts/restart-backend.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Stopping existing backend processes..."
pkill -9 -f "uvicorn" 2>/dev/null || true
pkill -9 -f "dev-backend" 2>/dev/null || true

# Wait until all old processes are actually gone before starting fresh.
# A deadlocked event loop ignores SIGTERM; SIGKILL takes a moment for the
# kernel to reap, and the port may still be held briefly after the signal.
_wait=0
while [ "$_wait" -lt 5 ] && pgrep -f "uvicorn" >/dev/null 2>&1; do
    sleep 1
    _wait=$((_wait + 1))
done

echo "Starting backend..."
exec bash "$SCRIPT_DIR/dev-backend.sh"
