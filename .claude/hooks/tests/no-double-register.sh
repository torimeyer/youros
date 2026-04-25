#!/bin/bash
# Regression test: the Agent PreToolUse register hook must be wired
# EXACTLY ONCE, in the user-global ~/.claude/settings.json, and NOT
# in the project-local .claude/settings.json. If both match, Claude
# Code fires the hook twice per Task spawn and the /register POST
# duplicates rows (or 409s and we lose the bg-flag handoff).
#
# Fails if:
#   - ~/.claude/settings.json has zero PreToolUse matcher=Agent entries
#   - project .claude/settings.json has a PreToolUse matcher=Agent entry
#     pointing at register-agent.sh
#
# Usage: bash .claude/hooks/tests/no-double-register.sh

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PROJECT_SETTINGS="$REPO_ROOT/.claude/settings.json"
GLOBAL_SETTINGS="$HOME/.claude/settings.json"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

if [ ! -f "$PROJECT_SETTINGS" ]; then
    fail "project settings.json not found: $PROJECT_SETTINGS"
fi
if [ ! -f "$GLOBAL_SETTINGS" ]; then
    fail "global settings.json not found: $GLOBAL_SETTINGS (run scripts/install-claude-hooks.sh)"
fi

PROJECT_AGENT_COUNT=$(PATH_JSON="$PROJECT_SETTINGS" python3 <<'PY'
import json, os, sys
path = os.environ["PATH_JSON"]
try:
    with open(path) as f:
        d = json.load(f)
except Exception as e:
    sys.stderr.write(f"parse error: {e}\n")
    sys.exit(2)
pretool = (d.get("hooks") or {}).get("PreToolUse") or []
n = 0
for e in pretool:
    if not isinstance(e, dict):
        continue
    if e.get("matcher") != "Agent":
        continue
    for h in e.get("hooks") or []:
        if isinstance(h, dict) and "register-agent.sh" in (h.get("command") or ""):
            n += 1
print(n)
PY
)

GLOBAL_AGENT_COUNT=$(PATH_JSON="$GLOBAL_SETTINGS" python3 <<'PY'
import json, os, sys
path = os.environ["PATH_JSON"]
try:
    with open(path) as f:
        d = json.load(f)
except Exception as e:
    sys.stderr.write(f"parse error: {e}\n")
    sys.exit(2)
pretool = (d.get("hooks") or {}).get("PreToolUse") or []
n = 0
for e in pretool:
    if not isinstance(e, dict):
        continue
    if e.get("matcher") != "Agent":
        continue
    for h in e.get("hooks") or []:
        if isinstance(h, dict) and "register-agent.sh" in (h.get("command") or ""):
            n += 1
print(n)
PY
)

if [ "$PROJECT_AGENT_COUNT" != "0" ]; then
    fail "project .claude/settings.json has $PROJECT_AGENT_COUNT Agent register entries; expected 0 (must be global-only to prevent double-fire)"
fi

if [ "$GLOBAL_AGENT_COUNT" != "1" ]; then
    fail "global ~/.claude/settings.json has $GLOBAL_AGENT_COUNT Agent register entries; expected exactly 1"
fi

printf 'PASS: Agent register hook wired exactly once (global only)\n'

# --- Behavioral tests (→922 Bug A) ------------------------------------
# Spin up a mock /api/agents/register server, feed register-agent.sh
# real inputs, and assert exactly one registration per Task call.
#
# Test 1: edit-verb prompt  → bridge guard fires → 0 registrations
# Test 2: read-only prompt  → guard skips        → 1 registration
# Test 3: bridge disabled + edit-verb            → 1 registration (fall-through)

MOCK_TMP=$(mktemp -d)
MOCK_PORT=18931
REGISTER_LOG="$MOCK_TMP/register.log"
touch "$REGISTER_LOG"

cat <<'PYEOF' | python3 - "$MOCK_PORT" "$REGISTER_LOG" &
import sys, http.server

port = int(sys.argv[1])
log_path = sys.argv[2]

class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        self.rfile.read(length)
        with open(log_path, 'a') as f:
            f.write(self.path + '\n')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"result":"ok"}')
    def log_message(self, *args): pass

http.server.HTTPServer(('127.0.0.1', port), H).serve_forever()
PYEOF
MOCK_PID=$!

cleanup_mock() {
    kill "$MOCK_PID" 2>/dev/null || true
    rm -rf "$MOCK_TMP"
}
trap cleanup_mock EXIT

# Give the server time to bind
sleep 0.4

# Clear any stale pending-register queue so drain-pending.sh doesn't
# replay old failures and inflate the count for these tests.
> "$HOME/.myos/subagents/pending-register.jsonl" 2>/dev/null || true

MOCK_BASE="http://127.0.0.1:$MOCK_PORT"
REGISTER_HOOK="$HOME/.claude/hooks/register-agent.sh"

count_registrations() {
    local n
    n=$(grep -c '^/api/agents/register$' "$REGISTER_LOG" 2>/dev/null) || n=0
    printf '%s' "$n"
}

# Test 1: edit-verb prompt → bridge guard → no registration
> "$REGISTER_LOG"
printf '{"tool_name":"Agent","tool_input":{"description":"fix the null pointer in main.py","prompt":"fix the bug and commit"}}\n' \
    | TORIOS_API_BASE="$MOCK_BASE" bash "$REGISTER_HOOK" 2>/dev/null
N=$(count_registrations)
if [ "$N" != "0" ]; then
    fail "edit-verb prompt: expected 0 registrations, got $N (bridge guard not firing)"
fi
printf 'PASS: edit-verb prompt -> 0 registrations (bridge guard active)\n'

# Test 2: read-only prompt → no edit verb → 1 registration
> "$REGISTER_LOG"
printf '{"tool_name":"Agent","tool_input":{"description":"search the codebase","prompt":"read the logs and summarize findings"}}\n' \
    | TORIOS_API_BASE="$MOCK_BASE" bash "$REGISTER_HOOK" 2>/dev/null
N=$(count_registrations)
if [ "$N" != "1" ]; then
    fail "read-only prompt: expected 1 registration, got $N"
fi
printf 'PASS: read-only prompt -> 1 registration\n'

# Test 3: bridge disabled + edit-verb → 1 registration (fall-through)
> "$REGISTER_LOG"
printf '{"tool_name":"Agent","tool_input":{"description":"fix the bug","prompt":"edit the config file"}}\n' \
    | TORIOS_API_BASE="$MOCK_BASE" TASK_ISOLATION_BRIDGE_DISABLE=1 bash "$REGISTER_HOOK" 2>/dev/null
N=$(count_registrations)
if [ "$N" != "1" ]; then
    fail "bridge-disabled edit-verb: expected 1 registration, got $N (should fall through when bridge is off)"
fi
printf 'PASS: bridge-disabled edit-verb -> 1 registration (fall-through)\n'

printf 'PASS: all behavioral tests passed\n'
exit 0
