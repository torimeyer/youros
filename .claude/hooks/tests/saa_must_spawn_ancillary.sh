#!/usr/bin/env bash
# Regression test: saa_must_spawn ancillary commands (git status/log/diff, curl --connect-timeout)
# Fixed during rename session bash rejection diagnosis (2026-05-28)
#
# These commands were blocked during the needle-to-task rename session because
# saa_must_spawn was active but lacked read-only probe entries in its allowlist.
# This test ensures they are whitelisted going forward.
#
# Run: bash .claude/hooks/tests/saa_must_spawn_ancillary.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB="$SCRIPT_DIR/../lib"
RULE="$LIB/rules/saa_must_spawn.sh"
JSON_FILE="$LIB/default-rules.json"

FAILED=0
fail() { echo "FAIL: $1" >&2; FAILED=1; }
pass() { echo "  ok: $1"; }

# ---- 1. Structural checks ----
echo "=== 1. Structural checks (saa_must_spawn.sh) ==="

for pat in '"git status"' '"git log"' '"git diff"' '"git show"' '"curl --connect-timeout"'; do
    if grep -qF "$pat" "$RULE"; then
        pass "pattern $pat present in saa_must_spawn.sh"
    else
        fail "pattern $pat MISSING from saa_must_spawn.sh"
    fi
done

echo ""
echo "=== 2. Structural checks (default-rules.json) ==="

check_json_prefix() {
    local key="$1"
    if python3 -c "
import json, sys
d = json.load(open('$JSON_FILE'))
prefixes = d['rules']['saa_must_spawn']['ancillary_cmd_prefixes']
if '$key' not in prefixes:
    sys.exit(1)
" 2>/dev/null; then
        pass "ancillary_cmd_prefixes contains '$key'"
    else
        fail "ancillary_cmd_prefixes missing '$key'"
    fi
}

check_json_prefix "git status"
check_json_prefix "git log"
check_json_prefix "git diff"
check_json_prefix "git show"
check_json_prefix "curl --connect-timeout"
check_json_prefix "ostk work "
check_json_prefix "scripts/e2e_smoke.sh"

# ---- 3. Functional: invoke _saa_must_spawn_check with saa-triggered transcript ----
echo ""
echo "=== 3. Functional: case-statement allow/block checks ==="

SCRATCH=$(mktemp -d -t saa-ancillary.XXXXXX)
trap "rm -rf $SCRATCH" EXIT

FAKE_HOME="$SCRATCH/home"
FAKE_PROJ="$SCRATCH/fake-proj"
SLUG=$(echo "$FAKE_PROJ" | sed 's|/|-|g')
mkdir -p "$FAKE_HOME/.claude/projects/$SLUG"
mkdir -p "$FAKE_PROJ"
mkdir -p "$FAKE_HOME/.youros"

# Transcript: last user message has "saa" trigger, no Agent after it
TRANSCRIPT="$FAKE_HOME/.claude/projects/$SLUG/session.jsonl"
printf '{"type":"user","message":{"content":"saa diagnose why bash was rejected during rename"}}\n' \
    > "$TRANSCRIPT"

# rules.json: enable saa_must_spawn
cat > "$FAKE_HOME/.youros/rules.json" <<'RULES'
{"schema_version":1,"rules":{"saa_must_spawn":{"enabled":true}}}
RULES

run_check() {
    local tool="$1" cmd="$2"
    (
        export HOME="$FAKE_HOME"
        export CLAUDE_PROJECT_DIR="$FAKE_PROJ"

        deny()         { exit 2; }
        log_rule_fire() { :; }
        init_deny_traps() { :; }
        _LOAD_RULE_DIR="$LIB"

        # shellcheck source=/dev/null
        source "$LIB/deny.sh"      2>/dev/null || true
        source "$LIB/log-fire.sh"  2>/dev/null || true
        source "$LIB/load-rule.sh" 2>/dev/null || true
        source "$RULE"             2>/dev/null || true

        _saa_must_spawn_check "$tool" "$cmd"
    )
    echo $?
}

assert_allow() {
    local label="$1" tool="$2" cmd="$3"
    local rc
    rc=$(run_check "$tool" "$cmd")
    if [ "$rc" = "0" ]; then
        pass "$label"
    else
        fail "$label — expected allow (exit 0), got exit $rc"
    fi
}

assert_block() {
    local label="$1" tool="$2" cmd="$3"
    local rc
    rc=$(run_check "$tool" "$cmd")
    if [ "$rc" = "2" ]; then
        pass "$label"
    else
        fail "$label — expected block (exit 2), got exit $rc"
    fi
}

assert_advise() {
    local label="$1" tool="$2" cmd="$3"
    local rc stderr_out
    rc=$(run_check "$tool" "$cmd")
    # Capture stderr separately: the Advice: message goes to stderr from advise()
    stderr_out=$(
        (
            export HOME="$FAKE_HOME"
            export CLAUDE_PROJECT_DIR="$FAKE_PROJ"

            deny()         { exit 2; }
            log_rule_fire() { :; }
            init_deny_traps() { :; }
            _LOAD_RULE_DIR="$LIB"

            # shellcheck source=/dev/null
            source "$LIB/deny.sh"      2>/dev/null || true
            source "$LIB/log-fire.sh"  2>/dev/null || true
            source "$LIB/load-rule.sh" 2>/dev/null || true
            source "$RULE"             2>/dev/null || true

            _saa_must_spawn_check "$tool" "$cmd"
        ) 2>&1
    )
    if [ "$rc" = "0" ] && echo "$stderr_out" | grep -q "Advice:"; then
        pass "$label"
    elif [ "$rc" != "0" ]; then
        fail "$label — expected advise (exit 0), got exit $rc"
    else
        fail "$label — expected 'Advice:' in stderr, got: $(echo "$stderr_out" | head -1)"
    fi
}

# New allowlist entries
assert_allow "git status is allowed"                   mcp__ostk__bash "git status"
assert_allow "git status -s is allowed"                mcp__ostk__bash "git status -s"
assert_allow "git log --oneline is allowed"            mcp__ostk__bash "git log --oneline -10"
assert_allow "git diff is allowed"                     mcp__ostk__bash "git diff HEAD~1"
assert_allow "git show is allowed"                     mcp__ostk__bash "git show HEAD"
assert_allow "curl --connect-timeout is allowed"       mcp__ostk__bash \
    "curl --connect-timeout 3 -m 5 -sSk https://127.0.0.1:8000/api/agents"

# Regressions: existing allowlist entries
assert_allow "scripts/e2e_smoke.sh still allowed"      mcp__ostk__bash "bash scripts/e2e_smoke.sh"
assert_allow "ostk work list still allowed"            mcp__ostk__bash "ostk work list --status open"

# Sanity: a non-ancillary inline command must still fire advisory (rule is advisory since →1288)
assert_advise "inline echo advises (sanity)"           mcp__ostk__bash "echo hello"

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "ALL TESTS PASSED"
    exit 0
else
    echo "$FAILED TESTS FAILED"
    exit 1
fi
