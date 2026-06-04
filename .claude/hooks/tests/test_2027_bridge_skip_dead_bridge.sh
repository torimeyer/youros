#!/bin/bash
# Test (→2027): register-agent.sh registers when bridge hook is absent.
#
# When CLAUDE_PROJECT_DIR has no .claude/hooks/pre-agent-guard.sh,
# an edit-verb prompt must still hit /api/agents/register — not bridge-skip
# out silently. Without the fix, the bridge-skip guard fires on the edit verb
# and exits 0 without registering, leaving the agent invisible.
#
# Usage: bash .claude/hooks/tests/test_2027_bridge_skip_dead_bridge.sh
# Exit 0 on pass, nonzero on fail.

set -u

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)/register-agent.sh"
if [ ! -f "$HOOK" ]; then
    echo "FAIL: register-agent.sh not found at $HOOK" >&2
    exit 1
fi

PORT=$((22000 + RANDOM % 3000))
SCRATCH=$(mktemp -d)
HIT_LOG="$SCRATCH/hits.log"
: > "$HIT_LOG"
FAKE_PROJECT="$SCRATCH/fake_project"
mkdir -p "$FAKE_PROJECT/.claude"
# Intentionally do NOT create .claude/hooks/pre-agent-guard.sh

trap 'rm -rf "$SCRATCH"; [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

python3 - "$PORT" "$HIT_LOG" 2>/dev/null <<'PY' &
import http.server, socketserver, sys, threading
port = int(sys.argv[1]); hit_log = sys.argv[2]
lock = threading.Lock()
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b""
        with lock, open(hit_log, "ab") as f:
            f.write(self.path.encode() + b"\t" + body + b"\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"result":"ok"}')
    def log_message(self, *a, **k): pass
with socketserver.TCPServer(("127.0.0.1", port), H) as s:
    s.serve_forever()
PY
SERVER_PID=$!

for _ in 1 2 3 4 5; do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

INPUT=$(cat <<JSON
{"tool_name":"Agent","tool_input":{"description":"edit the readme","prompt":"Please edit README.md to add a new section about deployment."}}
JSON
)

# Run with CLAUDE_PROJECT_DIR pointing to a dir that has NO pre-agent-guard.sh.
# This simulates a non-torios project or a worktree where the bridge hook is absent.
echo "$INPUT" \
    | TORIOS_API_BASE="http://127.0.0.1:${PORT}" \
      CLAUDE_PROJECT_DIR="$FAKE_PROJECT" \
      bash "$HOOK" 2>/dev/null

if ! grep -q '/api/agents/register' "$HIT_LOG" 2>/dev/null; then
    echo "FAIL: edit-verb agent was bridge-skipped with no bridge present — never registered (→2027)" >&2
    echo "  Calls logged in hit log (should have /api/agents/register):" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

echo "PASS: edit-verb agent registered when pre-agent-guard.sh is absent (→2027)"

# ── Regression: with bridge WIRED in settings, bridge-skip still fires (no double-register) ──
# (→2027) The skip now keys on the hook being referenced in a settings.json,
# not merely present as a file. Wire pre-agent-guard into the project settings
# so _BRIDGE_WIRED is true and the skip is expected to fire.
FAKE_WITH_BRIDGE="$SCRATCH/fake_with_bridge"
mkdir -p "$FAKE_WITH_BRIDGE/.claude/hooks"
touch "$FAKE_WITH_BRIDGE/.claude/hooks/pre-agent-guard.sh"
cat > "$FAKE_WITH_BRIDGE/.claude/settings.json" <<'SETTINGS'
{"hooks":{"PreToolUse":[{"matcher":"Agent","hooks":[{"type":"command","command":".claude/hooks/pre-agent-guard.sh"}]}]}}
SETTINGS

HITS_BEFORE=$(wc -l < "$HIT_LOG" | tr -d ' ')

echo "$INPUT" \
    | TORIOS_API_BASE="http://127.0.0.1:${PORT}" \
      CLAUDE_PROJECT_DIR="$FAKE_WITH_BRIDGE" \
      bash "$HOOK" 2>/dev/null

HITS_AFTER=$(wc -l < "$HIT_LOG" | tr -d ' ')

if [ "$HITS_AFTER" -gt "$HITS_BEFORE" ]; then
    echo "FAIL: bridge-skip regression — registration called when bridge IS wired (would double-register)" >&2
    exit 1
fi

echo "PASS: bridge-skip fires when pre-agent-guard is wired in settings (no double-register regression)"

exit 0
