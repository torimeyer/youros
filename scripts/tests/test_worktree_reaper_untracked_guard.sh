#!/usr/bin/env bash
# Regression test: worktree-reaper.sh must NOT delete a worktree that has
# untracked new files, even when there are 0 commits ahead of main and no
# staged/unstaged tracked changes.
#
# Root cause of →2466 (2026-07-02 incident): the reaper's dirty-tree check
# used only `git diff --quiet` (tracked changes); new files written to the
# worktree but never `git add`-ed were invisible to it.  The fix adds a
# `git ls-files --others --exclude-standard` check, mirroring the guard
# already present in spawn_isolation.py:remove_worktree.

set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/../.." && pwd)"
REAPER="$REPO_ROOT/scripts/worktree-reaper.sh"

if [ ! -x "$REAPER" ]; then
  echo "FAIL: reaper not found or not executable at $REAPER" >&2
  exit 1
fi

TMPROOT="$(mktemp -d -t wt-untracked-XXXXXX)"
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

# Create a worktree with 0 commits ahead of main (scaffold commit only — empty diff).
git worktree add -q -b worktree-agent-with-report .claude/worktrees/agent-with-report main

# Write a "report" as an untracked file — never staged, never committed.
# This simulates an agent that wrote an 18,955-byte deliverable and then
# stopped before committing.
echo "IMPORTANT REPORT CONTENT (18955 bytes of agent deliverable)" \
  > .claude/worktrees/agent-with-report/agent-report.md

# Confirm: the worktree has 0 commits ahead of main
AHEAD=$(git rev-list --count "main..worktree-agent-with-report" 2>/dev/null || echo "err")
if [ "$AHEAD" != "0" ]; then
  echo "FAIL: test setup error — expected 0 commits ahead, got $AHEAD" >&2
  exit 1
fi

# Confirm: tracked dirty check passes (no staged/unstaged tracked changes)
if ! git -C .claude/worktrees/agent-with-report diff --quiet 2>/dev/null; then
  echo "FAIL: test setup error — unexpected tracked changes" >&2
  exit 1
fi
if ! git -C .claude/worktrees/agent-with-report diff --cached --quiet 2>/dev/null; then
  echo "FAIL: test setup error — unexpected staged changes" >&2
  exit 1
fi

# Confirm: the untracked file IS there
UNTRACKED=$(git -C .claude/worktrees/agent-with-report ls-files --others --exclude-standard 2>/dev/null)
if [ -z "$UNTRACKED" ]; then
  echo "FAIL: test setup error — expected untracked file, none found" >&2
  exit 1
fi

# ── Test 1: dry-run classifies worktree as unique (NOT absorbed) ──────────
echo "=== test 1: dry-run classifies worktree with untracked file as unique ==="
DRY_OUT=$(bash "$REAPER" 2>&1 || true)
echo "$DRY_OUT"

if printf '%s\n' "$DRY_OUT" | grep -q 'worktree-agent-with-report.*absorbed'; then
  echo "FAIL: reaper classified worktree with untracked file as absorbed (would delete it)" >&2
  exit 1
fi
if printf '%s\n' "$DRY_OUT" | grep -qE 'absorbed=[1-9]'; then
  echo "FAIL: reaper reports absorbed > 0 (should be 0 since the only worktree has untracked files)" >&2
  exit 1
fi
echo "PASS: dry-run did not classify worktree as absorbed"

# ── Test 2: --apply does NOT delete the worktree ─────────────────────────
echo "=== test 2: --apply refuses to delete worktree with untracked file ==="
# active-agent guard: pass empty YOUROS_ACTIVE_AGENTS so the guard is
# satisfied (no active agents to protect) and any absorbed worktrees would
# be removed.  The untracked guard is the only thing standing between the
# worktree and deletion.
YOUROS_ACTIVE_AGENTS="" bash "$REAPER" --apply 2>&1 || true

if [ ! -d .claude/worktrees/agent-with-report ]; then
  echo "FAIL: reaper deleted worktree with untracked file (agent-report.md lost)" >&2
  exit 1
fi
if [ ! -f .claude/worktrees/agent-with-report/agent-report.md ]; then
  echo "FAIL: agent-report.md was deleted" >&2
  exit 1
fi
echo "PASS: worktree with untracked file was NOT deleted"

echo ""
echo "all tests passed"
