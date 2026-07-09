#!/usr/bin/env bash
# Test for scripts/worktree-reaper.sh
#
# Sets up a disposable git repo with two agent worktrees (one absorbed,
# one with unique commits), runs the reaper in dry-run and --apply modes,
# and asserts the counts/filesystem state. Always cleans up on exit.

set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/../.." && pwd)"
REAPER="$REPO_ROOT/scripts/worktree-reaper.sh"

if [ ! -x "$REAPER" ]; then
  echo "FAIL: reaper not found or not executable at $REAPER" >&2
  exit 1
fi

TMPROOT="$(mktemp -d -t wt-reaper-XXXXXX)"
cleanup() {
  # best-effort cleanup; ignore failures since we may be recovering from a
  # partial test run
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

# Worktree 1: absorbed (branch points at main, no unique diff).
git worktree add -q -b worktree-agent-absorbed01 .claude/worktrees/agent-absorbed01 main

# Worktree 2: unique (branch has a new commit not on main).
git worktree add -q -b worktree-agent-unique01 .claude/worktrees/agent-unique01 main
(
  cd .claude/worktrees/agent-unique01
  echo "unique content" > unique-file.txt
  git add unique-file.txt
  git commit -q -m "unique work"
)

# --- Dry run ---
echo "--- dry run ---"
DRY_OUT="$(bash "$REAPER" 2>&1)"
echo "$DRY_OUT"

if ! printf '%s\n' "$DRY_OUT" | grep -q 'absorbed=1'; then
  echo "FAIL: dry-run did not report absorbed=1" >&2
  exit 1
fi
if ! printf '%s\n' "$DRY_OUT" | grep -q 'unique=1'; then
  echo "FAIL: dry-run did not report unique=1" >&2
  exit 1
fi
# Nothing should have been removed.
if [ ! -d .claude/worktrees/agent-absorbed01 ]; then
  echo "FAIL: dry-run removed the absorbed worktree" >&2
  exit 1
fi
if [ ! -d .claude/worktrees/agent-unique01 ]; then
  echo "FAIL: dry-run removed the unique worktree" >&2
  exit 1
fi

# --- Apply without fleet signal: →947 fail-safe must refuse ---
# The reaper refuses --apply removals unless it can load the active-agent
# fleet from YOUROS_ACTIVE_AGENTS or .ostk/agent_state.json. This fixture
# repo provides neither, so the run must exit 1 and remove nothing (→2605).
echo "--- apply without fleet signal (947 fail-safe) ---"
set +e
FAILSAFE_OUT="$(env -u YOUROS_ACTIVE_AGENTS bash "$REAPER" --apply 2>&1)"
failsafe_rc=$?
set -e
echo "$FAILSAFE_OUT"
if [ "$failsafe_rc" -ne 1 ]; then
  echo "FAIL: --apply without fleet signal should exit 1 (fail-safe), got $failsafe_rc" >&2
  exit 1
fi
if ! printf '%s\n' "$FAILSAFE_OUT" | grep -q 'skipping all removals'; then
  echo "FAIL: fail-safe did not log 'skipping all removals'" >&2
  exit 1
fi
if [ ! -d .claude/worktrees/agent-absorbed01 ]; then
  echo "FAIL: fail-safe run removed the absorbed worktree" >&2
  exit 1
fi

# --- Apply (with fleet signal) ---
# Set-but-empty YOUROS_ACTIVE_AGENTS means "fleet loaded, zero active
# agents", which lets removals proceed. Same convention as
# test_worktree_reaper_orphan.sh (→2605).
echo "--- apply ---"
APPLY_OUT="$(YOUROS_ACTIVE_AGENTS='' bash "$REAPER" --apply 2>&1)"
echo "$APPLY_OUT"

if [ -d .claude/worktrees/agent-absorbed01 ]; then
  echo "FAIL: --apply did not remove the absorbed worktree dir" >&2
  exit 1
fi
if git show-ref --verify --quiet refs/heads/worktree-agent-absorbed01; then
  echo "FAIL: --apply did not delete the absorbed branch" >&2
  exit 1
fi
if [ ! -d .claude/worktrees/agent-unique01 ]; then
  echo "FAIL: --apply removed the unique worktree (should have parked)" >&2
  exit 1
fi
if ! git show-ref --verify --quiet refs/heads/worktree-agent-unique01; then
  echo "FAIL: --apply deleted the unique branch (should have parked)" >&2
  exit 1
fi

# --- Bad arg ---
echo "--- bad arg ---"
set +e
bash "$REAPER" --bogus >/dev/null 2>&1
rc=$?
set -e
if [ "$rc" -ne 2 ]; then
  echo "FAIL: bad arg should exit 2, got $rc" >&2
  exit 1
fi

echo
echo "PASS: worktree-reaper.sh classifies, reaps, and parks correctly."
