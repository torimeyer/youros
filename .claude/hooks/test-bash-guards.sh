#!/bin/bash
# Smoke tests for bash-guards.sh deny paths (wave 2 retrofit).
# Asserts: exit 2, stderr has "Blocked:", hook-denies.log has fresh JSON entry.
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
HOOK="$REPO/.claude/hooks/bash-guards.sh"
DENY_LOG="$HOME/.claude/logs/hook-denies.log"

PASS=0; FAIL=0
ok() { echo "PASS: $1"; PASS=$((PASS+1)); }
ko() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

# Check a condition expressed as a shell command
chk() { local label="$1"; shift; if "$@" 2>/dev/null; then ok "$label"; else ko "$label"; fi; }
# Check that HOOK_OUT contains a pattern
chk_out() { local label="$1" pat="$2"; if echo "$HOOK_OUT" | grep -q "$pat"; then ok "$label"; else ko "$label (got: $HOOK_OUT)"; fi; }

HOOK_OUT="" HOOK_RC=""
run_hook() {
    local json="$1"
    local out; out=$(mktemp)
    local rc_f; rc_f=$(mktemp)
    ( CLAUDE_PROJECT_DIR="$REPO" bash "$HOOK" <<<"$json" >"$out" 2>&1; echo $? >"$rc_f" )
    HOOK_OUT=$(cat "$out"); HOOK_RC=$(cat "$rc_f")
    rm -f "$out" "$rc_f"
}

has_log_entry() {
    # has_log_entry <hook_basename> <since_iso>
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

# ---- Test 1: curl without --connect-timeout ----
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
run_hook '{"tool_name":"Bash","tool_input":{"command":"curl http://example.com"}}'
chk     "curl-timeout: exit 2"     [ "$HOOK_RC" = "2" ]
chk_out "curl-timeout: Blocked:"   "Blocked:"
chk     "curl-timeout: log entry"  has_log_entry "bash-guards.sh" "$TS"

# ---- Test 2: npm run dev ----
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
run_hook '{"tool_name":"Bash","tool_input":{"command":"npm run dev"}}'
chk     "npm-dev: exit 2"          [ "$HOOK_RC" = "2" ]
chk_out "npm-dev: Blocked:"        "Blocked:"
chk     "npm-dev: log entry"       has_log_entry "bash-guards.sh" "$TS"

# ---- Test 3: zsh reserved var (status=) ----
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
run_hook '{"tool_name":"Bash","tool_input":{"command":"status=$(date)"}}'
chk     "zsh-var: exit 2"          [ "$HOOK_RC" = "2" ]
chk_out "zsh-var: Blocked:"        "Blocked:"
chk     "zsh-var: log entry"       has_log_entry "bash-guards.sh" "$TS"

# ---- Test 4: open source file ----
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
run_hook '{"tool_name":"Bash","tool_input":{"command":"open main.py"}}'
chk     "open-src: exit 2"         [ "$HOOK_RC" = "2" ]
chk_out "open-src: Blocked:"       "Blocked:"
chk     "open-src: log entry"      has_log_entry "bash-guards.sh" "$TS"

# ---- Test 5: bare vitest (skip if wrapper absent) ----
if [ -f "$REPO/scripts/run-vitest.sh" ]; then
    TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    run_hook '{"tool_name":"Bash","tool_input":{"command":"vitest run"}}'
    chk     "vitest: exit 2"       [ "$HOOK_RC" = "2" ]
    chk_out "vitest: Blocked:"     "Blocked:"
    chk     "vitest: log entry"    has_log_entry "bash-guards.sh" "$TS"
else
    echo "SKIP: vitest (scripts/run-vitest.sh absent)"
fi

echo ""
echo "bash-guards.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
