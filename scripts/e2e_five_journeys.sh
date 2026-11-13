#!/usr/bin/env bash
# Automated Five-Journey E2E release gate for yourOS v6 (->3097).
#
# Runs J1..J5 in-process FastAPI + isolated tmp_path git integration tests,
# writes full output to /tmp/e2e_five_journeys.log, and exits 0 on pass or 1 on fail.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="/tmp/e2e_five_journeys.log"
TMP_LOG="$(mktemp /tmp/e2e_five_journeys.XXXXXX.log)"
trap 'rm -f "$TMP_LOG"' EXIT INT TERM HUP

PYTHON_BIN="$REPO_ROOT/api/.venv/bin/python3.13"
if [ ! -x "$PYTHON_BIN" ] && [ -x "$REPO_ROOT/api/.venv/bin/python" ]; then
    PYTHON_BIN="$REPO_ROOT/api/.venv/bin/python"
fi
if [ ! -x "$PYTHON_BIN" ]; then
    COMMON_GIT_DIR="$(git -C "$REPO_ROOT" rev-parse --git-common-dir 2>/dev/null || true)"
    if [ -n "$COMMON_GIT_DIR" ]; then
        MAIN_REPO_ROOT="$(cd "$REPO_ROOT" && cd "$COMMON_GIT_DIR/.." 2>/dev/null && pwd || true)"
        if [ -n "$MAIN_REPO_ROOT" ] && [ -x "$MAIN_REPO_ROOT/api/.venv/bin/python3.13" ]; then
            PYTHON_BIN="$MAIN_REPO_ROOT/api/.venv/bin/python3.13"
        fi
    fi
fi

if [ ! -x "$PYTHON_BIN" ]; then
    msg="ERROR: Python venv binary not found at api/.venv/bin/python3.13"
    echo "$msg" > "$TMP_LOG"
    if [ ! /dev/stdout -ef "$LOG_FILE" ]; then
        cp "$TMP_LOG" "$LOG_FILE"
    fi
    cat "$TMP_LOG"
    exit 1
fi

(
    cd "$REPO_ROOT" && \
    PYTHONPATH="$REPO_ROOT/api${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m pytest -c api/pyproject.toml api/tests/test_five_core_journeys_e2e.py
) > "$TMP_LOG" 2>&1
rc=$?

if [ ! /dev/stdout -ef "$LOG_FILE" ]; then
    cp "$TMP_LOG" "$LOG_FILE"
fi
cat "$TMP_LOG"

if [ "$rc" -eq 0 ]; then
    exit 0
fi
exit 1
