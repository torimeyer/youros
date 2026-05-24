#!/usr/bin/env bash
# test_worktree_reaper_squashed.sh
#
# Self-test for the content-equiv absorption fix in worktree-reaper.sh.
#
# Creates a temp git repo with:
#   - A squash-merged branch: 1 commit ahead by hash, content already on main
#   - An unmerged branch: 1 commit ahead with unique content not yet on main
#
# Expects:
#   - Squash-merged branch → absorbed  (with "(squashed)" suffix)
#   - Unmerged branch      → unique
#
# Exit 0 on pass, 1 on fail.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAPER="$SCRIPT_DIR/worktree-reaper.sh"

if [ ! -f "$REAPER" ]; then
  echo "FAIL: reaper not found at $REAPER" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d /tmp/test-reaper-squash-XXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

MAIN_REPO="$WORK_DIR/repo"

# Init
git init "$MAIN_REPO" --quiet
git -C "$MAIN_REPO" config user.email "test@test.local"
git -C "$MAIN_REPO" config user.name "Test"
# Force branch name to 'main' regardless of git defaultBranch config
git -C "$MAIN_REPO" symbolic-ref HEAD refs/heads/main
git -C "$MAIN_REPO" commit --allow-empty -m "initial" --quiet

# Directory the reaper scans
mkdir -p "$MAIN_REPO/.claude/worktrees"

# -----------------------------------------------------------------
# Branch 1: worktree-agent-squashed-feature
#   Simulates a branch that was squash-merged: the exact same file
#   content exists on main via a separate commit, but the branch SHA
#   never appears in main's history.
# -----------------------------------------------------------------
git -C "$MAIN_REPO" checkout -b worktree-agent-squashed-feature --quiet
echo "feature content" > "$MAIN_REPO/feature.txt"
git -C "$MAIN_REPO" add feature.txt
git -C "$MAIN_REPO" commit -m "add feature" --quiet

# Back to main, apply identical content as a squash commit
git -C "$MAIN_REPO" checkout main --quiet
echo "feature content" > "$MAIN_REPO/feature.txt"
git -C "$MAIN_REPO" add feature.txt
git -C "$MAIN_REPO" commit -m "squash: add feature" --quiet

# worktree-agent-squashed-feature is now 1 ahead by hash, 0 diff vs main
git -C "$MAIN_REPO" worktree add \
  "$MAIN_REPO/.claude/worktrees/agent-squashed-feature" \
  worktree-agent-squashed-feature --quiet

# -----------------------------------------------------------------
# Branch 2: worktree-agent-unmerged
#   A branch with content that genuinely has not landed on main.
# -----------------------------------------------------------------
git -C "$MAIN_REPO" checkout -b worktree-agent-unmerged --quiet
echo "unique unmerged content $(date +%s%N)" > "$MAIN_REPO/unique.txt"
git -C "$MAIN_REPO" add unique.txt
git -C "$MAIN_REPO" commit -m "add unique work" --quiet
git -C "$MAIN_REPO" checkout main --quiet

git -C "$MAIN_REPO" worktree add \
  "$MAIN_REPO/.claude/worktrees/agent-unmerged" \
  worktree-agent-unmerged --quiet

# -----------------------------------------------------------------
# Run the reaper (dry-run) from inside the temp repo
# -----------------------------------------------------------------
output=$(cd "$MAIN_REPO" && bash "$REAPER" 2>&1 || true)

echo "=== reaper output ==="
echo "$output"
echo "=== end ==="

pass=1

if echo "$output" | grep -qE "worktree-agent-squashed-feature[[:space:]]+absorbed"; then
  echo "PASS: squashed branch classified as absorbed"
else
  echo "FAIL: squashed branch should be absorbed" >&2
  pass=0
fi

if echo "$output" | grep -q "squashed"; then
  echo "PASS: absorbed row shows (squashed) suffix"
else
  echo "FAIL: absorbed row should show (squashed) suffix" >&2
  pass=0
fi

if echo "$output" | grep -qE "worktree-agent-unmerged[[:space:]]+unique"; then
  echo "PASS: unmerged branch classified as unique"
else
  echo "FAIL: unmerged branch should be unique" >&2
  pass=0
fi

if echo "$output" | grep -q "absorbed=1"; then
  echo "PASS: summary shows absorbed=1"
else
  echo "FAIL: summary should show absorbed=1" >&2
  pass=0
fi

if echo "$output" | grep -q "unique=1"; then
  echo "PASS: summary shows unique=1"
else
  echo "FAIL: summary should show unique=1" >&2
  pass=0
fi

if [ "$pass" -eq 1 ]; then
  echo ""
  echo "ALL TESTS PASSED"
  exit 0
else
  echo ""
  echo "SOME TESTS FAILED" >&2
  exit 1
fi
