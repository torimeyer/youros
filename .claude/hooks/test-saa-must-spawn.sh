#!/bin/bash
# Smoke tests for saa-must-spawn.sh.
# Covers original deny behavior plus the over-block fix (→1025 →1029):
#   1. "fix" + plain bash  → blocked (no Agent yet, not ancillary)
#   2. "fix" + Agent in transcript + bash → allowed
#   3. "fix" + bash is "ostk work add ..." → allowed (ancillary)
#   4. "fix" + bash is "scripts/dev-frontend.sh" → allowed (ancillary)
#   5. No fix trigger → allowed
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

mkdir -p "$PROJ_JSONL_DIR"

# Temp JSONL files — cleaned up on exit.
JSONL_FIX_ONLY="/tmp/test-saa-fix-only-$$.jsonl"
JSONL_FIX_WITH_AGENT="/tmp/test-saa-fix-agent-$$.jsonl"
JSONL_SAA="/tmp/test-saa-saa-msg-$$.jsonl"
JSONL_NO_TRIGGER="/tmp/test-saa-no-trigger-$$.jsonl"

# fix msg, no Agent call
echo '{"type":"user","message":{"content":"fix this hook bug"}}' > "$JSONL_FIX_ONLY"

# fix msg + Agent already in transcript
printf '%s\n%s\n' \
    '{"type":"user","message":{"content":"fix this hook bug"}}' \
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Agent","input":{}}]}}' \
    > "$JSONL_FIX_WITH_AGENT"

# saa msg (original test)
echo '{"type":"user","message":{"content":"saa build something"}}' > "$JSONL_SAA"

# neutral msg
echo '{"type":"user","message":{"content":"show me the logs"}}' > "$JSONL_NO_TRIGGER"

cleanup() {
    rm -f "$FAKE_CFG" "$JSONL_FIX_ONLY" "$JSONL_FIX_WITH_AGENT" "$JSONL_SAA" "$JSONL_NO_TRIGGER"
}
trap cleanup EXIT

run_hook() {
    local json="$1" jsonl="$2"
    # Symlink the target JSONL as the newest file in PROJ_JSONL_DIR so ls -t picks it.
    local link="$PROJ_JSONL_DIR/test-saa-active-$$.jsonl"
    ln -sf "$jsonl" "$link" 2>/dev/null || cp "$jsonl" "$link"
    local out rc_f
    out=$(mktemp); rc_f=$(mktemp)
    (
        CLAUDE_PROJECT_DIR="$MAIN_REPO" \
        MYOS_CONFIG_PATH="$FAKE_CFG" \
        bash "$HOOK" <<<"$json" >"$out" 2>&1
        echo $? >"$rc_f"
    )
    HOOK_OUT=$(cat "$out"); HOOK_RC=$(cat "$rc_f")
    rm -f "$out" "$rc_f" "$link"
}

# ── Case 1: "fix" prompt + plain bash → blocked ──────────────────────────────
echo "=== Case 1: fix + bash (no Agent yet) → blocked ==="
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
run_hook '{"tool_name":"Bash","tool_input":{"command":"echo hello"}}' "$JSONL_FIX_ONLY"
chk     "fix-deny: exit 2"      [ "$HOOK_RC" = "2" ]
chk_out "fix-deny: Blocked:"    "Blocked:"
chk     "fix-deny: log entry"   has_log_entry "saa-must-spawn.sh" "$TS"

# ── Case 2: "fix" prompt + Agent in transcript + bash → allowed ───────────────
echo ""
echo "=== Case 2: fix + Agent already in transcript → allowed ==="
run_hook '{"tool_name":"Bash","tool_input":{"command":"echo hello"}}' "$JSONL_FIX_WITH_AGENT"
chk "agent-fired-allow: exit 0" [ "$HOOK_RC" = "0" ]

# ── Case 3: "fix" prompt + ostk work add → allowed ───────────────────────────
echo ""
echo "=== Case 3: fix + ostk work add (needle filing) → allowed ==="
run_hook '{"tool_name":"Bash","tool_input":{"command":"ostk work add \"fix hook over-block\""}}' "$JSONL_FIX_ONLY"
chk "ostk-work-allow: exit 0"   [ "$HOOK_RC" = "0" ]

# ── Case 4: "fix" prompt + scripts/dev-frontend.sh → allowed ─────────────────
echo ""
echo "=== Case 4: fix + scripts/dev-frontend.sh (server start) → allowed ==="
run_hook '{"tool_name":"Bash","tool_input":{"command":"scripts/dev-frontend.sh"}}' "$JSONL_FIX_ONLY"
chk "dev-script-allow: exit 0"  [ "$HOOK_RC" = "0" ]

# ── Case 5: no fix trigger → allowed ─────────────────────────────────────────
echo ""
echo "=== Case 5: no fix-trigger → allowed ==="
run_hook '{"tool_name":"Bash","tool_input":{"command":"echo hello"}}' "$JSONL_NO_TRIGGER"
chk "no-trigger-allow: exit 0"  [ "$HOOK_RC" = "0" ]

# ── Original test: saa + Agent tool passes through ───────────────────────────
echo ""
echo "=== Original: saa + Agent tool → allowed ==="
run_hook '{"tool_name":"Agent","tool_input":{"prompt":"do something"}}' "$JSONL_SAA"
chk "agent-ok: exit 0"          [ "$HOOK_RC" = "0" ]

echo ""
echo "saa-must-spawn.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
