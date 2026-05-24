#!/usr/bin/env bash
# Regression test for →1343: zsh-readonly guard false positives inside quoted strings.
#
# The guard should BLOCK real shell-level assignments to zsh read-only vars,
# and PASS when those names appear only inside quoted string arguments.
#
# Usage:
#   bash .claude/hooks/tests/test_zsh_readonly_guard.sh
#
# Exit: 0 = all cases passed, 1 = any failure.

set -u

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUARD="$HOOKS_DIR/lib/rules/bash_guards.sh"

fail() { printf 'FAIL: %s\n' "$1" >&2; FAILURES=$((FAILURES + 1)); }
pass() { printf 'ok:   %s\n' "$1"; }

FAILURES=0

# Source only the Python snippet directly for isolated testing.
# We call the same Python logic that _bg_zsh_reserved_var embeds.
_check_cmd() {
    local cmd="$1"
    HOOK_CMD="$cmd" python3 <<'PY' 2>/dev/null
import os, re
cmd = os.environ.get("HOOK_CMD", "")
RESERVED = ['status', 'pipestatus', 'path', 'cdpath', 'fpath',
            'manpath', 'prompt', 'psvar', 'argv', 'signals', 'options']

def strip_quoted(s):
    result = []
    i = 0
    while i < len(s):
        c = s[i]
        if c in ('"', "'"):
            quote = c
            result.append(c)
            i += 1
            while i < len(s) and s[i] != quote:
                if s[i] == '\\' and quote == '"' and i + 1 < len(s):
                    result.append('XX')
                    i += 2
                else:
                    result.append('X')
                    i += 1
            if i < len(s):
                result.append(s[i])
                i += 1
        else:
            result.append(c)
            i += 1
    return ''.join(result)

stripped = strip_quoted(cmd)
strict = re.compile(
    r'(?:(?:^|[;\s|&(]))'
    r'(' + '|'.join(re.escape(v) for v in RESERVED) + r')'
    r'(?=[^a-zA-Z0-9_])',
    re.MULTILINE,
)
for m in strict.finditer(stripped):
    var = m.group(1)
    after = stripped[m.end():]
    if re.match(r'\s*=(?!=)', after):
        print(var)
        break
PY
}

assert_block() {
    local desc="$1" cmd="$2"
    local out
    out=$(_check_cmd "$cmd")
    if [ -n "$out" ]; then
        pass "BLOCK [$out] — $desc"
    else
        fail "expected BLOCK but got PASS — $desc"
    fi
}

assert_pass() {
    local desc="$1" cmd="$2"
    local out
    out=$(_check_cmd "$cmd")
    if [ -z "$out" ]; then
        pass "PASS — $desc"
    else
        fail "false positive [$out] — $desc"
    fi
}

# ── Cases that MUST be blocked ───────────────────────────────────────────────

assert_block \
    "bare status= at command start" \
    'status=$(kubectl get pod -o jsonpath="{.status}")'

assert_block \
    "status= after semicolon" \
    'XYZ=foo; status=bar'

assert_block \
    "status=\$? after semicolon" \
    'result=$(curl -s https://example.com); status=$?'

assert_block \
    "path= assignment" \
    'path=/usr/local/bin:$path'

assert_block \
    "prompt= assignment" \
    'prompt="$ "'

# ── Cases that MUST NOT be blocked (false-positive regression) ────────────────

assert_pass \
    "status= inside single quotes (→1343 false positive)" \
    "echo 'status=completed'"

assert_pass \
    "status= inside double-quoted arg with leading space (→1343 false positive)" \
    'ostk work add "... where status=completed means done"'

assert_pass \
    "status= inside python -c string" \
    "python3 -c \"print('status=ok')\""

assert_pass \
    "prev_status= — longer identifier, not a reserved name" \
    'prev_status=$(git status --porcelain)'

assert_pass \
    "git status — reads status, does not assign" \
    'files_dirty=$(git status --porcelain)'

assert_pass \
    "awk pattern with status: key (no assignment)" \
    "awk -F'\"status\":' '{print \$2}' file.json"

assert_pass \
    "curl with status= in JSON body inside single quotes" \
    "curl -X POST https://api/endpoint -d '{\"status\": \"done\"}'"

assert_pass \
    "grep for status=running in a log" \
    "grep 'status=running' /tmp/agent.log"

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
if [ "$FAILURES" -eq 0 ]; then
    echo "All $(( $(grep -c 'assert_' "$0") )) cases passed."
    exit 0
else
    echo "$FAILURES case(s) FAILED." >&2
    exit 1
fi
