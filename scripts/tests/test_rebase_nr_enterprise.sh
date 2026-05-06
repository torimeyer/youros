#!/usr/bin/env bash
# Tests for scripts/rebase-nr-enterprise.sh
# Run: bash scripts/tests/test_rebase_nr_enterprise.sh
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/rebase-nr-enterprise.sh"

PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

echo ""
echo "Running tests for scripts/rebase-nr-enterprise.sh"
echo "────────────────────────────────────────────────────"

# ── Test 1: --help exits 0 and prints usage ───────────────────────────────────
echo ""
echo "Test 1: --help exits 0 and prints usage"
if output=$(bash "$SCRIPT" --help 2>&1); then
  if echo "$output" | grep -q "Usage:"; then
    pass "--help exited 0 and output contains 'Usage:'"
  else
    fail "--help exited 0 but output missing 'Usage:'"
  fi
else
  fail "--help exited non-zero"
fi

# ── Test 2: --cleanup on clean repo exits 0 ───────────────────────────────────
echo ""
echo "Test 2: --cleanup on repo with no stale worktrees exits 0"
# First make sure there are no stale worktrees from a prior run
if output=$(bash "$SCRIPT" --cleanup 2>&1); then
  if echo "$output" | grep -qE "(No .tmp-nr-rebase|Cleanup complete)"; then
    pass "--cleanup exits 0 with no stale worktrees"
  else
    fail "--cleanup exited 0 but output unexpected: $output"
  fi
else
  fail "--cleanup exited non-zero on clean repo"
fi

# ── Test 3: dirty working tree exits non-zero with clear error ────────────────
echo ""
echo "Test 3: dirty working tree exits non-zero with clear error"

# Create a temp dirty file in the repo root to simulate dirty tree
DIRTY_FILE="$REPO_ROOT/.tmp-test-dirty-$$"
echo "dirty" > "$DIRTY_FILE"

# We need to be on main (or appear to be) for the branch check to pass first.
# If we're not on main, the branch check fires before the dirty check.
# Either way we want to confirm exit non-zero with a clear message.
CURRENT_BRANCH=$(cd "$REPO_ROOT" && git rev-parse --abbrev-ref HEAD)

if output=$(cd "$REPO_ROOT" && bash "$SCRIPT" 2>&1); then
  fail "Should have exited non-zero with dirty working tree, but exited 0"
else
  if echo "$output" | grep -qiE "(dirty|dirty.*stash|Working tree|must be on|not up to date)"; then
    pass "Exits non-zero with clear error message when tree is dirty or not on main"
  else
    fail "Exited non-zero but error message not descriptive: $output"
  fi
fi

rm -f "$DIRTY_FILE"

# ── Test 4: delta-file path array matches spec ────────────────────────────────
echo ""
echo "Test 4: delta-file path array matches the brief's specification"

EXPECTED_PATHS=(
  "api/main.py"
  "api/services/claude_code_provider.py"
  "api/routers/settings.py"
  "app/src/components/OnboardingWizard.tsx"
)

SCRIPT_CONTENT=$(cat "$SCRIPT")
ALL_FOUND=1
for expected in "${EXPECTED_PATHS[@]}"; do
  if ! echo "$SCRIPT_CONTENT" | grep -qF "\"$expected\""; then
    fail "delta-file not found in script: $expected"
    ALL_FOUND=0
  fi
done

if [[ $ALL_FOUND -eq 1 ]]; then
  pass "All 4 expected delta-file paths present in DELTA_FILES array"
fi

# Check count: exactly 4 entries in DELTA_FILES
DELTA_BLOCK=$(echo "$SCRIPT_CONTENT" | awk '/^DELTA_FILES=\(/{flag=1} flag{print} /^\)/{if(flag) {flag=0}}')
DELTA_COUNT=$(echo "$DELTA_BLOCK" | grep -c '"' || true)
if [[ "$DELTA_COUNT" -eq 4 ]]; then
  pass "DELTA_FILES array has exactly 4 entries"
else
  fail "DELTA_FILES array has $DELTA_COUNT entries (expected 4)"
fi

# ── Test 5: --help mentions all 4 delta-files ─────────────────────────────────
echo ""
echo "Test 5: --help output mentions all 4 delta-files"
HELP_OUTPUT=$(bash "$SCRIPT" --help 2>&1)
HELP_ALL_FOUND=1
for expected in "${EXPECTED_PATHS[@]}"; do
  if ! echo "$HELP_OUTPUT" | grep -qF "$expected"; then
    fail "--help output missing delta-file: $expected"
    HELP_ALL_FOUND=0
  fi
done
if [[ $HELP_ALL_FOUND -eq 1 ]]; then
  pass "--help lists all 4 delta-files"
fi

# ── Test 6: unknown flag exits non-zero ───────────────────────────────────────
echo ""
echo "Test 6: unknown flag exits non-zero"
if bash "$SCRIPT" --not-a-real-flag 2>&1 | grep -q "Unknown argument"; then
  pass "Unknown flag exits with 'Unknown argument' message"
else
  fail "Unknown flag did not produce expected error"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────────────"
TOTAL=$((PASS + FAIL))
echo "Results: $PASS/$TOTAL passed"
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo "FAIL — $FAIL test(s) failed"
  exit 1
else
  echo "ALL TESTS PASSED"
  exit 0
fi
