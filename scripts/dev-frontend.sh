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
# Fix: skip npm entirely. Run node node_modules/.bin/vite directly
# so there is ONE process; killing it frees the port. Also kill any
# existing listener on 3010 first as a belt-and-braces safeguard for
# the case where an earlier run leaked a zombie.
#
# NOTE: do NOT use ``exec`` here. When vite.config.ts changes, Vite
# restarts itself internally but the new child process can die if
# there is a momentary compile error or a port-free race. With exec,
# there is no parent to catch that exit. Without exec the shell
# wrapper remains alive and watch-frontend.sh can restart it.
#
# Usage:
#   scripts/dev-frontend.sh         # starts Vite on port 3010 (one shot)
#
# IMPORTANT: when backgrounding this script from mcp__ostk__bash, always redirect
# stdout and stderr, or the tool call will block until Vite is killed:
#   nohup scripts/dev-frontend.sh > /tmp/dev-frontend.log 2>#   scripts/dev-frontend.sh         # starts Vite on port 3010 (one shot)1 < /dev/null & disown
# See docs/agents/bash-background-processes.md. Prefer mcp__ostk__spawn instead.
#   scripts/watch-frontend.sh       # resilient loop (use for demos)
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
# Run (not exec) so the shell wrapper stays alive. A non-zero exit from
# Vite (e.g. after a failed config-change restart) propagates to the
# caller, letting watch-frontend.sh detect the crash and re-launch.
node "$VITE_BIN"
