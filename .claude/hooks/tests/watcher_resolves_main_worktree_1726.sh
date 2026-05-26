#!/usr/bin/env bash
# Test: session-start.sh must resolve WATCHER_SCRIPT via git main worktree,
# not via raw $CWD (which can be a stale worktree path).
# →1726: watcher cross-worktree contamination fix.
#
# RED: fails before the fix (line 154 uses $CWD verbatim)
# GREEN: passes after fix (uses git worktree list --porcelain)
set -euo pipefail

PASS=0
FAIL=0

pass() { echo "PASS: $1"; ((PASS++)) || true; }
fail() { echo "FAIL: $1"; ((FAIL++)) || true; }

SESSION_START="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/session-start.sh"

# ---- Test 1: session-start.sh must NOT reference $CWD for WATCHER_SCRIPT ----
# The raw pattern `WATCHER_SCRIPT="$CWD/` means the path is never resolved
# through git and will point at whatever $CWD is — which may be a worktree.
if grep -qF 'WATCHER_SCRIPT="$CWD/' "$SESSION_START"; then
    fail "session-start.sh still uses raw \$CWD for WATCHER_SCRIPT (→1726 not fixed)"
else
    pass "session-start.sh no longer uses raw \$CWD for WATCHER_SCRIPT"
fi

# ---- Test 2: session-start.sh must use git worktree resolution ----
# After the fix, the script must resolve the main worktree via
# `git worktree list --porcelain` (or equivalent).
if grep -q 'worktree list' "$SESSION_START"; then
    pass "session-start.sh uses git worktree list to resolve main worktree"
else
    fail "session-start.sh does not use git worktree list (→1726 fix missing)"
fi

# ---- Test 3: WATCHER_SCRIPT must NOT start with $CWD directly ----
# The raw form `WATCHER_SCRIPT="$CWD/...` is banned; a fallback `:-$CWD` is OK.
# We check: is there a line where $CWD is the PRIMARY (not fallback) source?
DIRECT_CWD=$(grep -E 'WATCHER_SCRIPT="\$CWD/' "$SESSION_START" || true)
if [ -n "$DIRECT_CWD" ]; then
    fail "WATCHER_SCRIPT uses \$CWD as primary path (not just fallback): $DIRECT_CWD"
else
    pass "WATCHER_SCRIPT does not use raw \$CWD as primary path"
fi

# ---- Test 4: watcher script file itself is git-tracked (sanity check) ----
REPO_ROOT=$(git -C "$(dirname "$SESSION_START")" rev-parse --show-toplevel 2>/dev/null || true)
if [ -n "$REPO_ROOT" ]; then
    WATCHER_REL=".claude/hooks/lib/agent-completion-watcher.sh"
    if git -C "$REPO_ROOT" ls-files --error-unmatch "$WATCHER_REL" &>/dev/null; then
        pass "agent-completion-watcher.sh is git-tracked (confirms it needs main-worktree resolution)"
    else
        fail "agent-completion-watcher.sh is NOT git-tracked (unexpected)"
    fi
else
    pass "SKIP: not in git repo (no repo root found)"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
