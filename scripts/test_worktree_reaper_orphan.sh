#!/usr/bin/env bash
# Regression test: worktree-reaper.sh --apply must remove orphan dirs (→1333).
#
# An "orphan" is a dir under .claude/worktrees/agent-* that exists on disk but
# has NO entry in `git worktree list`. These accumulate when the backend restarts
# mid-spawn (event loop dies before _drain_stderr calls remove_worktree()), or
# when ghost_reaper/prune drops the registry row and git prune later removes the
# worktree registration while leaving the dir behind.
#
# The reaper's original loop iterated `git worktree list --porcelain` exclusively
# and was therefore BLIND to orphan dirs. This test verifies the phase-2 sweep
# added in →1333 finds and removes them.

set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAPER="$THIS_DIR/worktree-reaper.sh"

if [ ! -x "$REAPER" ]; then
  echo "FAIL: reaper not found or not executable at $REAPER" >&2
  exit 1
fi

TMPROOT="$(mktemp -d -t wt-reaper-orphan-XXXXXX)"
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

# ---- Case 1: plain orphan (never had a git registration) ----
# Simulates a dir left over after backend restart + git prune cleared registration.
mkdir -p .claude/worktrees/agent-orphan01/.ostk
echo "state" > .claude/worktrees/agent-orphan01/.ostk/state.json
echo "scratch" > .claude/worktrees/agent-orphan01/work.tmp

# ---- Case 2: orphan with .git file but no tracked changes ----
# Simulates: create_worktree() succeeded (added .git), git worktree remove
# removed the registration, shutil.rmtree was never called, dir stays.
git worktree add -q -b worktree-agent-orphan02 .claude/worktrees/agent-orphan02 main
# Simulate losing the registration: remove the git worktree entry directly,
# leaving the dir behind (mirrors what git prune does when gitdir is stale).
# We do this by removing then re-adding the registration so git prune won't
# flag it as stale, then manually deleting the .git/worktrees/<id> entry.
_wt_id=$(git worktree list --porcelain | awk '/^worktree.*orphan02/ {found=1; next} found && /^bare/ {print "bare"; exit} found && /^HEAD/ {found=0}' || true)
# Simpler: just `git worktree remove --force` but keep the dir manually.
git worktree remove --force .claude/worktrees/agent-orphan02 >/dev/null 2>&1 || true
# Re-create the dir without re-registering with git (pure orphan state).
mkdir -p .claude/worktrees/agent-orphan02/.ostk
echo "data" > .claude/worktrees/agent-orphan02/data.txt
git branch -D worktree-agent-orphan02 >/dev/null 2>&1 || true

# ---- Case 3: registered absorbed worktree (phase 1 handles this, not phase 2) ----
git worktree add -q -b worktree-agent-absorbed01 .claude/worktrees/agent-absorbed01 main

# ---- Case 4: registered UNIQUE worktree (must NOT be removed by either phase) ----
git worktree add -q -b worktree-agent-live01 .claude/worktrees/agent-live01 main
(
  cd .claude/worktrees/agent-live01
  echo "real work" > feature.py
  git add feature.py
  git commit -q -m "feat: add feature"
)

echo "=== verify setup ==="
echo "dirs on disk: $(ls .claude/worktrees/ | wc -l | tr -d ' ')"
echo "git worktree count: $(git worktree list --porcelain | grep -c '^worktree' | tr -d ' ')"

# --- DRY RUN ---
echo
echo "=== dry-run ==="
DRY_OUT="$(YOUROS_ACTIVE_AGENTS='' bash "$REAPER" 2>&1)"
echo "$DRY_OUT"

# Dry run must report the 2 orphan dirs.
if ! printf '%s\n' "$DRY_OUT" | grep -qE 'orphan.*2|2.*orphan'; then
  # Count lines mentioning "orphan (dry-run)"
  _orphan_lines=$(printf '%s\n' "$DRY_OUT" | grep -c 'orphan (dry-run)' || true)
  if [ "${_orphan_lines:-0}" -ne 2 ]; then
    echo "FAIL: dry-run should report 2 orphan dirs, got: $_orphan_lines" >&2
    echo "--- reaper output ---"
    printf '%s\n' "$DRY_OUT"
    exit 1
  fi
fi

# Dry run must NOT remove any orphan dirs.
if [ ! -d .claude/worktrees/agent-orphan01 ] || [ ! -d .claude/worktrees/agent-orphan02 ]; then
  echo "FAIL: dry-run removed orphan dirs" >&2
  exit 1
fi
# Dry run must NOT remove unique/live worktree.
if [ ! -d .claude/worktrees/agent-live01 ]; then
  echo "FAIL: dry-run removed the live worktree" >&2
  exit 1
fi

# --- APPLY ---
echo
echo "=== apply ==="
# REAPER_MIN_AGE_MINUTES=0 disables the →2608 recent-activity guard so this
# fixture (freshly created, young mtimes) still exercises the removal path.
APPLY_OUT="$(YOUROS_ACTIVE_AGENTS='' REAPER_MIN_AGE_MINUTES=0 bash "$REAPER" --apply 2>&1)"
echo "$APPLY_OUT"

# Orphan dirs must be gone after apply.
if [ -d .claude/worktrees/agent-orphan01 ]; then
  echo "FAIL: apply did not remove agent-orphan01" >&2
  exit 1
fi
if [ -d .claude/worktrees/agent-orphan02 ]; then
  echo "FAIL: apply did not remove agent-orphan02" >&2
  exit 1
fi

# The live worktree (unique: has commits) must still exist.
if [ ! -d .claude/worktrees/agent-live01 ]; then
  echo "FAIL: apply incorrectly removed the live/unique worktree" >&2
  exit 1
fi

# The absorbed worktree must have been removed by phase 1 (normal reaper).
if [ -d .claude/worktrees/agent-absorbed01 ]; then
  echo "FAIL: apply did not remove the absorbed worktree" >&2
  exit 1
fi

echo
echo "PASS: orphan sweep removes orphan dirs without touching live/unique worktrees"
exit 0
