#!/bin/bash
# Regression test for main_commit_guard.sh (→1348).
#
# Proves the guard blocks `git commit` from a worktree agent when the branch
# is `main`, and allows it when the branch is a proper worktree branch.
#
# Background: →1346 subagent committed c0a22a2 directly to main.
# isolation_bridge.sh contains NO symlink claim — the agent hallucinated it.
# This guard is the correct prevention layer.
#
# Run: bash .claude/hooks/tests/main_commit_guard_test.sh

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
GUARD_LIB="$REPO_ROOT/.claude/hooks/lib/rules/main_commit_guard.sh"

if [ ! -f "$GUARD_LIB" ]; then
    echo "FAIL cannot find guard lib at $GUARD_LIB"
    exit 1
fi

FAILED=0
SCRATCH=$(mktemp -d -t main-commit-guard-test.XXXXXX)

fail() { echo "FAIL: $1"; FAILED=1; }
pass() { echo "  pass: $1"; }

cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

# ---- helpers ----
make_fake_worktree() {
    # Creates a minimal git repo that looks like a worktree agent directory.
    # $1 = base project path, $2 = branch name to checkout
    local base="$1" branch="$2"
    local wt="$base/.claude/worktrees/agent-1346-test-fake"
    mkdir -p "$wt"
    git -C "$wt" init -q
    git -C "$wt" config user.email "test@test"
    git -C "$wt" config user.name "Test"
    echo "init" > "$wt/init.txt"
    git -C "$wt" add init.txt
    git -C "$wt" commit -q -m "initial"
    local _def
    _def=$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
    if [ "$_def" != "main" ]; then
        git -C "$wt" branch -m "$_def" main
    fi
    if [ "$branch" != "main" ]; then
        git -C "$wt" checkout -b "$branch" -q 2>/dev/null || true
    fi
    echo "$wt"
}

run_guard() {
    # Runs _main_commit_guard_check in a subshell with provided env.
    # $1=HOOK_CWD, $2=CLAUDE_PROJECT_DIR, $3=cmd, $4=input_json (optional)
    # Both deny() and advise() output "DENIED:" and exit 2 here so the test
    # can detect when the guard condition fires (regardless of blocking vs advisory).
    local hook_cwd="$1" cpd="$2" cmd="$3" inp="${4:-}"
    (
        HOOK_CWD="$hook_cwd"
        CLAUDE_PROJECT_DIR="$cpd"
        INPUT_JSON="$inp"
        export HOOK_CWD CLAUDE_PROJECT_DIR INPUT_JSON
        rule_enabled()  { return 0; }
        log_rule_fire() { return 0; }
        deny()   { echo "DENIED: $1"; exit 2; }
        advise() { echo "DENIED: $1"; exit 2; }
        export -f rule_enabled log_rule_fire deny advise 2>/dev/null || true
        . "$GUARD_LIB"
        _main_commit_guard_check "Bash" "$cmd" "${INPUT_JSON:-}" 2>/dev/null
        echo "ALLOWED"
    )
}

# ===========================================================================
# TEST 1: guard BLOCKS when cwd is worktree and branch is main
# ===========================================================================
PROJ1="$SCRATCH/proj1"
WT1=$(make_fake_worktree "$PROJ1" "main")

RESULT1=$(run_guard "$WT1" "" "git commit -m 'bad commit to main'")

if echo "$RESULT1" | grep -q "DENIED"; then
    pass "test1: commit on main branch in worktree (via HOOK_CWD) was blocked"
else
    fail "test1: commit on main was NOT blocked (got: $RESULT1)"
fi

# ===========================================================================
# TEST 2: guard ALLOWS when worktree branch is NOT main
# ===========================================================================
PROJ2="$SCRATCH/proj2"
WT2=$(make_fake_worktree "$PROJ2" "worktree-agent-1346-test-fake")

RESULT2=$(run_guard "$WT2" "" "git commit -m 'fix(→1346): real work'")

if echo "$RESULT2" | grep -q "ALLOWED"; then
    pass "test2: commit on worktree branch (not main) was allowed"
elif echo "$RESULT2" | grep -q "DENIED"; then
    fail "test2: commit on worktree branch was unexpectedly blocked"
else
    fail "test2: unexpected result: $RESULT2"
fi

# ===========================================================================
# TEST 3: guard BLOCKS via CLAUDE_PROJECT_DIR worktree + main branch
#         (production case: agent's cwd is main repo, CLAUDE_PROJECT_DIR is
#         the worktree, and the checked-out path is main branch)
# ===========================================================================
PROJ3="$SCRATCH/proj3"
WT3=$(make_fake_worktree "$PROJ3" "main")
# HOOK_CWD is the main repo root (not a worktree path) but CLAUDE_PROJECT_DIR is
RESULT3=$(run_guard "$PROJ3" "$WT3" "git commit -m 'bad via project dir'")

if echo "$RESULT3" | grep -q "DENIED"; then
    pass "test3: commit blocked via CLAUDE_PROJECT_DIR worktree detection + main branch"
else
    fail "test3: CLAUDE_PROJECT_DIR-based detection failed (got: $RESULT3)"
fi

# ===========================================================================
# TEST 4: guard IGNORES non-commit git commands (git status, git log, etc.)
# ===========================================================================
PROJ4="$SCRATCH/proj4"
WT4=$(make_fake_worktree "$PROJ4" "main")   # main branch — would trigger if wrong

RESULT4=$(run_guard "$WT4" "" "git status && git log --oneline -5")

if echo "$RESULT4" | grep -q "ALLOWED"; then
    pass "test4: non-commit git commands not blocked (even on main branch)"
else
    fail "test4: non-commit command was unexpectedly blocked: $RESULT4"
fi

# ===========================================================================
# TEST 5: guard IGNORES commands outside worktree paths
# ===========================================================================
RESULT5=$(run_guard "/Users/someone/myproject" "/Users/someone/myproject" "git commit -m 'normal commit'")

if echo "$RESULT5" | grep -q "ALLOWED"; then
    pass "test5: commit outside worktree path is allowed (not in agent-* dir)"
else
    fail "test5: non-worktree commit was blocked: $RESULT5"
fi

# ===========================================================================
# TEST 6: guard BLOCKS via input JSON cwd (production hook path)
# ===========================================================================
PROJ6="$SCRATCH/proj6"
WT6=$(make_fake_worktree "$PROJ6" "main")
INPUT_JSON6=$(python3 -c "
import json
print(json.dumps({'tool_input': {'cwd': '${WT6}', 'cmd': 'git commit -m bad'}}))
")

RESULT6=$(run_guard "" "" "git commit -m 'bad via json cwd'" "$INPUT_JSON6")

if echo "$RESULT6" | grep -q "DENIED"; then
    pass "test6: cwd from input JSON triggers block when branch is main"
else
    fail "test6: JSON cwd detection failed (got: $RESULT6)"
fi

# ===========================================================================
# Result
# ===========================================================================
echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "PASS (6/6 tests)"
    exit 0
else
    echo "FAILED"
    exit 1
fi
