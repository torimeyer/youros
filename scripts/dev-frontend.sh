#!/bin/bash
# --- trunk guard: yourOS serves from main. Warn (do not block) if not. ---
_GB="$(git -C "$(dirname "$0")/.." branch --show-current 2>/dev/null)"
if [ -n "$_GB" ] && [ "$_GB" != "main" ]; then
  printf '\033[33mWARNING: serving frontend from branch "%s", not main. Work merged to main will NOT appear here. Run: git checkout main (ALLOW_NONMAIN=1 silences this).\033[0m\n' "$_GB" >&2
  [ -n "$ALLOW_NONMAIN" ] || sleep 2
fi
# --- end trunk guard ---
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
# Fix: skip npm entirely. Run node node_modules/.bin/vite directly
# so there is ONE process; killing it frees the port. Also kill any
# existing listener on 3010 first as a belt-and-braces safeguard for
# the case where an earlier run leaked a zombie.
#
# →2764: THIS SCRIPT OWNS ITS LOGGING. Vite's stdout/stderr always go
# to a persistent, size-capped log file:
#
#     ~/.youros/logs/vite-dev.log        (override: VITE_DEV_LOG)
#
# and never to the caller's stdout. Vite's output is unbounded (proxy
# errors, the browser-console relay). When a caller wired it to a pipe
# nobody drained, the pipe filled and vite's next synchronous write
# blocked the Node event loop forever: port stayed LISTEN, nothing was
# accepted, zero CPU. Writing to a file makes that freeze impossible no
# matter how this script is started. The wrapper's own stdout carries
# only a handful of bounded lines (start banner, "ready on port 3010",
# exit line), which can never fill a pipe.
#
# When run in a terminal, a background `tail -f` of the log restores the
# familiar interactive view; a stuck terminal then stalls only the tail,
# never vite.
#
# NOTE: do NOT use ``exec`` here. When vite.config.ts changes, Vite
# restarts itself internally but the new child process can die if
# there is a momentary compile error or a port-free race. With exec,
# there is no parent to catch that exit. Without exec the shell
# wrapper remains alive and watch-frontend.sh can restart it.
#
# Usage:
#   scripts/dev-frontend.sh         # one shot; vite output -> log file
#   scripts/watch-frontend.sh       # DEFAULT for long-lived serving:
#                                   # restarts on crash AND on wedge (→2726)
#
# Backgrounding this script no longer risks blocking mcp__ostk__bash
# (stdout output is bounded), but the canonical way is still:
#   mcp__ostk__spawn(alias="frontend", cmd="scripts/watch-frontend.sh",
#                    wait_for="ready on port 3010")
# See docs/agents/bash-background-processes.md.
#
# HTTPS cert: if Chrome shows "Not Secure", run once:
#   scripts/setup-localhost-cert.sh
# That script installs a trusted local CA (via mkcert) or trusts the
# existing self-signed cert via the macOS Keychain. No vite changes needed.

set -e
set +m 2>/dev/null  # suppress job-control noise ([N] PID lines)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_DIR="$REPO_DIR/app"
VITE_PORT=3010
VITE_BIN="$APP_DIR/node_modules/.bin/vite"

# →2764 persistent log with a startup size cap. One rotated generation
# (.1) is kept so the tail of a crashed session survives the rotation.
# watch-frontend.sh also truncates the log mid-run if it grows past the
# cap (safe because we open it in append mode below).
VITE_DEV_LOG="${VITE_DEV_LOG:-$HOME/.youros/logs/vite-dev.log}"
VITE_DEV_LOG_MAX_BYTES="${VITE_DEV_LOG_MAX_BYTES:-10485760}"  # 10 MB
mkdir -p "$(dirname "$VITE_DEV_LOG")"
if [ -f "$VITE_DEV_LOG" ]; then
    _log_size=$(stat -f%z "$VITE_DEV_LOG" 2>/dev/null || stat -c%s "$VITE_DEV_LOG" 2>/dev/null || echo 0)
    if [ "${_log_size:-0}" -ge "$VITE_DEV_LOG_MAX_BYTES" ]; then
        mv -f "$VITE_DEV_LOG" "${VITE_DEV_LOG}.1"
    fi
fi

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

# →1798 Nuke the Vite dep-optimization cache before every start so that
# stale chunk hashes (caused by pnpm-lock / package-lock divergence) never
# survive into a new dev session and trigger the Vite 8 blank-page 504.
rm -rf "$APP_DIR/node_modules/.vite"

echo "$(date '+%Y-%m-%d %H:%M:%S') [dev-frontend] starting vite on port $VITE_PORT" >> "$VITE_DEV_LOG"
echo "[dev-frontend] vite output -> $VITE_DEV_LOG"

# Interactive mirror: only when stdout is a real terminal. A blocked
# terminal stalls this tail process, never vite itself.
TAIL_PID=""
if [ -t 1 ]; then
    tail -n 0 -f "$VITE_DEV_LOG" &
    TAIL_PID=$!
fi

# Run (not exec) so the shell wrapper stays alive; a non-zero exit from
# Vite propagates to the caller so watch-frontend.sh can detect the
# crash and re-launch. Node runs in the background with its output on
# the log file (→2764); the wrapper forwards SIGTERM/SIGINT so killing
# the wrapper still stops vite cleanly instead of stranding a zombie
# listener on port 3010.
node "$VITE_BIN" >> "$VITE_DEV_LOG" 2>&1 < /dev/null &
NODE_PID=$!
trap 'kill -TERM "$NODE_PID" 2>/dev/null' TERM INT

# Bounded readiness line for spawn-style callers (wait_for="ready on
# port 3010"). At most one line per script lifetime; best-effort only.
for _i in $(seq 1 60); do
    kill -0 "$NODE_PID" 2>/dev/null || break
    _rc=$(curl -sk -o /dev/null -w '%{http_code}' --connect-timeout 2 -m 3 "https://127.0.0.1:$VITE_PORT/" 2>/dev/null || true)
    case "$_rc" in
        2*|3*) echo "[dev-frontend] ready on port $VITE_PORT"; break ;;
    esac
    sleep 1
done

set +e
wait "$NODE_PID"
VITE_EXIT=$?
if [ "$VITE_EXIT" -gt 128 ]; then
    # wait was interrupted by the trapped signal; wait once more so node
    # is actually reaped (and the port is free) before we return.
    wait "$NODE_PID" 2>/dev/null
    VITE_EXIT=$?
fi
set -e

if [ -n "$TAIL_PID" ]; then
    kill "$TAIL_PID" 2>/dev/null || true
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') [dev-frontend] vite exited code=$VITE_EXIT" >> "$VITE_DEV_LOG"
echo "[dev-frontend] vite exited code=$VITE_EXIT"
exit "$VITE_EXIT"
