#!/usr/bin/env bash
# test_worktree_reaper_cherrypicked.sh
#
# Self-test for the patch-id absorption fix in worktree-reaper.sh (→2590).
#
# The orchestrator lands agent work by cherry-pick, which rewrites the
# commit SHA. If main then advances further, the branch is ahead by
# rev-list AND has a non-empty content diff against main, so the old
# reaper classified it "unique" forever. `git cherry main <branch>`
# marks patch-equivalent commits with '-'; the reaper must treat a
# branch whose ahead commits are ALL patch-equivalent as absorbed.
#
# Creates a temp git repo with:
#   a) cherry-picked branch: commit cherry-picked onto main, main then
#      advanced with an unrelated commit → expect absorbed (cherry-picked)
#   b) genuinely unmerged branch → expect unique + REFUSING message
#   c) cherry-picked branch whose worktree has uncommitted changes
#      → expect unique (dirty), never absorbed
#
# Exit 0 on pass, 1 on fail.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAPER="$SCRIPT_DIR/worktree-reaper.sh"

if [ ! -f "$REAPER" ]; then
  echo "FAIL: reaper not found at $REAPER" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d /tmp/test-reaper-cherry-XXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

MAIN_REPO="$WORK_DIR/repo"

git init "$MAIN_REPO" --quiet
git -C "$MAIN_REPO" config user.email "test@test.local"
git -C "$MAIN_REPO" config user.name "Test"
git -C "$MAIN_REPO" symbolic-ref HEAD refs/heads/main
git -C "$MAIN_REPO" commit --allow-empty -m "initial" --quiet

mkdir -p "$MAIN_REPO/.claude/worktrees"

# -----------------------------------------------------------------
# Branch a: worktree-agent-cherry-landed
#   Commit is cherry-picked onto main (new SHA, same patch-id), and
#   main then advances with an unrelated commit so the content diff
#   main..branch is NOT empty.
# -----------------------------------------------------------------
git -C "$MAIN_REPO" checkout -b worktree-agent-cherry-landed --quiet
echo "cherry content" > "$MAIN_REPO/cherry.txt"
git -C "$MAIN_REPO" add cherry.txt
git -C "$MAIN_REPO" commit -m "add cherry feature" --quiet
cherry_sha=$(git -C "$MAIN_REPO" rev-parse HEAD)

git -C "$MAIN_REPO" checkout main --quiet

# Advance main FIRST so the cherry-pick lands on a different parent and
# is guaranteed a rewritten SHA, and so the branch tree differs from
# main tip (non-empty diff).
echo "later mainline work" > "$MAIN_REPO/mainline.txt"
git -C "$MAIN_REPO" add mainline.txt
git -C "$MAIN_REPO" commit -m "unrelated mainline commit" --quiet

git -C "$MAIN_REPO" cherry-pick "$cherry_sha" --quiet

git -C "$MAIN_REPO" worktree add \
  "$MAIN_REPO/.claude/worktrees/agent-cherry-landed" \
  worktree-agent-cherry-landed --quiet

# Sanity: fixture must be ahead by SHA with a non-empty diff,
# otherwise we are accidentally testing the squashed path.
ahead=$(git -C "$MAIN_REPO" rev-list --count main..worktree-agent-cherry-landed)
if [ "$ahead" -eq 0 ]; then
  echo "FAIL: fixture broken, cherry branch not ahead of main" >&2
  exit 1
fi
if git -C "$MAIN_REPO" diff --quiet main..worktree-agent-cherry-landed; then
  echo "FAIL: fixture broken, diff is empty (squashed path, not cherry path)" >&2
  exit 1
fi

# -----------------------------------------------------------------
# Branch b: worktree-agent-unmerged
#   Content genuinely not on main.
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
# Branch c: worktree-agent-cherry-dirty
#   Same cherry-picked shape as (a), but the worktree has an
#   uncommitted modification. Must NOT be absorbed.
# -----------------------------------------------------------------
git -C "$MAIN_REPO" checkout -b worktree-agent-cherry-dirty --quiet
echo "dirty cherry content" > "$MAIN_REPO/dirtycherry.txt"
git -C "$MAIN_REPO" add dirtycherry.txt
git -C "$MAIN_REPO" commit -m "add dirty cherry feature" --quiet
dirty_sha=$(git -C "$MAIN_REPO" rev-parse HEAD)

git -C "$MAIN_REPO" checkout main --quiet
git -C "$MAIN_REPO" cherry-pick "$dirty_sha" --quiet

git -C "$MAIN_REPO" worktree add \
  "$MAIN_REPO/.claude/worktrees/agent-cherry-dirty" \
  worktree-agent-cherry-dirty --quiet

# Uncommitted tracked change inside the worktree.
echo "in-flight edit" >> "$MAIN_REPO/.claude/worktrees/agent-cherry-dirty/dirtycherry.txt"

# -----------------------------------------------------------------
# Run the reaper (dry-run) from inside the temp repo
# -----------------------------------------------------------------
output=$(cd "$MAIN_REPO" && bash "$REAPER" 2>&1 || true)

echo "=== reaper output ==="
echo "$output"
echo "=== end ==="

pass=1

if echo "$output" | grep -qE "worktree-agent-cherry-landed[[:space:]]+absorbed"; then
  echo "PASS: cherry-picked branch classified as absorbed"
else
  echo "FAIL: cherry-picked branch should be absorbed" >&2
  pass=0
fi

if echo "$output" | grep -q "cherry-picked"; then
  echo "PASS: absorbed row shows (cherry-picked) marker"
else
  echo "FAIL: absorbed row should show (cherry-picked) marker" >&2
  pass=0
fi

if echo "$output" | grep -qE "worktree-agent-unmerged[[:space:]]+unique"; then
  echo "PASS: unmerged branch classified as unique"
else
  echo "FAIL: unmerged branch should be unique" >&2
  pass=0
fi

if echo "$output" | grep -q "REFUSING to delete worktree-agent-unmerged"; then
  echo "PASS: REFUSING safety message intact for unmerged branch"
else
  echo "FAIL: REFUSING safety message missing for unmerged branch" >&2
  pass=0
fi

if echo "$output" | grep -qE "worktree-agent-cherry-dirty[[:space:]]+unique"; then
  echo "PASS: dirty cherry-picked worktree stays unique (not removed)"
else
  echo "FAIL: dirty cherry-picked worktree must not be absorbed" >&2
  pass=0
fi

if echo "$output" | grep -qE "worktree-agent-cherry-dirty[[:space:]]+absorbed"; then
  echo "FAIL: dirty cherry-picked worktree was marked absorbed" >&2
  pass=0
fi

if echo "$output" | grep -q "absorbed=1"; then
  echo "PASS: summary shows absorbed=1"
else
  echo "FAIL: summary should show absorbed=1" >&2
  pass=0
fi

if echo "$output" | grep -q "unique=2"; then
  echo "PASS: summary shows unique=2"
else
  echo "FAIL: summary should show unique=2" >&2
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
