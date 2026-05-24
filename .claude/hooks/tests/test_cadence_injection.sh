#!/bin/bash
# Unit test for standing-rules.sh cadence injection block.
#
# Covers:
#   1. Normal mode (60s target): hook emits CADENCE tag with elapsed seconds.
#   2. ADHD mode (30s target): target changes to 30s, label shows "(ADHD mode)".
#   3. OVERDUE: elapsed > target triggers the "OVERDUE" warning suffix.
#   4. Missing JSONL: cadence block silently absent (no crash, STANDING RULES still emitted).
#   5. No CLAUDE_SESSION_ID: cadence block silently absent.
#   6. snapshot-safe: when env vars absent, snapshot-tested blocks still present.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../standing-rules.sh"
FAILED=0

WORK_DIR=$(mktemp -d -t cadence-test.XXXXXX)
trap 'rm -rf "$WORK_DIR"' EXIT

fail() { echo "FAIL: $1"; FAILED=1; }
pass() { echo "  pass: $1"; }

# Shared: fake canned backend that returns empty agents list so the
# snapshot sections don't error. Re-used across all sub-tests.
PORT=19843
BACKEND_URL="http://127.0.0.1:${PORT}"
EMPTY_AGENTS='{"agents": []}'
SERVER_PID=""

start_server() {
    python3 - "$PORT" "$EMPTY_AGENTS" <<'PYSERVER' &
import sys, http.server
PORT = int(sys.argv[1])
BODY = sys.argv[2].encode()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)
    def log_message(self, *a, **kw):
        return
http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
PYSERVER
    SERVER_PID=$!
    for _ in $(seq 1 30); do
        if curl -sS --connect-timeout 1 -m 1 "${BACKEND_URL}/api/agents" -o /dev/null 2>/dev/null; then break; fi
        sleep 0.1
    done
}

stop_server() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    SERVER_PID=""
}

# Build a fake project tree with a session JSONL.
FAKE_SESSION_ID="test-session-cadence-abc123"
FAKE_PROJECT_DIR="${WORK_DIR}/fake-project"
FAKE_PROJECT_KEY=$(printf '%s' "$FAKE_PROJECT_DIR" | tr '/' '-')
FAKE_HOME="${WORK_DIR}/home"
FAKE_JSONL_DIR="${FAKE_HOME}/.claude/projects/${FAKE_PROJECT_KEY}"
mkdir -p "$FAKE_JSONL_DIR"
FAKE_JSONL="${FAKE_JSONL_DIR}/${FAKE_SESSION_ID}.jsonl"

# Tori config for HUMANFILE rendering.
FAKE_MYOS="${FAKE_HOME}/.myos"
mkdir -p "$FAKE_MYOS"
FAKE_CONFIG="${FAKE_MYOS}/config.json"
echo '{"enable_tori_rules": true}' > "$FAKE_CONFIG"
REAL_HUMANFILE="${HOME}/.ostk/HUMANFILE"

# Helper: write a JSONL with an assistant message N seconds ago.
write_jsonl_with_last_assistant() {
    local secs_ago=$1
    local ts
    ts=$(python3 -c "
from datetime import datetime, timezone, timedelta
t = datetime.now(timezone.utc) - timedelta(seconds=$secs_ago)
print(t.strftime('%Y-%m-%dT%H:%M:%S.000Z'))
")
    printf '{"type":"user","timestamp":"2026-01-01T00:00:00.000Z","sessionId":"%s"}\n' "$FAKE_SESSION_ID" > "$FAKE_JSONL"
    printf '{"type":"assistant","timestamp":"%s","sessionId":"%s"}\n' "$ts" "$FAKE_SESSION_ID" >> "$FAKE_JSONL"
    printf '{"type":"last-prompt","sessionId":"%s"}\n' "$FAKE_SESSION_ID" >> "$FAKE_JSONL"
}

start_server

# ---- Test 1: normal mode, 45s elapsed, 60s target, not overdue ----
echo "test 1: normal mode (60s target, 45s elapsed, not overdue)"
write_jsonl_with_last_assistant 45
OUT=$(HOME="$FAKE_HOME" \
      MYOS_CONFIG_PATH="$FAKE_CONFIG" \
      MYOS_HUMANFILE_PATH="$REAL_HUMANFILE" \
      MYOS_ADHD_MODE_FILE="${FAKE_MYOS}/.adhd_mode_absent" \
      CLAUDE_SESSION_ID="$FAKE_SESSION_ID" \
      CLAUDE_PROJECT_DIR="$FAKE_PROJECT_DIR" \
      MYOS_BACKEND_URL="$BACKEND_URL" \
      bash "$HOOK" 2>&1)

if echo "$OUT" | grep -q "CADENCE:"; then
    pass "CADENCE tag emitted"
else
    fail "CADENCE tag missing"
fi
if echo "$OUT" | grep -qE "target: 60s\b"; then
    pass "target is 60s in normal mode"
else
    fail "target is not 60s in normal mode"
fi
if echo "$OUT" | grep -q "ADHD mode"; then
    fail "ADHD mode label present in normal mode"
else
    pass "no ADHD mode label in normal mode"
fi
if echo "$OUT" | grep -q "OVERDUE"; then
    fail "OVERDUE present but elapsed (45s) < target (60s)"
else
    pass "no OVERDUE when not overdue"
fi

# ---- Test 2: ADHD mode, 45s elapsed, 30s target, overdue ----
echo "test 2: ADHD mode (30s target, 45s elapsed, overdue)"
write_jsonl_with_last_assistant 45
touch "${FAKE_MYOS}/.adhd_mode"
OUT=$(HOME="$FAKE_HOME" \
      MYOS_CONFIG_PATH="$FAKE_CONFIG" \
      MYOS_HUMANFILE_PATH="$REAL_HUMANFILE" \
      MYOS_ADHD_MODE_FILE="${FAKE_MYOS}/.adhd_mode" \
      CLAUDE_SESSION_ID="$FAKE_SESSION_ID" \
      CLAUDE_PROJECT_DIR="$FAKE_PROJECT_DIR" \
      MYOS_BACKEND_URL="$BACKEND_URL" \
      bash "$HOOK" 2>&1)

if echo "$OUT" | grep -q "ADHD mode"; then
    pass "ADHD mode label present"
else
    fail "ADHD mode label missing"
fi
if echo "$OUT" | grep -qE "target: 35s"; then
    pass "target is 35s in ADHD mode (settings fallback)"
else
    fail "target is not 35s in ADHD mode (got: $(echo "$OUT" | grep target))"
fi
if echo "$OUT" | grep -q "OVERDUE"; then
    pass "OVERDUE emitted when elapsed > target"
else
    fail "OVERDUE missing when elapsed (45s) > target (30s)"
fi
rm -f "${FAKE_MYOS}/.adhd_mode"

# ---- Test 3: ADHD mode, 10s elapsed, 30s target, not overdue ----
echo "test 3: ADHD mode, within target"
write_jsonl_with_last_assistant 10
touch "${FAKE_MYOS}/.adhd_mode"
OUT=$(HOME="$FAKE_HOME" \
      MYOS_CONFIG_PATH="$FAKE_CONFIG" \
      MYOS_HUMANFILE_PATH="$REAL_HUMANFILE" \
      MYOS_ADHD_MODE_FILE="${FAKE_MYOS}/.adhd_mode" \
      CLAUDE_SESSION_ID="$FAKE_SESSION_ID" \
      CLAUDE_PROJECT_DIR="$FAKE_PROJECT_DIR" \
      MYOS_BACKEND_URL="$BACKEND_URL" \
      bash "$HOOK" 2>&1)

if echo "$OUT" | grep -q "CADENCE:"; then
    pass "CADENCE tag emitted"
else
    fail "CADENCE tag missing in ADHD mode within target"
fi
if echo "$OUT" | grep -q "OVERDUE"; then
    fail "OVERDUE present but elapsed (10s) < target (30s)"
else
    pass "no OVERDUE when within target"
fi
rm -f "${FAKE_MYOS}/.adhd_mode"

# ---- Test 4: missing JSONL → cadence silently absent ----
echo "test 4: missing JSONL, cadence silently absent"
rm -f "$FAKE_JSONL"
OUT=$(HOME="$FAKE_HOME" \
      MYOS_CONFIG_PATH="$FAKE_CONFIG" \
      MYOS_HUMANFILE_PATH="$REAL_HUMANFILE" \
      MYOS_ADHD_MODE_FILE="${FAKE_MYOS}/.adhd_mode_absent" \
      CLAUDE_SESSION_ID="$FAKE_SESSION_ID" \
      CLAUDE_PROJECT_DIR="$FAKE_PROJECT_DIR" \
      MYOS_BACKEND_URL="$BACKEND_URL" \
      bash "$HOOK" 2>&1)

if echo "$OUT" | grep -q "CADENCE:"; then
    fail "CADENCE tag present despite missing JSONL"
else
    pass "CADENCE tag absent when JSONL missing"
fi
if echo "$OUT" | grep -q "STANDING RULES (non-negotiable this turn):"; then
    pass "STANDING RULES block still present"
else
    fail "STANDING RULES block missing"
fi

# ---- Test 5: no CLAUDE_SESSION_ID → cadence silently absent ----
echo "test 5: no CLAUDE_SESSION_ID, cadence silently absent"
write_jsonl_with_last_assistant 90
OUT=$(HOME="$FAKE_HOME" \
      MYOS_CONFIG_PATH="$FAKE_CONFIG" \
      MYOS_HUMANFILE_PATH="$REAL_HUMANFILE" \
      MYOS_ADHD_MODE_FILE="${FAKE_MYOS}/.adhd_mode_absent" \
      MYOS_BACKEND_URL="$BACKEND_URL" \
      bash "$HOOK" 2>&1)

if echo "$OUT" | grep -q "CADENCE:"; then
    fail "CADENCE tag present without CLAUDE_SESSION_ID"
else
    pass "CADENCE tag absent when session ID missing"
fi

# ---- Test 6: elapsed tag contains an integer seconds value ----
echo "test 6: CADENCE tag has integer elapsed seconds"
write_jsonl_with_last_assistant 75
OUT=$(HOME="$FAKE_HOME" \
      MYOS_CONFIG_PATH="$FAKE_CONFIG" \
      MYOS_HUMANFILE_PATH="$REAL_HUMANFILE" \
      MYOS_ADHD_MODE_FILE="${FAKE_MYOS}/.adhd_mode_absent" \
      CLAUDE_SESSION_ID="$FAKE_SESSION_ID" \
      CLAUDE_PROJECT_DIR="$FAKE_PROJECT_DIR" \
      MYOS_BACKEND_URL="$BACKEND_URL" \
      bash "$HOOK" 2>&1)

if echo "$OUT" | grep -qE "\[CADENCE: [0-9]+s since last reply"; then
    pass "CADENCE tag contains integer seconds"
else
    fail "CADENCE tag format wrong: $(echo "$OUT" | grep CADENCE)"
fi

# ---- Test 7: ADHD mode + settings.json check_in_seconds=35 → target 35s + depth-probe rule ----
echo "test 7: ADHD mode + settings.json check_in_seconds=35 → target 35s and depth-probe rule"
FAKE_SETTINGS="${FAKE_MYOS}/settings.json"
printf '{"adhd_mode": {"enabled": true, "check_in_seconds": 35, "focus_mode": true}}\n' > "$FAKE_SETTINGS"
touch "${FAKE_MYOS}/.adhd_mode"
write_jsonl_with_last_assistant 45
OUT=$(HOME="$FAKE_HOME" \
      MYOS_CONFIG_PATH="$FAKE_CONFIG" \
      MYOS_HUMANFILE_PATH="$REAL_HUMANFILE" \
      MYOS_ADHD_MODE_FILE="${FAKE_MYOS}/.adhd_mode" \
      MYOS_SETTINGS_PATH="$FAKE_SETTINGS" \
      CLAUDE_SESSION_ID="$FAKE_SESSION_ID" \
      CLAUDE_PROJECT_DIR="$FAKE_PROJECT_DIR" \
      MYOS_BACKEND_URL="$BACKEND_URL" \
      bash "$HOOK" 2>&1)

if echo "$OUT" | grep -qE "target: 35s"; then
    pass "target is 35s from settings.json"
else
    fail "target is not 35s (got: $(echo "$OUT" | grep target))"
fi
if echo "$OUT" | grep -q "ADHD DEPTH-PROBE RULE"; then
    pass "depth-probe rule injected when ADHD mode is on"
else
    fail "depth-probe rule missing when ADHD mode is on"
fi
rm -f "${FAKE_MYOS}/.adhd_mode" "$FAKE_SETTINGS"

# ---- Test 8: no ADHD mode → depth-probe rule NOT injected ----
echo "test 8: no ADHD mode → depth-probe rule absent"
write_jsonl_with_last_assistant 45
OUT=$(HOME="$FAKE_HOME" \
      MYOS_CONFIG_PATH="$FAKE_CONFIG" \
      MYOS_HUMANFILE_PATH="$REAL_HUMANFILE" \
      MYOS_ADHD_MODE_FILE="${FAKE_MYOS}/.adhd_mode_absent" \
      CLAUDE_SESSION_ID="$FAKE_SESSION_ID" \
      CLAUDE_PROJECT_DIR="$FAKE_PROJECT_DIR" \
      MYOS_BACKEND_URL="$BACKEND_URL" \
      bash "$HOOK" 2>&1)

if echo "$OUT" | grep -q "ADHD DEPTH-PROBE RULE"; then
    fail "depth-probe rule present when ADHD mode is off"
else
    pass "depth-probe rule absent when ADHD mode is off"
fi

stop_server

if [ "$FAILED" -eq 0 ]; then
    echo "PASS"
    exit 0
else
    echo "FAILED"
    exit 1
fi
