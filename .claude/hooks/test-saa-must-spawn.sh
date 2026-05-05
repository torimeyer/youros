#!/bin/bash
# Smoke tests for saa-must-spawn.sh deny path (wave 2 retrofit).
# Creates a temporary config + JSONL to simulate a "saa" user message,
# then verifies exit 2, stderr "Blocked:", and log entry.
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
# Use main worktree so CLAUDE_PROJECT_DIR is not inside .claude/worktrees/.
# Strip /.claude/worktrees/<name> suffix if present (worktree case).
MAIN_REPO="${REPO%/.claude/worktrees/*}"
if [ -z "$MAIN_REPO" ] || [ ! -d "$MAIN_REPO" ]; then
    MAIN_REPO="$REPO"
fi
HOOK="$REPO/.claude/hooks/saa-must-spawn.sh"
DENY_LOG="$HOME/.claude/logs/hook-denies.log"
PROJ_JSONL_DIR="$HOME/.claude/projects/-Users-torimeyer-claude-torios"

PASS=0; FAIL=0
ok() { echo "PASS: $1"; PASS=$((PASS+1)); }
ko() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
chk()     { local label="$1"; shift; if "$@" 2>/dev/null; then ok "$label"; else ko "$label"; fi; }
chk_out() { local label="$1" pat="$2"; if echo "$HOOK_OUT" | grep -q "$pat"; then ok "$label"; else ko "$label (got: $HOOK_OUT)"; fi; }

HOOK_OUT="" HOOK_RC=""

has_log_entry() {
    [ -f "$DENY_LOG" ] || return 1
    python3 - "$DENY_LOG" "$1" "$2" <<'PY'
import sys, json, datetime
log_path, hook_name, since = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    since_dt = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
except Exception:
    since_dt = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
found = False
try:
    with open(log_path) as f:
        for line in f:
            try:
                d = json.loads(line.strip())
                if not d.get("hook", "").endswith(hook_name): continue
                if not d.get("reason", ""): continue
                entry_dt = datetime.datetime.fromisoformat(d.get("ts", "").replace("Z", "+00:00"))
                if entry_dt < since_dt: continue
                found = True
            except Exception:
                pass
except Exception:
    pass
sys.exit(0 if found else 1)
PY
}

# Create temp config with enable_tori_rules: true
FAKE_CFG="/tmp/test-saa-wave2-cfg-$$.json"
echo '{"enable_tori_rules": true}' > "$FAKE_CFG"

# Create temp JSONL with a "saa" user message (newest file = picked by ls -t)
mkdir -p "$PROJ_JSONL_DIR"
DUMMY_JSONL="$PROJ_JSONL_DIR/test-saa-wave2-smoke-$$.jsonl"
echo '{"type":"user","message":{"content":"saa build something"}}' > "$DUMMY_JSONL"

cleanup() { rm -f "$FAKE_CFG" "$DUMMY_JSONL"; }
trap cleanup EXIT

run_hook() {
    local json="$1"
    local out; out=$(mktemp)
    local rc_f; rc_f=$(mktemp)
    (
        CLAUDE_PROJECT_DIR="$MAIN_REPO" \
        MYOS_CONFIG_PATH="$FAKE_CFG" \
        bash "$HOOK" <<<"$json" >"$out" 2>&1
        echo $? >"$rc_f"
    )
    HOOK_OUT=$(cat "$out"); HOOK_RC=$(cat "$rc_f")
    rm -f "$out" "$rc_f"
}

# ---- Test 1: Bash tool denied when last user msg is "saa ..." ----
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
run_hook '{"tool_name":"Bash","tool_input":{"command":"echo hello"}}'
chk     "saa-deny: exit 2"     [ "$HOOK_RC" = "2" ]
chk_out "saa-deny: Blocked:"   "Blocked:"
chk     "saa-deny: log entry"  has_log_entry "saa-must-spawn.sh" "$TS"

# ---- Test 2: Agent tool passes through (not blocked) ----
run_hook '{"tool_name":"Agent","tool_input":{"prompt":"do something"}}'
chk "agent-ok: exit 0"         [ "$HOOK_RC" = "0" ]

echo ""
echo "saa-must-spawn.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
