#!/usr/bin/env bash
# Tests for home_scan_guard.sh (→2479).
#
# Guards against macOS privacy popup storms caused by agents scanning the
# home folder, Documents, Desktop, Downloads, or iCloud (Library/Mobile Documents).
#
# Run: bash .claude/hooks/tests/home_scan_guard_test.sh

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
GUARD_LIB="$REPO_ROOT/.claude/hooks/lib/rules/home_scan_guard.sh"
HOME_DIR="$(cd ~ && pwd)"

if [ ! -f "$GUARD_LIB" ]; then
    echo "FAIL: cannot find guard lib at $GUARD_LIB"
    exit 1
fi

FAILED=0

fail() { echo "FAIL: $1"; FAILED=1; }
pass() { echo "  pass: $1"; }

# ---- test runner ----
# Runs _home_scan_guard_check in a subshell.
# deny() prints "DENIED:" and exits 2; the test checks for that.
run_guard() {
    local tool="$1" cmd="$2"
    (
        HOOK_CMD="$cmd"
        export HOOK_CMD
        rule_enabled()  { return 0; }
        log_rule_fire() { return 0; }
        deny()   { echo "DENIED: $1"; exit 2; }
        advise() { echo "ADVISED: $1"; return 0; }
        export -f rule_enabled log_rule_fire deny advise 2>/dev/null || true
        . "$GUARD_LIB"
        _home_scan_guard_check "$tool" "$cmd" 2>/dev/null
        echo "ALLOWED"
    )
}

echo "=== home_scan_guard tests ==="

# ===========================================================================
# TEST 1: find rooted at home directory is BLOCKED
# ===========================================================================
R1=$(run_guard "Bash" "find ~ -name '*.pdf'")
if echo "$R1" | grep -q "DENIED"; then
    pass "test1: find ~ is blocked"
else
    fail "test1: find ~ was NOT blocked (got: $R1)"
fi

# ===========================================================================
# TEST 2: find with absolute home path is BLOCKED
# ===========================================================================
R2=$(run_guard "mcp__ostk__bash" "find $HOME_DIR -name '*.key' -type f")
if echo "$R2" | grep -q "DENIED"; then
    pass "test2: find \$HOME (absolute) is blocked"
else
    fail "test2: find \$HOME absolute was NOT blocked (got: $R2)"
fi

# ===========================================================================
# TEST 3: find rooted at ~/Documents is BLOCKED
# ===========================================================================
R3=$(run_guard "Bash" "find ~/Documents -name '*.docx'")
if echo "$R3" | grep -q "DENIED"; then
    pass "test3: find ~/Documents is blocked"
else
    fail "test3: find ~/Documents was NOT blocked (got: $R3)"
fi

# ===========================================================================
# TEST 4: find rooted at iCloud folder is BLOCKED
# ===========================================================================
R4=$(run_guard "Bash" "find ~/Library/Mobile\ Documents -name '*.pages'")
if echo "$R4" | grep -q "DENIED"; then
    pass "test4: find ~/Library/Mobile Documents (iCloud) is blocked"
else
    fail "test4: find iCloud path was NOT blocked (got: $R4)"
fi

# ===========================================================================
# TEST 5: find rooted at ~/Desktop is BLOCKED
# ===========================================================================
R5=$(run_guard "Bash" "find ~/Desktop -maxdepth 2 -name '*.pdf'")
if echo "$R5" | grep -q "DENIED"; then
    pass "test5: find ~/Desktop is blocked"
else
    fail "test5: find ~/Desktop was NOT blocked (got: $R5)"
fi

# ===========================================================================
# TEST 6: grep -r on ~/Downloads is BLOCKED
# ===========================================================================
R6=$(run_guard "Bash" "grep -r 'password' ~/Downloads")
if echo "$R6" | grep -q "DENIED"; then
    pass "test6: grep -r ~/Downloads is blocked"
else
    fail "test6: grep -r ~/Downloads was NOT blocked (got: $R6)"
fi

# ===========================================================================
# TEST 7: ls -R on home is BLOCKED
# ===========================================================================
R7=$(run_guard "Bash" "ls -lRa ~/")
if echo "$R7" | grep -q "DENIED"; then
    pass "test7: ls -R ~/ is blocked"
else
    fail "test7: ls -R ~/ was NOT blocked (got: $R7)"
fi

# ===========================================================================
# TEST 8: find inside repo dir is ALLOWED
# ===========================================================================
R8=$(run_guard "Bash" "find $REPO_ROOT -name '*.py' -type f")
if echo "$R8" | grep -q "ALLOWED"; then
    pass "test8: find inside repo is allowed"
else
    fail "test8: find inside repo was unexpectedly blocked (got: $R8)"
fi

# ===========================================================================
# TEST 9: find in ~/.youros is ALLOWED
# ===========================================================================
R9=$(run_guard "Bash" "find ~/.youros -name 'settings.json'")
if echo "$R9" | grep -q "ALLOWED"; then
    pass "test9: find ~/.youros is allowed"
else
    fail "test9: find ~/.youros was unexpectedly blocked (got: $R9)"
fi

# ===========================================================================
# TEST 10: find in /tmp is ALLOWED
# ===========================================================================
R10=$(run_guard "mcp__ostk__bash" "find /tmp -name '*.log' -mtime -1")
if echo "$R10" | grep -q "ALLOWED"; then
    pass "test10: find /tmp is allowed"
else
    fail "test10: find /tmp was unexpectedly blocked (got: $R10)"
fi

# ===========================================================================
# TEST 11: non-recursive grep does NOT trigger guard (no -r flag)
# ===========================================================================
R11=$(run_guard "Bash" "grep 'pattern' ~/some-file.txt")
if echo "$R11" | grep -q "ALLOWED"; then
    pass "test11: grep without -r on a single file is allowed"
else
    fail "test11: non-recursive grep was unexpectedly blocked (got: $R11)"
fi

# ===========================================================================
# TEST 12: find inside ~/claude (project area within home) is ALLOWED
# ===========================================================================
R12=$(run_guard "Bash" "find ~/claude/torios -name '*.ts'")
if echo "$R12" | grep -q "ALLOWED"; then
    pass "test12: find ~/claude (project area) is allowed"
else
    fail "test12: find ~/claude was unexpectedly blocked (got: $R12)"
fi

# ===========================================================================
# TEST 13: grep -R (capital R) on ~/Desktop is BLOCKED
# ===========================================================================
R13=$(run_guard "Bash" "grep -R 'secret' ~/Desktop")
if echo "$R13" | grep -q "DENIED"; then
    pass "test13: grep -R (capital) on ~/Desktop is blocked"
else
    fail "test13: grep -R ~/Desktop was NOT blocked (got: $R13)"
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "PASS (13/13 tests)"
    exit 0
else
    echo "FAILED"
    exit 1
fi
