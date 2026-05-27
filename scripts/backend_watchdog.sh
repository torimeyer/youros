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
#   MYOS_WATCHDOG_DEADLOCK_THRESHOLD consecutive failed health probes while
#                              the backend PID is still alive before the
#                              watchdog treats it as a deadlocked event loop
#                              and sends SIGKILL (default 10)
#                              TRADE-OFF: a higher threshold means a genuinely
#                              deadlocked event loop takes longer to recover
#                              (10 × 30s ≈ 5 min). In practice, /api/health is
#                              lightweight and should never miss 10 consecutive
#                              probes unless the loop is truly stuck, so false
#                              positives (killing a healthy-but-busy backend)
#                              drop to near-zero at threshold 10.
#   MYOS_WATCHDOG_RESTART_WAIT seconds to wait for the new backend to become
#                              healthy after a restart before releasing the
#                              restart lock (default 15; tests use 1)
#   MYOS_WATCHDOG_PYSPY_TIMEOUT seconds to wait for py-spy to capture a stack
#                              dump before proceeding to SIGKILL (default 5;
#                              tests use a smaller value)
#   MYOS_WATCHDOG_PIDFILE      path to the watchdog pidfile (default
#                              /tmp/myos-backend-watchdog.pid; tests use a
#                              temp path to avoid dedup-guard conflicts with
#                              a live dev watchdog)
#   MYOS_WATCHDOG_LOGFILE      path to the watchdog logfile (default
#                              /tmp/myos-backend-watchdog.log)

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# DEV_BACKEND is overridable so tests (and future supervisors) can point the
# watchdog at a stub instead of the real launcher that binds port 8000.
DEV_BACKEND="${MYOS_WATCHDOG_DEV_BACKEND:-$SCRIPT_DIR/dev-backend.sh}"
PIDFILE="${MYOS_WATCHDOG_PIDFILE:-/tmp/myos-backend-watchdog.pid}"
LOGFILE="${MYOS_WATCHDOG_LOGFILE:-/tmp/myos-backend-watchdog.log}"
INTERVAL="${MYOS_WATCHDOG_INTERVAL:-30}"
HEALTH_URL="${MYOS_WATCHDOG_HEALTH_URL:-https://127.0.0.1:8000/api/health}"
MAX_RESTARTS="${MYOS_WATCHDOG_MAX_RESTARTS:-50}"
# Seconds to sleep between retries inside probe_once (default 5; tests set to 0
# so the inner retry loop completes in milliseconds instead of 10 seconds).
PROBE_RETRY_SLEEP="${MYOS_WATCHDOG_PROBE_RETRY_SLEEP:-5}"

# Port the backend listens on. The watchdog reads the matching pidfile
# (/tmp/myos-backend-<port>.pid) to verify whether a backend process is
# actually running before attempting a restart. Default 8000; tests pass
# a different port via env var so they don't collide with a live dev
# backend on :8000.
BACKEND_PORT="${MYOS_WATCHDOG_BACKEND_PORT:-8000}"
BACKEND_PIDFILE="/tmp/myos-backend-${BACKEND_PORT}.pid"
LAUNCHER_LOCK="/tmp/myos-backend-launcher-${BACKEND_PORT}.lock"
# Serialises concurrent restart_backend calls across watchdog instances.
# Two simultaneous watchdogs both seeing a dead PID can both pass the
# backend_pid_alive check and both spawn dev-backend.sh — the second then
# kills the uvicorn the first just started (kill-and-replace) and starts
# its own, producing a double-bind on port 8000 via macOS SO_REUSEPORT.
RESTART_LOCK="/tmp/myos-backend-restart.lock"
# Serialises concurrent watchdog startups so two simultaneous launches cannot
# both pass the dedup check (→942 recurrence: duplicate log lines at identical
# timestamps confirmed two live watchdogs).
STARTUP_LOCK="${PIDFILE%.pid}-startup.lock"

# Dedup guard: exit immediately if a live watchdog is already registered.
# Multiple watchdog processes spawn when concurrent dev-backend.sh launchers
# both pass the (previously racy) "is watchdog running?" check at the same
# time.  Two watchdogs firing simultaneously both call restart_backend, which
# runs dev-backend.sh twice, which races on the PIDFILE and port — one wins
# the exec/port race, the other fails with EADDRINUSE, and the dead loser's
# PID ends up in PIDFILE.  On the next reload the watchdog sees that stale PID
# as dead, bypasses backend_pid_alive(), and kills the live uvicorn.
#
# The TOCTOU fix (→942 recurrence): wrap the read-check-write in STARTUP_LOCK
# so only one process at a time can evaluate PIDFILE and claim ownership.
# Without this, two concurrent spawns both read a stale dead PID, both skip
# the dedup exit, and both execute `echo $$ > "$PIDFILE"` — producing two
# live watchdogs even though the last write "wins" the file.
_sl_waited=0
while ! ( set -o noclobber; echo $$ > "$STARTUP_LOCK" ) 2>/dev/null; do
    _sl_holder=$(cat "$STARTUP_LOCK" 2>/dev/null || true)
    if [ -n "$_sl_holder" ] && kill -0 "$_sl_holder" 2>/dev/null; then
        if [ "$_sl_waited" -ge 5 ]; then
            # Startup lock held for > 5s — bail rather than stack a third watchdog.
            exit 0
        fi
        sleep 1
        _sl_waited=$((_sl_waited + 1))
    else
        # Stale lock: holder is dead. Remove and retry.
        rm -f "$STARTUP_LOCK" 2>/dev/null || true
    fi
done
# STARTUP_LOCK held. Now check+write PIDFILE as an atomic unit.
if [ -f "$PIDFILE" ]; then
    _existing_wd=$(cat "$PIDFILE" 2>/dev/null || true)
    if [ -n "$_existing_wd" ] && [ "$_existing_wd" != "$$" ] && kill -0 "$_existing_wd" 2>/dev/null; then
        echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] watchdog: duplicate start detected (pid $_existing_wd already running), exiting" >> "$LOGFILE"
        rm -f "$STARTUP_LOCK" 2>/dev/null || true
        exit 0
    fi
fi
# Always keep the freshest pid in the pidfile, even when invoked directly.
echo $$ > "$PIDFILE"
rm -f "$STARTUP_LOCK" 2>/dev/null || true

# Kill any ghost watchdog processes that survived a pre-fix double-spawn.
# The STARTUP_LOCK (above) serializes concurrent startups so only ONE new
# watchdog writes PIDFILE, but ghost siblings from before the STARTUP_LOCK
# fix was deployed can still be alive — they hold no lock and appear in
# pgrep but not in PIDFILE. Without this purge they run indefinitely, and
# both fire restart_backend on the next backend failure, causing dual
# dev-backend.sh spawns and the kill-and-replace cascade in →942.
_ghost_wds=$(pgrep -f "backend_watchdog.sh" 2>/dev/null | grep -v "^$$" || true)
if [ -n "$_ghost_wds" ]; then
    log "INFO purging ghost watchdog(s): $(echo "$_ghost_wds" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill $_ghost_wds 2>/dev/null || true
fi

cleanup() {
    # Only remove files we own. Unconditional removal deletes a successor
    # watchdog's PID record if this watchdog exits after a newer one has
    # already overwritten the file — leaving the successor orphaned and
    # invisible to the next dev-backend.sh check, which then starts a
    # third watchdog. That third watchdog races with the second on the
    # next backend crash, causing a concurrent double-spawn of dev-backend.sh
    # and the SO_REUSEPORT double-bind seen in →942.
    if [ -f "$PIDFILE" ] && [ "$(cat "$PIDFILE" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$PIDFILE" 2>/dev/null || true
    fi
    if [ -f "$RESTART_LOCK" ] && [ "$(cat "$RESTART_LOCK" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$RESTART_LOCK" 2>/dev/null || true
    fi
    if [ -f "$STARTUP_LOCK" ] && [ "$(cat "$STARTUP_LOCK" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$STARTUP_LOCK" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] watchdog: $*" >> "$LOGFILE"; }

probe_once() {
    # Returns 0 if /api/health returns 200 within 5 seconds, else nonzero.
    # Retries 3x with PROBE_RETRY_SLEEP between attempts to survive TLS
    # handshake warm-up after restart.
    #
    # WHY || true instead of || echo "000":
    # curl -w "%{http_code}" always writes "000" to stdout when no HTTP
    # response is received (connection refused, timeout, TLS error). If we
    # also append `|| echo "000"`, the captured value becomes "000000"
    # (6 chars), which != "000", so the retry loop breaks immediately on the
    # first failure — zero retries. With || true the exit status is suppressed
    # without touching stdout, keeping code="000" so the loop retries.
    #
    # WHY --tls-max 1.2 is removed:
    # The backend negotiates TLS 1.3 by default. Capping at TLS 1.2 forces an
    # extra round-trip handshake; on a loaded system this can push the total
    # transfer time over the 5 s -m limit. A direct probe without --tls-max 1.2
    # reliably returns 200 in <1 s. Matching that flag set removes the skew.
    local code
    code="000"
    for _retry in 1 2 3; do
        code=$(curl --silent --insecure --tlsv1.2 --connect-timeout 3 -m 5 -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null) || true
        [ "$code" != "000" ] && break
        [ "$_retry" -lt 3 ] && sleep "$PROBE_RETRY_SLEEP"
    done
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
        consecutive_pid_alive_failures=$((consecutive_pid_alive_failures + 1))
        _threshold="${MYOS_WATCHDOG_DEADLOCK_THRESHOLD:-10}"
        if [ "$consecutive_pid_alive_failures" -lt "$_threshold" ]; then
            log "INFO health probe failed but backend pid alive (deadlock check ${consecutive_pid_alive_failures}/${_threshold}); skipping restart"
            return 0
        fi
        _locked_pid=$(cat "$BACKEND_PIDFILE" 2>/dev/null || true)
        log "WARNING backend pid $_locked_pid alive but health probe failed ${consecutive_pid_alive_failures}x -- event loop likely deadlocked; sending SIGKILL"
        # Capture a py-spy thread dump before killing so we can diagnose what
        # was blocking the asyncio event loop.
        _pyspy_out="/tmp/myos-deadlock-$(date +%s)-pid${_locked_pid}.txt"
        _pyspy_timeout="${MYOS_WATCHDOG_PYSPY_TIMEOUT:-5}"
        if command -v py-spy >/dev/null 2>&1; then
            _pyspy_exit=0
            if command -v timeout >/dev/null 2>&1; then
                _pyspy_timeout_cmd="timeout"
            elif command -v gtimeout >/dev/null 2>&1; then
                _pyspy_timeout_cmd="gtimeout"
            else
                _pyspy_timeout_cmd=""
            fi
            if [ -n "$_pyspy_timeout_cmd" ]; then
                "$_pyspy_timeout_cmd" "$_pyspy_timeout" py-spy dump --pid "$_locked_pid" > "$_pyspy_out" 2>&1 || _pyspy_exit=$?
            else
                py-spy dump --pid "$_locked_pid" > "$_pyspy_out" 2>&1 &
                _pyspy_bg=$!
                _pyspy_waited=0
                while [ "$_pyspy_waited" -lt "$_pyspy_timeout" ] && kill -0 "$_pyspy_bg" 2>/dev/null; do
                    sleep 1
                    _pyspy_waited=$((_pyspy_waited + 1))
                done
                if kill -0 "$_pyspy_bg" 2>/dev/null; then
                    kill "$_pyspy_bg" 2>/dev/null || true
                    wait "$_pyspy_bg" 2>/dev/null || true
                    _pyspy_exit=124
                else
                    wait "$_pyspy_bg" 2>/dev/null || true
                    _pyspy_exit=$?
                fi
            fi
            if [ "$_pyspy_exit" -eq 0 ]; then
                log "INFO captured py-spy dump for pid $_locked_pid to $_pyspy_out"
            elif grep -qi "permission\|insufficient" "$_pyspy_out" 2>/dev/null; then
                log "INFO py-spy needs sudo on macOS; run: sudo py-spy dump --pid $_locked_pid"
            else
                log "WARNING py-spy dump failed for pid $_locked_pid (exit=$_pyspy_exit)"
            fi
        else
            log "INFO py-spy not found on PATH; skipping stack dump before SIGKILL"
        fi
        kill -9 "$_locked_pid" 2>/dev/null || true
        # Wait up to 5s for the process to actually die before spawning a replacement.
        _kw=0
        while [ "$_kw" -lt 5 ] && kill -0 "$_locked_pid" 2>/dev/null; do
            sleep 1
            _kw=$((_kw + 1))
        done
        if kill -0 "$_locked_pid" 2>/dev/null; then
            log "ERROR pid $_locked_pid still alive after SIGKILL; proceeding anyway"
        else
            log "INFO pid $_locked_pid confirmed dead after SIGKILL"
        fi
        rm -f "$BACKEND_PIDFILE" 2>/dev/null || true
        consecutive_pid_alive_failures=0
        # Kill-only mode: launchd (KeepAlive=true) owns the respawn. We SIGKILLed
        # the wedged uvicorn above; do NOT launch dev-backend.sh, or we would race
        # launchd for port 8000 -- the exact double-bind the locking below guards
        # against. launchd sees the process exit and respawns it in ~1s.
        if [ "${MYOS_WATCHDOG_KILL_ONLY:-0}" = "1" ]; then
            log "INFO kill-only mode: wedged backend SIGKILLed, leaving respawn to launchd"
            return 0
        fi
        # Fall through to the normal restart path below.
    fi
    # Kill-only mode, crash path: the pid is dead (process already exited). launchd
    # respawns on exit, so the watchdog must do nothing here -- launching
    # dev-backend.sh would create a second uvicorn racing launchd's respawn.
    if [ "${MYOS_WATCHDOG_KILL_ONLY:-0}" = "1" ]; then
        log "INFO kill-only mode: backend down and pid dead, leaving respawn to launchd"
        return 0
    fi
    # Acquire restart lock atomically before spawning. When two watchdog
    # instances run simultaneously both can pass backend_pid_alive (both
    # read the same stale dead PID) and launcher_lock_held (neither has
    # started yet) and both spawn dev-backend.sh. The second dev-backend.sh
    # then kills the uvicorn the first just started (kill-and-replace),
    # briefly dropping the backend again — which re-triggers both watchdogs
    # and creates the 50-restart cascade seen in →942.
    if ! ( set -o noclobber; echo $$ > "$RESTART_LOCK" ) 2>/dev/null; then
        _rl_holder=$(cat "$RESTART_LOCK" 2>/dev/null || true)
        if [ -n "$_rl_holder" ] && kill -0 "$_rl_holder" 2>/dev/null; then
            log "INFO another watchdog ($_rl_holder) is already restarting; skipping duplicate restart"
            return 0
        fi
        # Stale lock: holder is dead. Remove and retry once.
        rm -f "$RESTART_LOCK" 2>/dev/null || true
        if ! ( set -o noclobber; echo $$ > "$RESTART_LOCK" ) 2>/dev/null; then
            log "INFO restart lock contested; skipping restart"
            return 0
        fi
    fi
    # Re-check after acquiring the lock: another watchdog may have already
    # restarted the backend while we were spinning.
    if backend_pid_alive; then
        rm -f "$RESTART_LOCK" 2>/dev/null || true
        log "INFO backend recovered while acquiring restart lock; skipping restart"
        return 0
    fi
    if launcher_lock_held; then
        rm -f "$RESTART_LOCK" 2>/dev/null || true
        log "INFO dev-backend.sh launcher lock held by pid $(cat "$LAUNCHER_LOCK" 2>/dev/null); skipping restart"
        return 0
    fi
    log "WARNING backend unreachable and pid dead, restarting via $DEV_BACKEND"
    if [ ! -x "$DEV_BACKEND" ]; then
        log "ERROR dev-backend.sh not executable at $DEV_BACKEND, cannot restart"
        rm -f "$RESTART_LOCK" 2>/dev/null || true
        return 1
    fi
    # Launch the backend detached so this watchdog stays parent of nothing
    # but itself. The watchdog itself stays alive across restarts so a
    # second crash inside the same dev session also gets caught. The
    # launcher lock in dev-backend.sh serializes this invocation against
    # any concurrent manual run.
    MYOS_NO_WATCHDOG=1 nohup "$DEV_BACKEND" >> /tmp/dev-backend.log 2>&1 &
    log "INFO restart launched, dev-backend.sh pid=$!"
    # Hold the restart lock until uvicorn has had time to bind (up to
    # MYOS_WATCHDOG_RESTART_WAIT seconds, default 15). Tests set this to a
    # small value so they don't have to wait the full startup window.
    _restart_wait="${MYOS_WATCHDOG_RESTART_WAIT:-15}"
    _rw=0
    while [ "$_rw" -lt "$_restart_wait" ] && ! probe_once; do
        sleep 1
        _rw=$((_rw + 1))
    done
    rm -f "$RESTART_LOCK" 2>/dev/null || true
}

restarts=0
consecutive_pid_alive_failures=0
log "INFO watchdog started, interval=${INTERVAL}s, health=$HEALTH_URL"

while :; do
    # Self-check: exit if PIDFILE now has a different live PID. This catches
    # any ghost watchdog that survived long enough to reach the main loop —
    # once a newer canonical watchdog writes its PID, all ghosts detect it
    # here and exit within one INTERVAL (30 s) rather than running forever.
    _loop_wd=$(cat "$PIDFILE" 2>/dev/null || true)
    if [ -n "$_loop_wd" ] && [ "$_loop_wd" != "$$" ] && kill -0 "$_loop_wd" 2>/dev/null; then
        log "INFO superseded by pid $_loop_wd; exiting to avoid ghost watchdog"
        exit 0
    fi
    sleep "$INTERVAL"
    if probe_once; then
        consecutive_pid_alive_failures=0
        continue
    fi
    # Three spaced retries absorb the full uvicorn reload window
    # (reload-delay 10.0 means /api/health can be briefly unanswered
    # for up to 10 seconds). Only after three consecutive misses
    # spaced 5 seconds apart do we consider the backend actually down.
    # This removes most of the restart thrash caused by MCP flapping.
    sleep "${MYOS_WATCHDOG_TRANSIENT_RETRY_SLEEP:-5}" && probe_once && { log "INFO transient miss, recovered on retry"; continue; }
    sleep "${MYOS_WATCHDOG_TRANSIENT_RETRY_SLEEP:-5}" && probe_once && { log "INFO transient miss, recovered on retry"; continue; }
    sleep "${MYOS_WATCHDOG_TRANSIENT_RETRY_SLEEP:-5}" && probe_once && { log "INFO transient miss, recovered on retry"; continue; }
    restarts=$((restarts + 1))
    if [ "$restarts" -gt "$MAX_RESTARTS" ]; then
        log "ERROR exceeded max restarts ($MAX_RESTARTS), exiting"
        exit 1
    fi
    restart_backend
    # Give the new uvicorn time to bind before the next probe.
    sleep 5
done
