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

restart_backend() {
    log "WARNING backend unreachable, restarting via $DEV_BACKEND"
    if [ ! -x "$DEV_BACKEND" ]; then
        log "ERROR dev-backend.sh not executable at $DEV_BACKEND, cannot restart"
        return 1
    fi
    # Launch the backend detached so this watchdog stays parent of nothing
    # but itself. The watchdog itself stays alive across restarts so a
    # second crash inside the same dev session also gets caught.
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
    # One retry to absorb a momentarily slow response.
    sleep 2
    if probe_once; then
        log "INFO transient miss, recovered on retry"
        continue
    fi
    restarts=$((restarts + 1))
    if [ "$restarts" -gt "$MAX_RESTARTS" ]; then
        log "ERROR exceeded max restarts ($MAX_RESTARTS), exiting"
        exit 1
    fi
    restart_backend
    # Give the new uvicorn time to bind before the next probe.
    sleep 5
done
