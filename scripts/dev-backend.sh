#!/bin/bash
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

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
API_DIR="$REPO_DIR/api"
UVICORN_PORT=8000

if [ ! -f "$API_DIR/.venv/bin/activate" ]; then
    echo "Python virtualenv not found at $API_DIR/.venv. Run install.sh first." >&2
    exit 1
fi

# Free port 8000 if a stale worker is still listening.
stale_pids=$(lsof -tiTCP:$UVICORN_PORT -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$stale_pids" ]; then
    echo "Freeing port $UVICORN_PORT from stale listener(s): $stale_pids"
    kill $stale_pids 2>/dev/null || true
    sleep 1
    still=$(lsof -tiTCP:$UVICORN_PORT -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$still" ]; then
        kill -9 $still 2>/dev/null || true
        sleep 1
    fi
fi

cd "$API_DIR"
# shellcheck source=/dev/null
source .venv/bin/activate

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
if [ "${RELEASE_MODE:-0}" = "1" ]; then
    echo "RELEASE_MODE=1 detected. Starting uvicorn with reload DISABLED."
    echo "This prevents the watchfiles thrash loop during release verification."
    exec uvicorn main:app \
        --host 127.0.0.1 \
        --port $UVICORN_PORT \
        --no-access-log
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
    --reload-delay 10.0 \
    --no-access-log
