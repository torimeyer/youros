#!/usr/bin/env bash
# backend_watchdog.sh
#
# Auto-recovery sibling for scripts/dev-backend.sh. Polls /api/health every
# 30 seconds. If two consecutive probes fail (no 200 within 5 seconds each),
# logs a WARNING and re-launches dev-backend.sh in the background so the
# demo never sits with a dead backend.
#
# This script writes its pid to /tmp/myos-backend-watchdog.pid so a second
# launch from dev-backend.sh becomes a no-op. Logs to /tmp/myos-backend-watchdog.log.
#
# Stop manually with: kill $(cat /tmp/myos-backend-watchdog.pid)
#
# Disable via: MYOS_NO_WATCHDOG=1 scripts/dev-backend.sh
#
# Tunables (env vars):
#   MYOS_WATCHDOG_INTERVAL   seconds between probes (default 30)
#   MYOS_WATCHDOG_HEALTH_URL full URL to probe (default https://127.0.0.1:8000/api/health)
#   MYOS_WATCHDOG_MAX_RESTARTS hard cap on restarts in one process lifetime
#                              (default 50, prevents infinite loops if the
#                              backend can never come up)

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEV_BACKEND="$SCRIPT_DIR/dev-backend.sh"
PIDFILE="/tmp/myos-backend-watchdog.pid"
LOGFILE="/tmp/myos-backend-watchdog.log"
INTERVAL="${MYOS_WATCHDOG_INTERVAL:-30}"
HEALTH_URL="${MYOS_WATCHDOG_HEALTH_URL:-https://127.0.0.1:8000/api/health}"
MAX_RESTARTS="${MYOS_WATCHDOG_MAX_RESTARTS:-50}"

# Port the backend listens on. The watchdog reads the matching pidfile
# (/tmp/myos-backend-<port>.pid) to verify whether a backend process is
# actually running before attempting a restart. Default 8000; tests pass
# a different port via env var so they don't collide with a live dev
# backend on :8000.
BACKEND_PORT="${MYOS_WATCHDOG_BACKEND_PORT:-8000}"
BACKEND_PIDFILE="/tmp/myos-backend-${BACKEND_PORT}.pid"
LAUNCHER_LOCK="/tmp/myos-backend-launcher-${BACKEND_PORT}.lock"

# Always keep the freshest pid in the pidfile, even when invoked directly.
echo $$ > "$PIDFILE"

cleanup() {
    rm -f "$PIDFILE" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] watchdog: $*" >> "$LOGFILE"; }

probe_once() {
    # Returns 0 if /api/health returns 200 within 5 seconds, else nonzero.
    local code
    code=$(curl -sk --connect-timeout 3 -m 5 -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
    [ "$code" = "200" ]
}

backend_pid_alive() {
    # Returns 0 if the pid recorded in BACKEND_PIDFILE is a live process.
    if [ ! -f "$BACKEND_PIDFILE" ]; then
        return 1
    fi
    local pid
    pid=$(cat "$BACKEND_PIDFILE" 2>/dev/null || true)
    if [ -z "$pid" ]; then
        return 1
    fi
    if kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    return 1
}

launcher_lock_held() {
    # Returns 0 if the launcher lock is held by a live process. Used to
    # avoid stacking a second dev-backend.sh on top of one that is
    # already in flight (e.g. the operator just re-ran the script).
    if [ ! -f "$LAUNCHER_LOCK" ]; then
        return 1
    fi
    local holder
    holder=$(cat "$LAUNCHER_LOCK" 2>/dev/null || true)
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
        return 0
    fi
    return 1
}

restart_backend() {
    # Before spawning a new dev-backend.sh, confirm there really is no
    # live backend AND no in-flight launcher. The health endpoint can
    # briefly stop answering during a uvicorn reload or under MCP
    # flapping even when the uvicorn parent pid is still alive and the
    # server is about to recover. If the pid is alive we skip the
    # restart entirely to avoid stacking a second uvicorn next to the
    # one that is just slow to respond.
    if backend_pid_alive; then
        log "INFO health probe failed but backend pid ($(cat "$BACKEND_PIDFILE" 2>/dev/null)) still alive; skipping restart"
        return 0
    fi
    if launcher_lock_held; then
        log "INFO dev-backend.sh launcher lock held by pid $(cat "$LAUNCHER_LOCK" 2>/dev/null); skipping restart"
        return 0
    fi
    log "WARNING backend unreachable and pid dead, restarting via $DEV_BACKEND"
    if [ ! -x "$DEV_BACKEND" ]; then
        log "ERROR dev-backend.sh not executable at $DEV_BACKEND, cannot restart"
        return 1
    fi
    # Launch the backend detached so this watchdog stays parent of nothing
    # but itself. The watchdog itself stays alive across restarts so a
    # second crash inside the same dev session also gets caught. The
    # launcher lock in dev-backend.sh serializes this invocation against
    # any concurrent manual run.
    MYOS_NO_WATCHDOG=1 nohup "$DEV_BACKEND" >> /tmp/dev-backend.log 2>&1 &
    log "INFO restart launched, dev-backend.sh pid=$!"
}

restarts=0
log "INFO watchdog started, interval=${INTERVAL}s, health=$HEALTH_URL"

while :; do
    sleep "$INTERVAL"
    if probe_once; then
        continue
    fi
    # Three spaced retries absorb the full uvicorn reload window
    # (reload-delay 10.0 means /api/health can be briefly unanswered
    # for up to 10 seconds). Only after three consecutive misses
    # spaced 5 seconds apart do we consider the backend actually down.
    # This removes most of the restart thrash caused by MCP flapping.
    sleep 5 && probe_once && { log "INFO transient miss, recovered on retry"; continue; }
    sleep 5 && probe_once && { log "INFO transient miss, recovered on retry"; continue; }
    sleep 5 && probe_once && { log "INFO transient miss, recovered on retry"; continue; }
    restarts=$((restarts + 1))
    if [ "$restarts" -gt "$MAX_RESTARTS" ]; then
        log "ERROR exceeded max restarts ($MAX_RESTARTS), exiting"
        exit 1
    fi
    restart_backend
    # Give the new uvicorn time to bind before the next probe.
    sleep 5
done
