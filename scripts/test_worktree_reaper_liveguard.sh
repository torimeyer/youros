#!/usr/bin/env bash
# test_worktree_reaper_liveguard.sh
#
# Regression test for →2608: the scheduled reaper service runs
# worktree-reaper.sh --apply every 15 minutes. A LIVE agent that has just
# merged main and not yet committed is diff-empty against main, so the
# absorbed/cherry-picked classification (→2590) marks it absorbed and the
# scheduled pass deletes its worktree mid-run. Two guards fix this:
#
#   1. lock guard : a worktree whose git lock is set (git worktree list
#      --porcelain shows `locked`) is NEVER removed, printed as
#      "skipped (locked)". The →2063 "--force --force for locked
#      worktrees" override no longer applies to registered worktrees.
#   2. age guard  : a worktree whose dir or any file inside was modified
#      within REAPER_MIN_AGE_MINUTES (default 30) is NEVER removed,
#      printed as "skipped (active <Nm)". Setting the env var to 0
#      disables the age guard.
#
# Fixture (all three worktrees are absorbed-shaped: 0 commits ahead, clean):
#   a) locked01 : locked + aged      -> expect "skipped (locked)", present
#   b) young01  : unlocked + fresh   -> expect "skipped (active", present
#   c) old01    : unlocked + aged    -> expect removed (existing behavior)
# Second --apply pass with REAPER_MIN_AGE_MINUTES=0:
#   b) young01 now removed (env override works, 0 disables the age guard)
#   a) locked01 STILL skipped (lock guard is unconditional)
#
# Exit 0 on pass, 1 on fail.

set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAPER="$THIS_DIR/worktree-reaper.sh"

if [ ! -x "$REAPER" ]; then
  echo "FAIL: reaper not found or not executable at $REAPER" >&2
  exit 1
fi

TMPROOT="$(mktemp -d -t wt-reaper-liveguard-XXXXXX)"
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

# (a) locked absorbed worktree. Aged below so ONLY the lock protects it.
git worktree add -q -b worktree-agent-locked01 .claude/worktrees/agent-locked01 main
git worktree lock --reason "live agent working" .claude/worktrees/agent-locked01

# (b) fresh absorbed worktree (young mtime, unlocked).
git worktree add -q -b worktree-agent-young01 .claude/worktrees/agent-young01 main

# (c) old unlocked absorbed worktree.
git worktree add -q -b worktree-agent-old01 .claude/worktrees/agent-old01 main

# Age (a) and (c): every dir and file gets an mtime 2 days in the past so
# the age guard cannot mask what we are testing. BSD date first (macOS),
# GNU date fallback.
OLD_STAMP="$(date -v-2d +%Y%m%d%H%M 2>/dev/null || date -d '2 days ago' +%Y%m%d%H%M)"
find .claude/worktrees/agent-locked01 -exec touch -t "$OLD_STAMP" {} +
find .claude/worktrees/agent-old01 -exec touch -t "$OLD_STAMP" {} +

fail=0

echo "--- apply pass 1 (default REAPER_MIN_AGE_MINUTES) ---"
OUT1="$(YOUROS_ACTIVE_AGENTS='' bash "$REAPER" --apply 2>&1)" || true
echo "$OUT1"

# (a) locked: skipped message, worktree and branch still present.
if ! printf '%s\n' "$OUT1" | grep -q "skipped (locked): worktree-agent-locked01"; then
  echo "FAIL: expected 'skipped (locked): worktree-agent-locked01' in output" >&2
  fail=1
fi
if [ ! -d .claude/worktrees/agent-locked01 ]; then
  echo "FAIL: locked worktree was removed" >&2
  fail=1
fi
if ! git show-ref --verify --quiet refs/heads/worktree-agent-locked01; then
  echo "FAIL: locked worktree's branch was deleted" >&2
  fail=1
fi

# (b) young: skipped message, worktree and branch still present.
if ! printf '%s\n' "$OUT1" | grep -q "skipped (active <30m): worktree-agent-young01"; then
  echo "FAIL: expected 'skipped (active <30m): worktree-agent-young01' in output" >&2
  fail=1
fi
if [ ! -d .claude/worktrees/agent-young01 ]; then
  echo "FAIL: young (recently active) worktree was removed" >&2
  fail=1
fi
if ! git show-ref --verify --quiet refs/heads/worktree-agent-young01; then
  echo "FAIL: young worktree's branch was deleted" >&2
  fail=1
fi

# (c) old + unlocked: removed (existing absorbed behavior preserved).
if ! printf '%s\n' "$OUT1" | grep -q "removed worktree-agent-old01"; then
  echo "FAIL: expected 'removed worktree-agent-old01' in output" >&2
  fail=1
fi
if [ -d .claude/worktrees/agent-old01 ]; then
  echo "FAIL: old absorbed worktree was NOT removed" >&2
  fail=1
fi
if git show-ref --verify --quiet refs/heads/worktree-agent-old01; then
  echo "FAIL: old absorbed worktree's branch was NOT deleted" >&2
  fail=1
fi

echo "--- apply pass 2 (REAPER_MIN_AGE_MINUTES=0 disables the age guard) ---"
OUT2="$(YOUROS_ACTIVE_AGENTS='' REAPER_MIN_AGE_MINUTES=0 bash "$REAPER" --apply 2>&1)" || true
echo "$OUT2"

# (b) young01 is now removable: the env override works.
if ! printf '%s\n' "$OUT2" | grep -q "removed worktree-agent-young01"; then
  echo "FAIL: with REAPER_MIN_AGE_MINUTES=0, young worktree should be removed" >&2
  fail=1
fi
if [ -d .claude/worktrees/agent-young01 ]; then
  echo "FAIL: with REAPER_MIN_AGE_MINUTES=0, young worktree dir still present" >&2
  fail=1
fi

# (a) locked01 is STILL skipped: the lock guard is unconditional.
if ! printf '%s\n' "$OUT2" | grep -q "skipped (locked): worktree-agent-locked01"; then
  echo "FAIL: lock guard must hold even with REAPER_MIN_AGE_MINUTES=0" >&2
  fail=1
fi
if [ ! -d .claude/worktrees/agent-locked01 ]; then
  echo "FAIL: locked worktree was removed on second pass" >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo
  echo "SOME TESTS FAILED" >&2
  exit 1
fi

echo
echo "PASS: reaper skips locked and recently-active worktrees, removes only old unlocked absorbed ones."
