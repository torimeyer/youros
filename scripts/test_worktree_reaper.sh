#!/usr/bin/env bash
# Regression test: worktree-reaper.sh must NOT delete a worktree whose
# branch has unmerged commits ahead of main.
#
# Reproduces the pattern that caused live agent worktrees (e.g.
# implement-about-youros-page-third-9d81d4) to be silently deleted when
# the reaper used git-diff to detect "absorbed" status. The diff approach
# returned empty when the branch content was byte-identical to main (e.g.
# after a prior cherry-pick), even though the branch had new commits.

set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAPER="$THIS_DIR/worktree-reaper.sh"

if [ ! -x "$REAPER" ]; then
  echo "FAIL: reaper not found or not executable at $REAPER" >&2
  exit 1
fi

TMPROOT="$(mktemp -d -t wt-reaper-unmerged-XXXXXX)"
cleanup() {
  if [ -n "${TMPROOT:-}" ] && [ -d "$TMPROOT" ]; then
    chmod -R u+w "$TMPROOT" 2>/dev/null || true
    rm -rf "$TMPROOT" 2>/dev/null || true
  fi
}
trap cleanup EXIT

FIXTURE="$TMPROOT/repo"
mkdir -p "$FIXTURE"
cd "$FIXTURE"

git init -q -b main .
git config user.email "reaper-test@example.com"
git config user.name "reaper-test"
echo "seed" > README
git add README
git commit -q -m "seed"

mkdir -p .claude/worktrees

# Worktree: branch has 1 commit ahead of main.
# This simulates a subagent that committed successfully to its branch
# but whose worktree hadn't been merged to main yet.
git worktree add -q -b worktree-agent-live01 .claude/worktrees/agent-live01 main
(
  cd .claude/worktrees/agent-live01
  echo "about page content" > about.tsx
  git add about.tsx
  git commit -q -m "feat(about): add about page"
)

# Sanity: confirm the branch really is 1 commit ahead of main.
ahead=$(git rev-list --count "main..worktree-agent-live01")
if [ "$ahead" -ne 1 ]; then
  echo "FAIL: test setup error -- expected 1 commit ahead of main, got $ahead" >&2
  exit 1
fi

# Also create an absorbed worktree (no commits ahead) to confirm the
# reaper still works correctly for the normal case.
git worktree add -q -b worktree-agent-absorbed01 .claude/worktrees/agent-absorbed01 main

echo "--- dry run ---"
DRY_OUT="$(bash "$REAPER" 2>&1)"
echo "$DRY_OUT"

# Dry run: absorbed=1 (absorbed01), unique=1 (live01).
if ! printf '%s\n' "$DRY_OUT" | grep -q 'absorbed=1'; then
  echo "FAIL: dry-run did not report absorbed=1" >&2
  exit 1
fi
if ! printf '%s\n' "$DRY_OUT" | grep -q 'unique=1'; then
  echo "FAIL: dry-run did not report unique=1" >&2
  exit 1
fi
# Dry run must not touch anything.
if [ ! -d .claude/worktrees/agent-live01 ]; then
  echo "FAIL: dry-run removed the live worktree" >&2
  exit 1
fi

echo "--- apply (must skip unmerged worktree) ---"
# →947 fail-safe: --apply refuses all removals unless the active-agent fleet
# can be loaded from YOUROS_ACTIVE_AGENTS or .ostk/agent_state.json. This
# fixture repo provides neither, so pass an explicitly empty fleet
# (set-but-empty = zero active agents), same convention as
# test_worktree_reaper_orphan.sh (→2605).
APPLY_OUT="$(YOUROS_ACTIVE_AGENTS='' bash "$REAPER" --apply 2>&1)"
echo "$APPLY_OUT"

# 1. Worktree with unmerged commits must survive.
if [ ! -d .claude/worktrees/agent-live01 ]; then
  echo "FAIL: --apply removed the worktree with unmerged commits" >&2
  exit 1
fi

# 2. Branch with unmerged commits must survive.
if ! git show-ref --verify --quiet refs/heads/worktree-agent-live01; then
  echo "FAIL: --apply deleted the branch with unmerged commits" >&2
  exit 1
fi

# 3. Reaper must log that it skipped due to unmerged commits. The reaper
# phrases it as "N unmerged commit(s) ahead of main", so match the
# singular stem, not the literal plural (→2605).
if ! printf '%s\n' "$APPLY_OUT" | grep -qi "unmerged commit"; then
  echo "FAIL: reaper did not log 'unmerged commit' in output" >&2
  echo "  Full output: $APPLY_OUT" >&2
  exit 1
fi

# 4. Absorbed worktree was removed (correct behavior preserved).
if [ -d .claude/worktrees/agent-absorbed01 ]; then
  echo "FAIL: --apply did not remove the absorbed worktree" >&2
  exit 1
fi
if git show-ref --verify --quiet refs/heads/worktree-agent-absorbed01; then
  echo "FAIL: --apply did not delete the absorbed branch" >&2
  exit 1
fi

# 5. rev-list count is still 1 (unmerged commit untouched).
still_ahead=$(git rev-list --count "main..worktree-agent-live01")
if [ "$still_ahead" -ne 1 ]; then
  echo "FAIL: rev-list count changed; expected 1, got $still_ahead" >&2
  exit 1
fi

echo
echo "PASS: reaper skips worktrees with unmerged commits and removes only absorbed ones."
