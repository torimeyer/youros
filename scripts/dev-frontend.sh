#!/bin/bash
# Dev-only helper to start the Vite frontend WITHOUT going through npm.
#
# The problem this solves: ``npm run dev`` forks a child Node process
# that actually runs Vite. When the ``npm`` parent is killed (e.g. by
# ostk interact kill), npm does not forward the signal to its child,
# so the child survives and keeps port 3010's TCP listener open as a
# zombie. The next attempt to start Vite silently falls back to port
# 3011 while the browser stays on 3010 talking to the zombie. Every
# request hangs. Needle 287.
#
# Fix: skip npm entirely. exec node node_modules/.bin/vite directly
# so there is ONE process; killing it frees the port. Also kill any
# existing listener on 3010 first as a belt-and-braces safeguard for
# the case where an earlier run leaked a zombie.
#
# Usage:
#   scripts/dev-frontend.sh         # starts Vite on port 3010

set -e
set +m 2>/dev/null  # suppress job-control noise ([N] PID lines)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_DIR="$REPO_DIR/app"
VITE_PORT=3010
VITE_BIN="$APP_DIR/node_modules/.bin/vite"

if [ ! -x "$VITE_BIN" ]; then
    echo "Vite binary not found at $VITE_BIN. Run 'npm install' in $APP_DIR first." >&2
    exit 1
fi

# Belt and braces: free port 3010 if anything is already listening on
# it. This is the zombie case that needle 287 fixes. We only kill
# processes we own (SIGTERM first, then SIGKILL if needed).
stale_pids=$(lsof -tiTCP:$VITE_PORT -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$stale_pids" ]; then
    echo "Freeing port $VITE_PORT from stale listener(s): $stale_pids"
    kill $stale_pids 2>/dev/null || true
    sleep 1
    still=$(lsof -tiTCP:$VITE_PORT -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$still" ]; then
        kill -9 $still 2>/dev/null || true
        sleep 1
    fi
fi

cd "$APP_DIR"
# exec replaces this shell with the Vite Node process. Only ONE
# process in the tree = interact kill frees the port every time.
exec node "$VITE_BIN"
