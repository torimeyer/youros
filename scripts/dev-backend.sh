#!/bin/bash
# --- trunk guard: yourOS serves from main. Warn (do not block) if not. ---
_GB="$(git -C "$(dirname "$0")/.." branch --show-current 2>/tmp/git_branch_err.log)"
if [ -n "$_GB" ] && [ "$_GB" != "main" ]; then
  printf '\033[33mWARNING: serving backend from branch "%s", not main. Work merged to main will NOT appear here. Run: git checkout main (ALLOW_NONMAIN=1 silences this).\033[0m\n' "$_GB" >&2
  [ -n "$ALLOW_NONMAIN" ] || sleep 2
fi
# --- end trunk guard ---
# Dev-only helper to start the FastAPI backend via uvicorn with the
# correct --reload exclusions and a belt-and-braces stale-listener
# cleanup on port 8000. Parallel to scripts/dev-frontend.sh.
#
# Needle 287 fixed the vite zombie that held port 3010 after every
# interact kill. The same risk exists on port 8000 if an earlier
# uvicorn worker survives a reloader crash. This script frees the
# port first, then execs uvicorn so killing the spawn tears down the
# real process.
#
# Usage:
#   scripts/dev-backend.sh          # starts uvicorn on port 8000
#
# IMPORTANT: when backgrounding this script from mcp__ostk__bash, always redirect
# stdout and stderr, or the tool call will block until uvicorn is killed:
#   nohup scripts/dev-backend.sh > /tmp/dev-backend.log 2>#   scripts/dev-backend.sh          # starts uvicorn on port 80001 < /dev/null & disown
# See docs/agents/bash-background-processes.md. Prefer mcp__ostk__spawn instead.

set -e
set +m 2>/dev/null  # suppress job-control noise ([N] PID lines)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# Allow the caller to override API_DIR (useful when this script runs from
# a git worktree that does not carry its own api/.venv; tests set this
# to the main repo's api/ directory).
API_DIR="${MYOS_API_DIR:-$REPO_DIR/api}"
UVICORN_PORT="${PORT:-${UVICORN_PORT:-8000}}"

# OAuth callbacks (Slack, GitHub, Atlassian, Drive) redirect the browser to
# FRONTEND_URL after connecting. In dev the frontend is the vite server on
# :3010 while this backend listens on :8000, so without this the callback
# falls back to the backend's own URL and the browser lands on :8000/<page>,
# which 404s ({"detail":"Not Found"}). Respect an explicit override; otherwise
# point at the dev frontend so connect flows return to the app.
export FRONTEND_URL="${FRONTEND_URL:-https://localhost:3010}"

# Pidfile + launcher lock live in /tmp and are scoped by port so parallel
# ports (e.g. :8001 in tests, :8000 in dev) do not clobber each other.
PIDFILE="/tmp/youros-backend-${UVICORN_PORT}.pid"
LAUNCHER_LOCK="/tmp/youros-backend-launcher-${UVICORN_PORT}.lock"

if [ ! -f "$API_DIR/.venv/bin/activate" ]; then
    echo "Python virtualenv not found at $API_DIR/.venv. Run install.sh first." >&2
    exit 1
fi

# Serialize concurrent dev-backend.sh invocations. Without this lock, the
# operator and the watchdog (scripts/backend_watchdog.sh) can both run
# dev-backend.sh at the same time: each sees a live uvicorn, each kills
# it, and whoever reaches `exec uvicorn` first wins. In the narrow window
# where the socket is freed but not yet re-bound, a second uvicorn can
# briefly coexist with the first and cause intermittent empty-body
# responses before one of them exits with EADDRINUSE.
#
# The lock is a pidfile containing the bash PID of the in-flight launcher.
# A stale lock from a crashed script is reclaimed automatically when the
# recorded PID is no longer alive. We wait up to 15 seconds for an
# in-flight launcher to finish (uvicorn startup takes 2 to 5 seconds)
# before giving up and exiting non-zero so the caller sees a clear message.
acquire_launcher_lock() {
    local waited=0
    # Use set -o noclobber in a subshell so the shell opens the lock file with
    # O_CREAT|O_EXCL — a single atomic syscall that fails if the file already
    # exists. This closes the TOCTOU window in the old check-then-write pattern
    # (two concurrent callers could both see "no lock file" and both proceed,
    # with the last writer winning PIDFILE but the first exec winning the port,
    # leaving PIDFILE pointing to the dead loser and corrupting the watchdog's
    # backend_pid_alive check on the next reload).
    while ! ( set -o noclobber; echo $$ > "$LAUNCHER_LOCK" ) 2>/dev/null; do
        local holder
        holder=$(cat "$LAUNCHER_LOCK" 2>/dev/null || true)
        if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
            # If the lock PID matches PIDFILE, the previous launcher exec'd
            # uvicorn without releasing the lock (intentional — prevents a
            # concurrent launcher from racing in during the startup window
            # before uvicorn has bound the port). Wait up to 10s for uvicorn
            # to actually bind, then clear the stale launcher lock so
            # kill-and-replace can proceed normally.
            local _pidfile_pid
            _pidfile_pid=$(cat "$PIDFILE" 2>/dev/null || true)
            if [ -n "$_pidfile_pid" ] && [ "$holder" = "$_pidfile_pid" ]; then
                for _bw in 1 2 3 4 5 6 7 8 9 10; do
                    sleep 1
                    if [ -n "$(lsof -tiTCP:$UVICORN_PORT -sTCP:LISTEN 2>/dev/null || true)" ]; then
                        break
                    fi
                done
                rm -f "$LAUNCHER_LOCK" 2>/dev/null || true
                continue
            fi
            if [ "$waited" -ge 15 ]; then
                echo "Another dev-backend.sh launcher (pid $holder) is holding $LAUNCHER_LOCK after 15s. Exiting." >&2
                return 1
            fi
            sleep 1
            waited=$((waited + 1))
        else
            # Stale lock: the recorded pid is gone. Remove and retry.
            rm -f "$LAUNCHER_LOCK" 2>/dev/null || true
        fi
    done
    return 0
}

release_launcher_lock() {
    # Only remove the lock if we own it. Avoid deleting another launcher's
    # lock if we exit early and the slot has already been claimed.
    if [ -f "$LAUNCHER_LOCK" ]; then
        local holder
        holder=$(cat "$LAUNCHER_LOCK" 2>/dev/null || true)
        if [ "$holder" = "$$" ]; then
            rm -f "$LAUNCHER_LOCK" 2>/dev/null || true
        fi
    fi
}

# Release the launcher lock on any exit except a successful `exec uvicorn`
# (exec replaces the process, taking our PID over, so the lock naturally
# stays pointed at a live pid until uvicorn exits; we clean it up in the
# watchdog instead when the uvicorn pid is found dead).
trap release_launcher_lock EXIT INT TERM

if ! acquire_launcher_lock; then
    exit 1
fi

# Guard: abort if a LIVE dev-backend.sh uvicorn is already listening on
# this port. Two independent invocations of this script double-bind the
# socket via SO_REUSEPORT, which makes lsof show two PIDs sharing the
# same fd and causes intermittent empty-body responses as the OS routes
# packets unpredictably between the two processes.
#
# We distinguish "legit live server" from "stale zombie" by checking
# whether the listening pid's command contains "uvicorn main:app".
# A zombie from a previous SIGKILL usually has a defunct command line
# or has already exited; a live dev server has the full uvicorn invocation.
existing_pids=$(lsof -tiTCP:$UVICORN_PORT -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$existing_pids" ]; then
    live_uvicorn_pids=""
    for _pid in $existing_pids; do
        _cmd=$(ps -p "$_pid" -o command= 2>/dev/null || true)
        if echo "$_cmd" | grep -q "uvicorn main:app"; then
            live_uvicorn_pids="$live_uvicorn_pids $_pid"
        fi
    done
    live_uvicorn_pids="${live_uvicorn_pids# }"  # trim leading space

    # KILL-AND-REPLACE instead of abort. Earlier versions of this script
    # aborted here with an error and asked the user to `kill` the existing
    # uvicorn manually. In practice that left the old hung uvicorn in place
    # while the watchdog kept retrying restarts, and any transient race in
    # process detection let a SECOND uvicorn bind the same port via
    # SO_REUSEPORT (macOS default). Two uvicorns on the same socket produce
    # empty-body responses and long API hangs, which make the Agents page
    # show stale "running" rows long after agents have completed.
    #
    # We now assume the caller actually wants this backend to be the live
    # one (they just ran `dev-backend.sh`) and kill the existing uvicorn
    # plus any orphan listener pid before proceeding. free_port_or_die below
    # handles the hard-kill escalation if SIGTERM is not enough.
    if [ -n "$live_uvicorn_pids" ]; then
        # IN-FLIGHT AGENT GUARD: refuse to kill a healthy backend while agents
        # are still running. Backend-managed agent workers die with the backend;
        # their uncommitted work survives in their worktrees but is orphaned and
        # the rows show as "abandoned" on restart. We only block when the live
        # backend can still answer /api/agents AND reports running/queued agents.
        # If the backend is wedged or down, the curl fails, _agents_json is empty,
        # and the guard falls through so watchdog recovery still works. Override
        # with MYOS_FORCE_RESTART=1 to bounce anyway.
        if [ "${MYOS_FORCE_RESTART:-0}" != "1" ]; then
            _agents_json=$(curl -sf --connect-timeout 3 -m 5 -k "https://127.0.0.1:$UVICORN_PORT/api/agents" 2>/dev/null \
                || curl -sf --connect-timeout 3 -m 5 "http://127.0.0.1:$UVICORN_PORT/api/agents" 2>/dev/null \
                || true)
            if [ -n "$_agents_json" ]; then
                _running=$(printf '%s' "$_agents_json" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    rows = d.get("agents", d) if isinstance(d, dict) else d
    for a in rows:
        # Only worktree-isolated spawned agents represent at-risk work: a bounce
        # orphans their uncommitted worktree. Bare session rows (the operating
        # Claude session, the api backend itself) have no worktree_path and
        # survive a restart, so they must NOT block it or every restart during a
        # live session would be refused.
        wt = a.get("worktree_path")
        if a.get("status") in ("running", "queued") and wt and wt not in ("", "-"):
            print("  - %s (%s) worktree=%s" % (a.get("name", "?"), a.get("status", "?"), wt))
except Exception:
    pass
' 2>/dev/null || true)
                if [ -n "$_running" ]; then
                    echo "REFUSING to restart backend on port $UVICORN_PORT: agents are still running." >&2
                    echo "$_running" >&2
                    echo "Their work lives in the worktrees above and will be orphaned if the backend dies." >&2
                    echo "Wait for them to finish, or set MYOS_FORCE_RESTART=1 to bounce anyway." >&2
                    exit 1
                fi
            fi
        fi
        echo "Existing uvicorn(s) on port $UVICORN_PORT (PID(s): $live_uvicorn_pids). Killing to reclaim socket and avoid double-bind."
        kill $live_uvicorn_pids 2>/dev/null || true
        # Give them up to 3 seconds to release the port gracefully. If the
        # socket is still held after that, free_port_or_die below will
        # escalate to SIGKILL.
        for _i in 1 2 3 4 5 6; do
            sleep 0.5
            if [ -z "$(lsof -tiTCP:$UVICORN_PORT -sTCP:LISTEN 2>/dev/null || true)" ]; then
                break
            fi
        done
    fi
fi

# Free port 8000 if a stale (non-uvicorn) worker is still listening.
# Belt-and-braces release: SIGTERM, wait for socket to free, then SIGKILL,
# then wait again. If the socket is STILL bound after both rounds, retry
# the whole sequence one more time before giving up. This pattern matters
# during the demo: lsof sometimes reports a pid that is mid-shutdown and
# the socket only releases a second or two after the process exits.
free_port_or_die() {
    local attempt
    for attempt in 1 2; do
        local stale_pids
        stale_pids=$(lsof -tiTCP:$UVICORN_PORT -sTCP:LISTEN 2>/dev/null || true)
        if [ -z "$stale_pids" ]; then
            return 0
        fi
        echo "Freeing port $UVICORN_PORT from stale listener(s): $stale_pids (attempt $attempt)"
        kill $stale_pids 2>/dev/null || true
        # Wait up to 3 seconds for graceful shutdown.
        local i
        for i in 1 2 3 4 5 6; do
            sleep 0.5
            if [ -z "$(lsof -tiTCP:$UVICORN_PORT -sTCP:LISTEN 2>/dev/null || true)" ]; then
                return 0
            fi
        done
        # Still bound, escalate to SIGKILL.
        local still
        still=$(lsof -tiTCP:$UVICORN_PORT -sTCP:LISTEN 2>/dev/null || true)
        if [ -n "$still" ]; then
            echo "Port $UVICORN_PORT still bound after SIGTERM, sending SIGKILL to: $still"
            kill -9 $still 2>/dev/null || true
            for i in 1 2 3 4; do
                sleep 0.5
                if [ -z "$(lsof -tiTCP:$UVICORN_PORT -sTCP:LISTEN 2>/dev/null || true)" ]; then
                    return 0
                fi
            done
        fi
    done
    local final
    final=$(lsof -tiTCP:$UVICORN_PORT -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$final" ]; then
        echo "ERROR: Port $UVICORN_PORT is still held by PID(s): $final after two cleanup rounds." >&2
        echo "       To unblock manually: kill -9 $final && lsof -iTCP:$UVICORN_PORT" >&2
        echo "       If lsof shows nothing, the kernel may still be holding the socket in TIME_WAIT." >&2
        echo "       Wait 30 seconds and try again, or pick a different port via UVICORN_PORT env var." >&2
        return 1
    fi
    return 0
}

if ! free_port_or_die; then
    exit 1
fi

cd "$API_DIR"

# Load local env overrides (e.g. GOOGLE_CLIENT_ID for OAuth) if present.
if [ -f "$HOME/.youros/.env" ]; then
    set -a
    source "$HOME/.youros/.env"
    set +a
fi

# shellcheck source=/dev/null
source .venv/bin/activate

# Auto-recovery watchdog: launch a sibling process that polls /api/health
# every 30 seconds and re-runs this script if the backend goes unreachable.
# Disable by setting MYOS_NO_WATCHDOG=1 (e.g. inside test harnesses or when
# running under a process supervisor that already restarts on exit).
WATCHDOG_SCRIPT="$SCRIPT_DIR/backend_watchdog.sh"
if [ "${MYOS_NO_WATCHDOG:-0}" != "1" ] && [ -x "$WATCHDOG_SCRIPT" ]; then
    # Only start the watchdog if one is not already running. The watchdog
    # writes its pid to /tmp/youros-backend-watchdog.pid; if that file exists
    # and the pid is still alive, leave it alone.
    _wd_pidfile="/tmp/youros-backend-watchdog.pid"
    _wd_running=0
    if [ -f "$_wd_pidfile" ]; then
        _wd_pid=$(cat "$_wd_pidfile" 2>/dev/null || true)
        if [ -n "$_wd_pid" ] && ps -p "$_wd_pid" >/dev/null 2>&1; then
            _wd_running=1
        fi
    fi
    if [ "$_wd_running" = "0" ]; then
        nohup "$WATCHDOG_SCRIPT" >> /tmp/youros-backend-watchdog.log 2>&1 &
        echo $! > "$_wd_pidfile"
        echo "Backend watchdog started (pid $(cat "$_wd_pidfile"), log /tmp/youros-backend-watchdog.log)"
    fi
fi

# SSL: if self-signed certs exist, serve HTTPS so Slack OAuth works.
SSL_KEY="$HOME/.youros/localhost.key"
SSL_CERT="$HOME/.youros/localhost.crt"
SSL_ARGS=""
if [ -f "$SSL_KEY" ] && [ -f "$SSL_CERT" ]; then
    SSL_ARGS="--ssl-keyfile $SSL_KEY --ssl-certfile $SSL_CERT"
    echo "SSL enabled (self-signed cert at $SSL_CERT)"
fi

# RELEASE_MODE: when set to "1", start uvicorn with reload DISABLED.
# This is the simplest reliable answer to the watchfiles thrash loop
# observed on services/task_audit.py. Root cause notes below.
#
# What we saw: with --reload on, watchfiles kept reporting
# services/task_audit.py as changed even though nothing was editing
# it. Every reload cycle took 1 to 2 seconds and the backend never
# settled long enough for scripts/e2e_smoke.sh to reach phase 4 live
# HTTP. The release gate could not pass.
#
# Why task_audit.py specifically: the file has macOS extended
# attributes (the "@" in `ls -l@`). Spotlight's mdworker and similar
# background indexers bump mtime on files with xattr updates even
# when the file contents have not changed. Once watchfiles saw one
# change it triggered a reload, the reload imported the file again
# which refreshed xattrs, which looked like another change, and the
# loop fed itself. Atomic writes by background agents (pytest, ostk
# audit's grep, atomic_io) made the pattern worse but task_audit.py
# was the anchor because it is imported at module load time AND has
# sticky xattrs from a prior download or curl.
#
# Fix (simplest reliable answer, per the release punch list):
# - When RELEASE_MODE=1 is set, do not pass --reload at all. The
#   e2e smoke script exports RELEASE_MODE=1 before spawning the
#   backend so verification runs against a stable, non-cycling
#   server. No reload means no watchfiles means no thrash loop.
# - In normal dev mode (RELEASE_MODE unset), we keep --reload with
#   the tighter excludes and 10.0s coalesce window added in needle
#   →301. Dev iteration still works, release gate still passes.
#
# The reload watch is scoped to api/ runtime code only. Test files,
# pytest cache, pyc files, and atomic-write ``.tmp`` files must NOT
# trigger a reload. Background agents edit them frequently and a
# reload mid-stream kills in-flight chat WebSockets. See
# feedback_uvicorn_reload_scope.md.
#
# Needle 301: --reload-delay was 1.0s, then 5.0s, now 10.0s. When a
# background agent edits 3 to 5 backend files in a burst (a normal
# diagnose run) the server cycled once per file, each cycle taking
# 1 to 2 seconds of shutdown and startup. External curls timed out
# for 10 to 20 seconds while Tori's browser dots went red. The 5.0s
# bump helped but watchfiles still thrashed on single-file edits
# because atomic writes, vim swap files, and .DS_Store touches all
# fired change events that slipped past the exclude globs. Bumping
# the delay to 10.0s gives watchfiles a bigger coalesce window so
# the e2e smoke test (scripts/e2e_smoke.sh) can reach phase 4 live
# HTTP without the backend cycling mid-request. Trade: a manual
# edit takes up to 10 seconds to pick up instead of 5. Worth it
# because the release gate was blocked on this.
#
# Exclude globs use fnmatch-style patterns relative to the watched
# dir. Belt-and-braces: cover swap files, DS_Store, backup files,
# and .git noise explicitly so watchfiles never sees them.
# Write PID to pidfile right before exec. `exec` replaces this shell with
# uvicorn, so the pid in PIDFILE is the live uvicorn reloader parent.
# backend_watchdog.sh reads this file and uses `kill -0` to verify the
# backend is actually alive before deciding to restart.
echo $$ > "$PIDFILE"

# Keep LAUNCHER_LOCK held through exec. After exec, the shell PID becomes
# the uvicorn PID — the same value written to PIDFILE. A concurrent
# launcher that races in before uvicorn binds the port will see that
# LAUNCHER_LOCK contains the PIDFILE PID, wait for the port to be bound
# (up to 10s in acquire_launcher_lock), then clear the lock and proceed
# with kill-and-replace. This closes the SO_REUSEPORT double-bind window
# that existed when the lock was released before uvicorn finished binding.
#
# We still clear the EXIT trap so the shell cleanup does not fire if exec
# fails and bash falls through to an unexpected exit path.
trap - EXIT INT TERM

# Install the passive-claim post-commit hook (→1427) idempotently.
"$SCRIPT_DIR/install-post-commit-hook.sh" 2>/dev/null || true

if [ "${RELEASE_MODE:-0}" = "1" ]; then
    echo "RELEASE_MODE=1 detected. Starting uvicorn with reload DISABLED."
    echo "This prevents the watchfiles thrash loop during release verification."
    exec uvicorn main:app \
        --host 127.0.0.1 \
        --port $UVICORN_PORT \
        --workers 1 \
        --no-access-log \
        $SSL_ARGS
fi

exec uvicorn main:app \
    --host 127.0.0.1 \
    --port $UVICORN_PORT \
    --reload \
    --reload-dir "$API_DIR" \
    --reload-exclude 'api/tests/*' \
    --reload-exclude 'api/tests/**/*' \
    --reload-exclude 'tests/*' \
    --reload-exclude 'tests/**/*' \
    --reload-exclude '**/.pytest_cache/*' \
    --reload-exclude '**/__pycache__/*' \
    --reload-exclude '**/*.pyc' \
    --reload-exclude '**/*.tmp' \
    --reload-exclude '**/.DS_Store' \
    --reload-exclude '**/*.swp' \
    --reload-exclude '**/*.swo' \
    --reload-exclude '**/*~' \
    --reload-exclude '**/.git/*' \
    --reload-exclude '**/.git/**/*' \
    --reload-exclude 'routers/agents.py' \
    --reload-delay 10.0 \
    --no-access-log \
    $SSL_ARGS
