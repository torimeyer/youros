#!/usr/bin/env bash
# Demo safety net: run dev-frontend.sh in a restart loop.
#
# Vite can die when vite.config.ts changes trigger an internal restart
# that hits a compile error or port-free race. This wrapper catches the
# exit and re-launches within 1 second so the frontend is never down
# for more than a few seconds during a demo.
#
# A clean SIGTERM / SIGINT (e.g. from `ostk interact kill` or Ctrl-C)
# kills the child and exits without restarting.
#
# Usage:
#   scripts/watch-frontend.sh          # foreground (Ctrl-C to stop)
#   nohup scripts/watch-frontend.sh >> /tmp/torios-frontend.log 2>&1 &
#
# Watcher state is appended to /tmp/torios-frontend-watcher.log.
# Vite stdout/stderr go to /tmp/torios-frontend.log (or this script's stdout).
#
# See also:
#   scripts/dev-frontend.sh    # one-shot launcher (called by this script)
#   scripts/health.sh --fix    # auto-start this watcher when frontend is down

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCH_LOG="/tmp/torios-frontend-watcher.log"
VITE_LOG="/tmp/torios-frontend.log"

CHILD_PID=""
STOP_REQUESTED=0

cleanup() {
    STOP_REQUESTED=1
    if [ -n "$CHILD_PID" ] && kill -0 "$CHILD_PID" 2>/dev/null; then
        kill "$CHILD_PID" 2>/dev/null || true
    fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') [watcher] stopped by signal" >> "$WATCH_LOG"
    exit 0
}
trap cleanup SIGTERM SIGINT

echo "$(date '+%Y-%m-%d %H:%M:%S') [watcher] start" >> "$WATCH_LOG"

ATTEMPT=0
while true; do
    if [ "$STOP_REQUESTED" = "1" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [watcher] stop flag set, exiting loop" >> "$WATCH_LOG"
        exit 0
    fi

    ATTEMPT=$(( ATTEMPT + 1 ))
    echo "$(date '+%Y-%m-%d %H:%M:%S') [watcher] attempt $ATTEMPT: launching vite" >> "$WATCH_LOG"

    "$SCRIPT_DIR/dev-frontend.sh" >> "$VITE_LOG" 2>&1 &
    CHILD_PID=$!
    wait "$CHILD_PID"
    EXIT_CODE=$?

    if [ "$STOP_REQUESTED" = "1" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [watcher] clean stop, not restarting" >> "$WATCH_LOG"
        exit 0
    fi

    echo "$(date '+%Y-%m-%d %H:%M:%S') [watcher] vite exited code=$EXIT_CODE; restarting in 1s" >> "$WATCH_LOG"
    echo "[watch-frontend] vite exited (code $EXIT_CODE), restarting..."
    sleep 1
done
