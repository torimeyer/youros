#!/usr/bin/env bash
# Blue-green zero-downtime backend reload behind the Vite dev proxy (task ->3110).
#
# Steady-state canonical port remains PRIMARY_PORT (default 8000) so launchd,
# backend_watchdog.sh, and direct :8000 callers remain 100% compatible.
# During reload:
#   1. Boot warm standby on STANDBY_PORT (default 8002) and verify /api/health.
#      If standby fails to come up, abort immediately without touching :8000.
#   2. Sync disk state onto standby via POST /api/internal/reload-state.
#   3. Atomically point ~/.youros/active_backend_port at STANDBY_PORT so Vite
#      routes :3010 traffic to standby with zero dropped requests.
#   4. Restart canonical PRIMARY_PORT and wait until its new worker answers 200.
#   5. Sync any cutover writes back onto PRIMARY_PORT via /api/internal/reload-state.
#   6. Atomically restore ~/.youros/active_backend_port to PRIMARY_PORT, drain
#      in-flight standby requests, and terminate standby cleanly.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${YOUROS_HOME:-${MYOS_DIR:-$HOME/.youros}}"
LOG_DIR="$DATA_DIR/logs"

PRIMARY_PORT="${PRIMARY_PORT:-8000}"
STANDBY_PORT="${STANDBY_PORT:-8002}"
ACTIVE_PORT_FILE="${ACTIVE_PORT_FILE:-$DATA_DIR/active_backend_port}"
LOCK_FILE="${RELOAD_LOCK_FILE:-/tmp/youros-zero-downtime-reload.lock}"
READINESS_TIMEOUT="${READINESS_TIMEOUT:-25}"
DRAIN_SECONDS="${DRAIN_SECONDS:-2}"
CACHE_SETTLE_SECONDS="${CACHE_SETTLE_SECONDS:-0.15}"
LOCK_WAIT_SECONDS="${LOCK_WAIT_SECONDS:-30}"

if [ "$PRIMARY_PORT" = "$STANDBY_PORT" ]; then
    echo "ERROR: PRIMARY_PORT ($PRIMARY_PORT) and STANDBY_PORT ($STANDBY_PORT) must differ." >&2
    exit 1
fi

if [ -z "${SCHEME:-}" ]; then
    if [ -f "$DATA_DIR/localhost.key" ] && [ -f "$DATA_DIR/localhost.crt" ]; then
        SCHEME="https"
    else
        SCHEME="http"
    fi
fi

STANDBY_PID=""
STANDBY_STARTED=0

write_active_port() {
    local target_port="$1"
    local parent_dir
    parent_dir="$(dirname "$ACTIVE_PORT_FILE")"
    mkdir -p "$parent_dir"
    local tmp_file="${ACTIVE_PORT_FILE}.tmp.$$"
    printf '%s\n' "$target_port" > "$tmp_file"
    mv -f "$tmp_file" "$ACTIVE_PORT_FILE"
}

port_pids() {
    local check_port="$1"
    lsof -tiTCP:"$check_port" -sTCP:LISTEN 2>/dev/null | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//' || true
}

terminate_standby() {
    if [ "$STANDBY_STARTED" != "1" ] && [ -z "$STANDBY_PID" ]; then
        return 0
    fi

    local targets=""
    if [ -n "$STANDBY_PID" ] && kill -0 "$STANDBY_PID" 2>/dev/null; then
        targets="$STANDBY_PID"
    fi
    local listener_pids
    listener_pids="$(port_pids "$STANDBY_PORT")"
    if [ -n "$listener_pids" ]; then
        targets="$(printf '%s\n%s\n' "$targets" "$listener_pids" | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ' ' || true)"
    fi
    targets="${targets%% }"

    if [ -z "$targets" ]; then
        STANDBY_PID=""
        STANDBY_STARTED=0
        return 0
    fi

    kill -TERM $targets 2>/dev/null || true
    local tick=0
    while [ "$tick" -lt 25 ]; do
        local alive=""
        local pid_item
        for pid_item in $targets; do
            if kill -0 "$pid_item" 2>/dev/null; then
                alive="$alive $pid_item"
            fi
        done
        local remaining_listeners
        remaining_listeners="$(port_pids "$STANDBY_PORT")"
        if [ -z "${alive# }" ] && [ -z "$remaining_listeners" ]; then
            STANDBY_PID=""
            STANDBY_STARTED=0
            return 0
        fi
        sleep 0.1
        tick=$((tick + 1))
    done

    local force_targets
    force_targets="$(printf '%s\n%s\n' "$targets" "$(port_pids "$STANDBY_PORT")" | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ' ' || true)"
    if [ -n "${force_targets%% }" ]; then
        kill -KILL $force_targets 2>/dev/null || true
    fi
    STANDBY_PID=""
    STANDBY_STARTED=0
}

release_lock() {
    if [ -f "$LOCK_FILE" ]; then
        local holder
        holder="$(cat "$LOCK_FILE" 2>/dev/null || true)"
        if [ "$holder" = "$$" ]; then
            rm -f "$LOCK_FILE" 2>/dev/null || true
        fi
    fi
}

cleanup() {
    local exit_code=$?
    set +e
    write_active_port "$PRIMARY_PORT" 2>/dev/null || true
    terminate_standby 2>/dev/null || true
    release_lock 2>/dev/null || true
    exit "$exit_code"
}

acquire_lock() {
    local waited_ticks=0
    local max_ticks=$((LOCK_WAIT_SECONDS * 2))
    while ! ( set -o noclobber; echo $$ > "$LOCK_FILE" ) 2>/dev/null; do
        local holder
        holder="$(cat "$LOCK_FILE" 2>/dev/null || true)"
        if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
            if [ "$waited_ticks" -ge "$max_ticks" ]; then
                echo "Another zero-downtime reload (pid $holder) holds $LOCK_FILE after ${LOCK_WAIT_SECONDS}s. Aborting." >&2
                return 1
            fi
            sleep 0.5
            waited_ticks=$((waited_ticks + 1))
        else
            rm -f "$LOCK_FILE" 2>/dev/null || true
        fi
    done
    return 0
}

check_health() {
    local check_port="$1"
    local http_code
    http_code="$(curl --connect-timeout 1 -m 2 -sSk -o /dev/null -w '%{http_code}' "${SCHEME}://127.0.0.1:${check_port}/api/health" 2>/dev/null || true)"
    [ "$http_code" = "200" ]
}

sync_worker_state() {
    local target_port="$1"
    curl --connect-timeout 2 -m 3 -sSk -X POST "${SCHEME}://127.0.0.1:${target_port}/api/internal/reload-state" >/dev/null 2>&1 || true
}

wait_for_health() {
    local check_port="$1"
    local timeout_sec="$2"
    local watched_pid="${3:-}"
    local max_ticks
    max_ticks="$(awk "BEGIN { t = int(($timeout_sec / 0.2) + 0.999); print (t < 1 ? 1 : t) }")"
    local int_timeout
    int_timeout="$(awk "BEGIN { t = int($timeout_sec + 0.999); print (t < 1 ? 1 : t) }")"
    local start_sec=$SECONDS
    local tick=0

    while [ "$tick" -lt "$max_ticks" ]; do
        if check_health "$check_port"; then
            return 0
        fi
        if [ -n "$watched_pid" ] && ! kill -0 "$watched_pid" 2>/dev/null; then
            if check_health "$check_port"; then
                return 0
            fi
            return 1
        fi
        if [ $((SECONDS - start_sec)) -ge "$int_timeout" ] && [ "$tick" -ge 1 ]; then
            break
        fi
        sleep 0.2
        tick=$((tick + 1))
    done

    check_health "$check_port"
}

trigger_primary_restart() {
    mkdir -p "$LOG_DIR"
    if [ -n "${PRIMARY_RESTART_CMD:-}" ]; then
        PORT="$PRIMARY_PORT" eval "$PRIMARY_RESTART_CMD"
        return 0
    fi
    local uid_val
    uid_val="$(id -u)"
    if launchctl print "gui/${uid_val}/com.youros.backend" >/dev/null 2>&1; then
        launchctl kickstart -k "gui/${uid_val}/com.youros.backend"
        return 0
    fi
    PORT="$PRIMARY_PORT" YOUROS_FORCE_RESTART=1 nohup bash "$SCRIPT_DIR/restart-backend.sh" --hard >> "$LOG_DIR/backend-primary.log" 2>&1 < /dev/null &
}

wait_for_primary_replacement() {
    local old_pids="$1"
    local timeout_sec="$2"
    local max_ticks
    max_ticks="$(awk "BEGIN { t = int(($timeout_sec / 0.2) + 0.999); print (t < 1 ? 1 : t) }")"
    local int_timeout
    int_timeout="$(awk "BEGIN { t = int($timeout_sec + 0.999); print (t < 1 ? 1 : t) }")"
    local start_sec=$SECONDS
    local tick=0
    local saw_old_exit=0

    if [ -z "$old_pids" ]; then
        saw_old_exit=1
    fi

    while [ "$tick" -lt "$max_ticks" ]; do
        if [ "$saw_old_exit" = "0" ]; then
            local cur_pids
            cur_pids="$(port_pids "$PRIMARY_PORT")"
            local any_old_alive=0
            local old_pid
            for old_pid in $old_pids; do
                if kill -0 "$old_pid" 2>/dev/null; then
                    any_old_alive=1
                    break
                fi
            done
            if [ "$any_old_alive" = "0" ] || { [ -n "$cur_pids" ] && [ "$cur_pids" != "$old_pids" ]; }; then
                saw_old_exit=1
            fi
        fi

        if [ "$saw_old_exit" = "1" ] && check_health "$PRIMARY_PORT"; then
            return 0
        fi

        if [ $((SECONDS - start_sec)) -ge "$int_timeout" ] && [ "$tick" -ge 1 ]; then
            break
        fi
        sleep 0.2
        tick=$((tick + 1))
    done

    [ "$saw_old_exit" = "1" ] && check_health "$PRIMARY_PORT"
}

if ! acquire_lock; then
    exit 1
fi

trap cleanup EXIT INT TERM

mkdir -p "$LOG_DIR"

# Cold-start path: if canonical primary is not currently healthy, start it directly.
if ! check_health "$PRIMARY_PORT"; then
    echo "Primary port $PRIMARY_PORT is not healthy; starting primary directly..."
    write_active_port "$PRIMARY_PORT"
    trigger_primary_restart
    if ! wait_for_health "$PRIMARY_PORT" "$READINESS_TIMEOUT"; then
        echo "ERROR: Primary backend on port $PRIMARY_PORT failed readiness check within ${READINESS_TIMEOUT}s." >&2
        exit 1
    fi
    echo "Primary backend healthy on port $PRIMARY_PORT."
    exit 0
fi

OLD_PRIMARY_PIDS="$(port_pids "$PRIMARY_PORT")"

# Clear any leftover listener on STANDBY_PORT before launching fresh standby.
LEFTOVER_STANDBY="$(port_pids "$STANDBY_PORT")"
if [ -n "$LEFTOVER_STANDBY" ]; then
    kill -TERM $LEFTOVER_STANDBY 2>/dev/null || true
    sleep 0.3
    STILL_STANDBY="$(port_pids "$STANDBY_PORT")"
    if [ -n "$STILL_STANDBY" ]; then
        kill -KILL $STILL_STANDBY 2>/dev/null || true
        sleep 0.2
    fi
fi

echo "Starting standby backend on port $STANDBY_PORT..."
STANDBY_STARTED=1
if [ -n "${STANDBY_START_CMD:-}" ]; then
    PORT="$STANDBY_PORT" RELEASE_MODE=1 YOUROS_NO_WATCHDOG=1 MYOS_NO_WATCHDOG=1 \
        eval "$STANDBY_START_CMD" > "$LOG_DIR/backend-standby.log" 2>&1 < /dev/null &
    STANDBY_PID=$!
else
    PORT="$STANDBY_PORT" RELEASE_MODE=1 YOUROS_NO_WATCHDOG=1 MYOS_NO_WATCHDOG=1 YOUROS_FORCE_RESTART=1 \
        bash "$SCRIPT_DIR/dev-backend.sh" > "$LOG_DIR/backend-standby.log" 2>&1 < /dev/null &
    STANDBY_PID=$!
fi

if ! wait_for_health "$STANDBY_PORT" "$READINESS_TIMEOUT" "$STANDBY_PID"; then
    echo "ERROR: Standby backend on port $STANDBY_PORT failed healthcheck; primary on $PRIMARY_PORT was untouched." >&2
    echo "       Inspect $LOG_DIR/backend-standby.log for startup errors." >&2
    exit 1
fi

sync_worker_state "$STANDBY_PORT"

echo "Standby healthy on port $STANDBY_PORT; cutting Vite proxy traffic over..."
write_active_port "$STANDBY_PORT"
sleep "$CACHE_SETTLE_SECONDS"
sync_worker_state "$STANDBY_PORT"

echo "Restarting canonical primary backend on port $PRIMARY_PORT..."
trigger_primary_restart

if ! wait_for_primary_replacement "$OLD_PRIMARY_PIDS" "$READINESS_TIMEOUT"; then
    echo "ERROR: Primary backend on port $PRIMARY_PORT failed to recover within ${READINESS_TIMEOUT}s." >&2
    exit 1
fi

sync_worker_state "$PRIMARY_PORT"

echo "Primary healthy on port $PRIMARY_PORT; restoring active port and draining standby (${DRAIN_SECONDS}s)..."
write_active_port "$PRIMARY_PORT"
sleep "$CACHE_SETTLE_SECONDS"
sync_worker_state "$PRIMARY_PORT"
sleep "$DRAIN_SECONDS"
sync_worker_state "$PRIMARY_PORT"

terminate_standby
echo "Zero-downtime backend reload complete on port $PRIMARY_PORT."
