#!/bin/bash
# Unit test for standing-rules.sh live agent snapshot block.
#
# Covers:
#   1. Happy path: canned /api/agents payload. Confirm the two user-spawned
#      claude-code agents appear, confirm a main-session row (name starts with
#      'claude-code-') is filtered out, confirm a completed row is filtered out,
#      confirm the STANDING RULES block is always present.
#   2. Backend-down path: kill the canned server, confirm stdout still contains
#      the STANDING RULES block AND a graceful "couldn't reach myOS backend"
#      note, and no bash traceback / error bomb.
#
# Hard budget: 20 seconds.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../standing-rules.sh"
PORT=18743
BACKEND_URL="http://127.0.0.1:${PORT}"
FIXTURE_DIR="$(mktemp -d -t standing-rules-test.XXXXXX)"
FIXTURE="${FIXTURE_DIR}/agents.json"
SERVER_PID=""
FAILED=0

STAMP_HOME=""
cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$FIXTURE_DIR"
  if [ -n "$STAMP_HOME" ] && [ -d "$STAMP_HOME" ]; then
    rm -rf "$STAMP_HOME"
  fi
}
trap cleanup EXIT

fail() {
  echo "FAIL: $1"
  FAILED=1
}

pass() {
  echo "  pass: $1"
}

# --- Build fixture payload ---
# 2 user-spawned claude-code agents (running): should appear.
# 1 main-session row (name prefix 'claude-code-', running): should be filtered.
# 1 completed row: should be filtered.
NOW_ISO=$(python3 -c "from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())")
TWO_MIN_AGO=$(python3 -c "from datetime import datetime, timezone, timedelta; print((datetime.now(timezone.utc) - timedelta(seconds=125)).isoformat())")
FORTY_FIVE_S_AGO=$(python3 -c "from datetime import datetime, timezone, timedelta; print((datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat())")
THIRTY_S_AGO=$(python3 -c "from datetime import datetime, timezone, timedelta; print((datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat())")
TEN_S_AGO=$(python3 -c "from datetime import datetime, timezone, timedelta; print((datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat())")

cat > "$FIXTURE" <<JSON
{
  "agents": [
    {
      "name": "diagnose-alpha",
      "source": "claude-code",
      "status": "running",
      "spawned_at": "${TWO_MIN_AGO}",
      "transcript_bytes": 125952
    },
    {
      "name": "fix-beta",
      "source": "claude-code",
      "status": "running",
      "spawned_at": "${FORTY_FIVE_S_AGO}",
      "transcript_bytes": 89000
    },
    {
      "name": "claude-code-abcd1234",
      "source": "claude-code",
      "status": "running",
      "spawned_at": "${NOW_ISO}",
      "transcript_bytes": 200
    },
    {
      "name": "diagnose-gamma-done",
      "source": "claude-code",
      "status": "completed",
      "spawned_at": "${TWO_MIN_AGO}",
      "completed_at": "${THIRTY_S_AGO}",
      "transcript_bytes": 400,
      "summary": "landed fix and committed abcd123"
    },
    {
      "name": "files-location-died",
      "source": "claude-code",
      "status": "terminated_stale",
      "spawned_at": "${TWO_MIN_AGO}",
      "completed_at": "${TEN_S_AGO}",
      "transcript_bytes": 900
    }
  ]
}
JSON

# --- Start canned backend ---
# Serve /api/agents -> fixture. Anything else -> 404.
python3 - "$PORT" "$FIXTURE" <<'PYSERVER' &
import sys, http.server
PORT = int(sys.argv[1])
FIXTURE = sys.argv[2]

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/agents"):
            with open(FIXTURE, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *a, **kw):
        return

http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
PYSERVER
SERVER_PID=$!

# Wait up to 3s for server to be ready.
for _ in $(seq 1 30); do
  if curl -sS --connect-timeout 1 -m 1 "${BACKEND_URL}/api/agents" -o /dev/null 2>/dev/null; then
    break
  fi
  sleep 0.1
done

# --- Test 1: happy path ---
echo "test 1: running snapshot with canned backend"
# Isolate the completions stamp so prior runs don't suppress the block.
# Capture the real HUMANFILE path BEFORE we override HOME below: bash
# evaluates command-prefix env assignments left-to-right, so
# `HOME=$STAMP_HOME MYOS_HUMANFILE_PATH="${HOME}/..."` sees the already
# overridden HOME and points at the empty stamp directory.
STAMP_HOME=$(mktemp -d -t standing-rules-stamp.XXXXXX)
REAL_HUMANFILE="${HOME}/.ostk/HUMANFILE"
OUT_HAPPY=$(HOME="$STAMP_HOME" MYOS_HUMANFILE_PATH="$REAL_HUMANFILE" MYOS_BACKEND_URL="$BACKEND_URL" bash "$HOOK" 2>&1)

# STANDING RULES block preserved.
if echo "$OUT_HAPPY" | grep -q "STANDING RULES (non-negotiable this turn):"; then
  pass "STANDING RULES block present"
else
  fail "STANDING RULES block missing"
fi

# ACTIVE HUMANFILE RULES block present (re-injected every turn).
if echo "$OUT_HAPPY" | grep -q "ACTIVE HUMANFILE RULES (read every turn):"; then
  pass "ACTIVE HUMANFILE RULES block present"
else
  fail "ACTIVE HUMANFILE RULES block missing"
fi

# Spot check: a high-signal HUMANFILE rule made it through.
if echo "$OUT_HAPPY" | grep -q "saa = spawn for everything"; then
  pass "curated HUMANFILE rule rendered"
else
  fail "curated HUMANFILE rule missing"
fi

# Both user-spawned agents appear.
if echo "$OUT_HAPPY" | grep -q "diagnose-alpha"; then
  pass "diagnose-alpha listed"
else
  fail "diagnose-alpha not listed"
fi
if echo "$OUT_HAPPY" | grep -q "fix-beta"; then
  pass "fix-beta listed"
else
  fail "fix-beta not listed"
fi

# Main-session row filtered out.
if echo "$OUT_HAPPY" | grep -q "claude-code-abcd1234"; then
  fail "main-session row 'claude-code-abcd1234' should have been filtered"
else
  pass "main-session row filtered"
fi

# Completed row filtered out of the RUNNING list. Must not appear as
# "- diagnose-gamma-done (spawned ...)" which is the running-format prefix.
# It will appear in the separate COMPLETED block below, which is fine.
if echo "$OUT_HAPPY" | grep -qE "^- diagnose-gamma-done \(spawned"; then
  fail "completed row 'diagnose-gamma-done' should be filtered from running list"
else
  pass "completed row filtered from running list"
fi

# Snapshot header present.
if echo "$OUT_HAPPY" | grep -q "CURRENT RUNNING AGENTS"; then
  pass "snapshot header present"
else
  fail "snapshot header missing"
fi

# --- Completion-surfacing assertions ---
# New block: agents that finished without a native task-notification must
# now appear in a separate "AGENTS THAT FINISHED SINCE YOUR LAST TURN"
# block so the parent session is forced to reconcile.
if echo "$OUT_HAPPY" | grep -q "AGENTS THAT FINISHED SINCE YOUR LAST TURN"; then
  pass "completions block header present"
else
  fail "completions block header missing"
fi

if echo "$OUT_HAPPY" | grep -q "diagnose-gamma-done"; then
  pass "completed-status row surfaced in completions block"
else
  fail "completed-status row missing from completions block"
fi

if echo "$OUT_HAPPY" | grep -q "files-location-died"; then
  pass "terminated_stale row surfaced (simulates agent that died silently)"
else
  fail "terminated_stale row missing (the core bug we are fixing)"
fi

# Status label should distinguish auto-closed rows so the narrator knows
# to treat them differently from clean completions.
if echo "$OUT_HAPPY" | grep -q "auto-closed"; then
  pass "terminated_stale row labelled 'auto-closed' for the narrator"
else
  fail "terminated_stale row not labelled auto-closed"
fi

# Highwater stamp should be written so the next turn does not re-surface.
if [ -s "$STAMP_HOME/.myos/subagents/last-seen-completions.stamp" ]; then
  pass "highwater stamp written"
else
  fail "highwater stamp not written (next turn will re-surface these)"
fi

# Second run with the same stamp must NOT re-surface the block.
OUT_SECOND=$(HOME="$STAMP_HOME" MYOS_HUMANFILE_PATH="$REAL_HUMANFILE" MYOS_BACKEND_URL="$BACKEND_URL" bash "$HOOK" 2>&1)
if echo "$OUT_SECOND" | grep -q "AGENTS THAT FINISHED SINCE YOUR LAST TURN"; then
  fail "completions block re-surfaced after highwater bump (would spam every turn)"
else
  pass "completions block suppressed on second run (idempotent per completion)"
fi

# --- Test 2: backend down ---
echo "test 2: graceful degradation when backend is unreachable"
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
SERVER_PID=""

OUT_DOWN=$(MYOS_BACKEND_URL="$BACKEND_URL" bash "$HOOK" 2>&1)

if echo "$OUT_DOWN" | grep -q "STANDING RULES (non-negotiable this turn):"; then
  pass "STANDING RULES block present when backend is down"
else
  fail "STANDING RULES block missing when backend is down"
fi

if echo "$OUT_DOWN" | grep -q "ACTIVE HUMANFILE RULES"; then
  pass "ACTIVE HUMANFILE RULES block present when backend is down"
else
  fail "ACTIVE HUMANFILE RULES block missing when backend is down"
fi

if echo "$OUT_DOWN" | grep -q "couldn't reach myOS backend"; then
  pass "graceful backend-down note present"
else
  fail "graceful backend-down note missing"
fi

# No traceback leaked. Use whole-word boundaries so benign words in the
# HUMANFILE excerpt (e.g. "No exceptions") don't trip the check.
if echo "$OUT_DOWN" | grep -qE "Traceback \(most recent|Error: |Exception: |bash:.*:.*error|curl:.*error"; then
  fail "error bomb leaked when backend is down"
else
  pass "no error bomb when backend is down"
fi

# --- Result ---
if [ "$FAILED" -eq 0 ]; then
  echo "PASS"
  exit 0
else
  echo "FAILED"
  exit 1
fi
