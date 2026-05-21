#!/usr/bin/env bash
# test_agent_branch.sh — verify agent-branch.sh behavior (→1553)
set -uo pipefail  # no -e so we can collect failures

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CMD="$SCRIPT_DIR/agent-branch.sh"

pass=0; fail=0

check() {
    local desc="$1" expected="$2"; shift 2
    local actual rc
    actual=$("$@" 2>/dev/null); rc=$?
    if [[ $rc -ne 0 ]]; then actual="__ERROR__"; fi
    if [[ "$actual" == "$expected" ]]; then
        echo "  PASS: $desc"
        pass=$((pass + 1))
    else
        echo "  FAIL: $desc"
        echo "        expected: $expected"
        echo "        got:      $actual"
        fail=$((fail + 1))
    fi
}

check_err() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  FAIL: $desc (expected non-zero exit)"
        fail=$((fail + 1))
    else
        echo "  PASS: $desc (correctly errored)"
        pass=$((pass + 1))
    fi
}

echo "=== agent-branch.sh tests ==="

# --- Formula (--wt-id): short name passes through unchanged ---
check "short name ≤30 chars: wt_id is unchanged" \
    "n9-short" \
    "$CMD" --wt-id "n9-short"

# --- Formula (--wt-id): long name gets blake2s suffix ---
check "long name: wt_id uses blake2s suffix" \
    "n9-worktree-branch-lo-bdef4fd7" \
    "$CMD" --wt-id "n9-worktree-branch-lookup-helper-ce3a90"

# --- End-to-end agent name: derive branch from my own worktree ---
check "agent name: returns correct branch" \
    "worktree-agent-n9-worktree-branch-lo-bdef4fd7" \
    "$CMD" "n9-worktree-branch-lookup-helper-ce3a90"

# --- Path input: my own worktree ---
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# Derive the main repo root (script runs from a worktree, worktrees live in the main repo)
GIT_COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null)"
MAIN_ROOT="${GIT_COMMON_DIR%/.git}"
MY_WT="$MAIN_ROOT/.claude/worktrees/agent-n9-worktree-branch-lo-bdef4fd7"
if [[ -d "$MY_WT" ]]; then
    check "path input: returns branch from git" \
        "worktree-agent-n9-worktree-branch-lo-bdef4fd7" \
        "$CMD" "$MY_WT"
else
    echo "  SKIP: path test (worktree not present in this environment)"
fi

# --- Error cases ---
check_err "no args: exits non-zero" "$CMD"
check_err "missing worktree: exits non-zero" "$CMD" "ghost-agent-000000"
check_err "missing path: exits non-zero" "$CMD" "/tmp/no-such-worktree-here"

echo ""
echo "Results: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
