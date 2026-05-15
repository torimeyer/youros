#!/bin/bash
# Test: work_close_guard.sh (→1381)
#
# Proves the guard:
#   1. Blocks `ostk work close →NNN` when no commit on main references →NNN
#   2. Allows the close when a commit on main references →NNN
#   3. Allows the close when --no-verify is passed
#   4. Ignores unrelated Bash commands
#   5. Fails open when needle ID cannot be parsed
#
# Run: bash .claude/hooks/tests/work_close_guard_test.sh

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
GUARD_LIB="$REPO_ROOT/.claude/hooks/lib/rules/work_close_guard.sh"

if [ ! -f "$GUARD_LIB" ]; then
    echo "FAIL: cannot find guard lib at $GUARD_LIB"
    exit 1
fi

FAILED=0
SCRATCH=$(mktemp -d -t work-close-guard-test.XXXXXX)

fail() { echo "FAIL: $1"; FAILED=1; }
pass() { echo "  pass: $1"; }

cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

# ---- helpers ----

make_fake_repo() {
    # Creates a minimal git repo with commits on main.
    # $1 = path to create the repo at
    # $2 = commit message (references or doesn't reference a needle)
    local path="$1" msg="$2"
    mkdir -p "$path"
    git -C "$path" init -q
    git -C "$path" config user.email "test@test"
    git -C "$path" config user.name "Test"
    echo "init" > "$path/init.txt"
    git -C "$path" add init.txt
    git -C "$path" commit -q -m "initial commit"
    # Get branch name after first commit (not before — HEAD is unborn before)
    local def
    def=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
    if [ "$def" != "main" ]; then
        git -C "$path" branch -m "$def" main 2>/dev/null || true
    fi
    # Add the needle-referencing (or non-referencing) commit
    echo "work" > "$path/work.txt"
    git -C "$path" add work.txt
    git -C "$path" commit -q -m "$msg"
}

run_guard() {
    # Runs _work_close_guard_check in a subshell.
    # $1 = CLAUDE_PROJECT_DIR (repo path)
    # $2 = cmd
    local cpd="$1" cmd="$2"
    (
        CLAUDE_PROJECT_DIR="$cpd"
        export CLAUDE_PROJECT_DIR
        rule_enabled()  { return 0; }
        log_rule_fire() { return 0; }
        deny() { echo "DENIED: $1"; exit 2; }
        export -f rule_enabled log_rule_fire deny 2>/dev/null || true
        . "$GUARD_LIB"
        _work_close_guard_check "mcp__ostk__bash" "$cmd" 2>/dev/null
        echo "ALLOWED"
    )
}

# ===========================================================================
# TEST 1: BLOCK — ostk work close →1374 with no matching commit on main
# ===========================================================================
REPO1="$SCRATCH/repo1"
make_fake_repo "$REPO1" "fix(→9999): something unrelated"

RESULT1=$(run_guard "$REPO1" "ostk work close →1374")
if echo "$RESULT1" | grep -q "DENIED"; then
    pass "test1: close →1374 blocked when no commit on main references it"
else
    fail "test1: expected DENIED, got: $RESULT1"
fi

# ===========================================================================
# TEST 2: ALLOW — ostk work close →1374 with a matching commit on main
# ===========================================================================
REPO2="$SCRATCH/repo2"
make_fake_repo "$REPO2" "fix(→1374): actually fixed the bug"

RESULT2=$(run_guard "$REPO2" "ostk work close →1374")
if echo "$RESULT2" | grep -q "ALLOWED"; then
    pass "test2: close →1374 allowed when commit on main references it"
else
    fail "test2: expected ALLOWED, got: $RESULT2"
fi

# ===========================================================================
# TEST 3: ALLOW — OSTK_WORK_CLOSE_NO_VERIFY=1 bypasses the check even with no commit
# ===========================================================================
REPO3="$SCRATCH/repo3"
make_fake_repo "$REPO3" "chore: unrelated cleanup"

RESULT3=$(run_guard "$REPO3" "OSTK_WORK_CLOSE_NO_VERIFY=1 ostk work close →1374")
if echo "$RESULT3" | grep -q "ALLOWED"; then
    pass "test3: OSTK_WORK_CLOSE_NO_VERIFY=1 bypasses commit check"
else
    fail "test3: expected ALLOWED with bypass env var, got: $RESULT3"
fi

# ===========================================================================
# TEST 4: ALLOW — unrelated command passes through unchanged
# ===========================================================================
REPO4="$SCRATCH/repo4"
make_fake_repo "$REPO4" "chore: nothing special"

RESULT4=$(run_guard "$REPO4" "ostk boot && echo ready")
if echo "$RESULT4" | grep -q "ALLOWED"; then
    pass "test4: non-close command passes through"
else
    fail "test4: expected ALLOWED for unrelated cmd, got: $RESULT4"
fi

# ===========================================================================
# TEST 5: ALLOW — numeric-only needle ID (no → arrow)
# ===========================================================================
REPO5="$SCRATCH/repo5"
make_fake_repo "$REPO5" "feat(→1381): block close without commit"

RESULT5=$(run_guard "$REPO5" "ostk work close 1381")
if echo "$RESULT5" | grep -q "ALLOWED"; then
    pass "test5: numeric-only needle ID matched commit with → prefix"
else
    fail "test5: expected ALLOWED for numeric ID, got: $RESULT5"
fi

# ===========================================================================
# TEST 6: BLOCK — numeric-only needle ID, no matching commit
# ===========================================================================
REPO6="$SCRATCH/repo6"
make_fake_repo "$REPO6" "feat(→9999): something else entirely"

RESULT6=$(run_guard "$REPO6" "ostk work close 1374")
if echo "$RESULT6" | grep -q "DENIED"; then
    pass "test6: numeric-only needle ID blocked when no commit references it"
else
    fail "test6: expected DENIED for numeric ID with no commit, got: $RESULT6"
fi

# ===========================================================================
# TEST 7: ALLOW — close from inside a worktree (worktree shares main history)
# ===========================================================================
REPO7="$SCRATCH/repo7"
make_fake_repo "$REPO7" "fix(→1381): guard implemented"

# Simulate running from inside a worktree path
WORKTREE7="$REPO7/.claude/worktrees/agent-1381-test"
mkdir -p "$WORKTREE7"
# git worktree add for real (so git log main works correctly)
git -C "$REPO7" worktree add "$WORKTREE7" -b worktree-agent-1381-test 2>/dev/null || true

RESULT7=$(
    CLAUDE_PROJECT_DIR="$WORKTREE7"
    export CLAUDE_PROJECT_DIR
    rule_enabled()  { return 0; }
    log_rule_fire() { return 0; }
    deny() { echo "DENIED: $1"; exit 2; }
    export -f rule_enabled log_rule_fire deny 2>/dev/null || true
    . "$GUARD_LIB"
    _work_close_guard_check "mcp__ostk__bash" "ostk work close →1381" 2>/dev/null
    echo "ALLOWED"
)
if echo "$RESULT7" | grep -q "ALLOWED"; then
    pass "test7: close from inside a worktree allowed when commit exists on main"
else
    fail "test7: expected ALLOWED from worktree, got: $RESULT7"
fi

# ===========================================================================
# TEST 8: ALLOW — --reason flag does not confuse the ID parser
# ===========================================================================
REPO8="$SCRATCH/repo8"
make_fake_repo "$REPO8" "fix(→1381): implemented guard"

RESULT8=$(run_guard "$REPO8" 'ostk work close →1381 --reason "done"')
if echo "$RESULT8" | grep -q "ALLOWED"; then
    pass "test8: --reason flag does not confuse needle ID parser"
else
    fail "test8: expected ALLOWED with --reason, got: $RESULT8"
fi

# ===========================================================================
# TEST 9: ALLOW — "ostk work close" appearing inside a quoted argument
#          (e.g. curl -d '...ostk work close...') must NOT trigger the guard
# ===========================================================================
REPO9="$SCRATCH/repo9"
make_fake_repo "$REPO9" "chore: nothing special"

CURL_CMD="curl --connect-timeout 3 -m 5 -sSk -X POST https://127.0.0.1:8000/api/agents/foo/complete -H 'Content-Type: application/json' -d '{\"summary\": \"→1381 done: hook blocks ostk work close without a verifying commit on main. 8/8 tests pass.\"}'"

RESULT9=$(run_guard "$REPO9" "$CURL_CMD")
if echo "$RESULT9" | grep -q "ALLOWED"; then
    pass "test9: ostk work close inside quoted curl -d arg does not trigger guard"
else
    fail "test9: curl with ostk work close in JSON data was incorrectly blocked: $RESULT9"
fi

# ===========================================================================
# Result
# ===========================================================================
echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "PASS (9/9 tests)"
    exit 0
else
    echo "FAILED"
    exit 1
fi
