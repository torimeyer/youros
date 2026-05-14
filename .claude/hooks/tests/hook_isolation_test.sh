#!/usr/bin/env bash
# hook_isolation_test.sh  →1349
# Regression test: writing a file inside a worktree's .claude/hooks/ must NOT
# appear in the parent repo's .claude/hooks/.
#
# Run before fix → RED (symlink causes leakage).
# Run after fix  → GREEN (rsync copy isolates the directory).
#
# Usage: bash .claude/hooks/tests/hook_isolation_test.sh
# Exit 0 = pass, exit 1 = fail.

set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
# Allow caller to override the api dir for testing against a worktree's fixed
# spawn_isolation.py before the fix is merged to main.
API_DIR="${OVERRIDE_API_DIR:-$REPO_ROOT/api}"

TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT

MAIN_CLAUDE="$TMPDIR_TEST/main/.claude"
WT_CLAUDE="$TMPDIR_TEST/worktree/.claude"
mkdir -p "$MAIN_CLAUDE/hooks" "$MAIN_CLAUDE/lib"
mkdir -p "$TMPDIR_TEST/worktree"

# Seed a minimal hooks dir in "main"
echo '#!/bin/bash' > "$MAIN_CLAUDE/hooks/register-agent.sh"
echo '# lib'       > "$MAIN_CLAUDE/lib/helper.sh"
echo '{}'           > "$MAIN_CLAUDE/settings.json"

# Run sync_claude_dir_to_worktree (the spawn helper)
python3 - <<PYEOF
import asyncio, sys
sys.path.insert(0, "$API_DIR")
from services.spawn_isolation import sync_claude_dir_to_worktree
ok = asyncio.run(sync_claude_dir_to_worktree("$MAIN_CLAUDE", "$WT_CLAUDE"))
if not ok:
    print("FAIL: sync_claude_dir_to_worktree returned False", file=sys.stderr)
    sys.exit(1)
PYEOF

# Write a sentinel into the WORKTREE's hooks dir
SENTINEL="$WT_CLAUDE/hooks/_isolation_sentinel_$$.txt"
echo "worktree-only-$(date +%s)" > "$SENTINEL"

# The same path through the parent must NOT exist
MAIN_SENTINEL="$MAIN_CLAUDE/hooks/_isolation_sentinel_$$.txt"

if [ -e "$MAIN_SENTINEL" ]; then
    echo "FAIL: sentinel leaked into parent hooks dir!"
    echo "  worktree sentinel: $SENTINEL"
    echo "  parent copy:       $MAIN_SENTINEL"
    echo ""
    echo "  Root cause: $WT_CLAUDE/hooks is a symlink:"
    ls -la "$WT_CLAUDE/hooks" 2>/dev/null || echo "  (could not ls)"
    exit 1
fi

echo "PASS: worktree write stayed in worktree — no leakage into parent hooks dir."
exit 0
