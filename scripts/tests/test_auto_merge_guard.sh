#!/usr/bin/env bash
# Regression tests for the ostk-agent-stop.sh auto-merge guards (→2466).
#
# Two incidents drove this:
#   2026-07-02: a worktree with untracked deliverables was deleted after
#               the hook merged a clean branch onto main (dirty check was
#               missing, so the reaper saw "absorbed" and deleted it).
#   2026-07-06: auto-merge fired when a duplicate registry row completed,
#               fast-forwarding main before the orchestrator could verify.
#
# Fixes:
#   Guard 1 — opt-in: AGENT_AUTO_MERGE_ENABLED must be "1" (default off).
#   Guard 2 — dirty check: refuse merge when worktree has uncommitted tracked
#             changes OR untracked new files.

set -uo pipefail

HOOK="$(cd "$(dirname "$0")/../.." && pwd)/.claude/hooks/ostk-agent-stop.sh"
# Also accept the hook from the main project .claude/hooks (not in worktree copy)
if [ ! -f "$HOOK" ]; then
  HOOK="$(cd "$(dirname "$0")/../../.." && pwd)/.claude/hooks/ostk-agent-stop.sh"
fi

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found at $HOOK" >&2
  exit 1
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

BIN="$WORK/bin"
mkdir -p "$BIN"
printf '#!/bin/bash\nexit 0\n' > "$BIN/ostk"
chmod +x "$BIN/ostk"
printf '#!/bin/bash\nexit 0\n' > "$BIN/curl"
chmod +x "$BIN/curl"

FAIL=0

# home_for TEST_NUM — fixed home dir per test so log paths are stable
home_for() { echo "$WORK/home-$1"; }

run_hook_in_worktree() {
    local wt_dir="$1" env_extra="$2" homed="$3"
    mkdir -p "$homed/.youros/subagents"
    local payload='{"session_id":"test-session"}'
    PATH="$BIN:$PATH" HOME="$homed" \
        bash -c "cd '$wt_dir' && $env_extra bash '$HOOK'" <<< "$payload" >/dev/null 2>&1 || true
}

# ── Test 1: opt-in not set → merge is skipped ────────────────────────────
echo "=== test 1: auto-merge skipped when AGENT_AUTO_MERGE_ENABLED not set ==="
REPO1="$WORK/repo1"
git init -q -b main "$REPO1"
git -C "$REPO1" config user.email "t@t.com"
git -C "$REPO1" config user.name "T"
git -C "$REPO1" commit --allow-empty -m "init" -q

WT1_BRANCH="worktree-agent-noop-$(date +%s)"
WT1_DIR="$WORK/wt1"
git -C "$REPO1" worktree add -q -b "$WT1_BRANCH" "$WT1_DIR"
git -C "$WT1_DIR" commit --allow-empty -m "agent work" -q

HOMED1=$(home_for 1)
MAIN_BEFORE1=$(git -C "$REPO1" rev-parse main 2>/dev/null)
run_hook_in_worktree "$WT1_DIR" "" "$HOMED1"
MAIN_AFTER1=$(git -C "$REPO1" rev-parse main 2>/dev/null)

if [ "$MAIN_AFTER1" = "$MAIN_BEFORE1" ]; then
    echo "PASS: main unchanged when opt-in not set"
else
    echo "FAIL: main was fast-forwarded without AGENT_AUTO_MERGE_ENABLED=1"
    FAIL=1
fi

DEBT_LOG="$HOMED1/.youros/logs/merge-debt.log"
if grep -q "SKIP-OPT-IN" "$DEBT_LOG" 2>/dev/null; then
    echo "PASS: SKIP-OPT-IN logged to merge-debt.log"
else
    echo "FAIL: expected SKIP-OPT-IN in merge-debt.log"
    cat "$DEBT_LOG" 2>/dev/null || echo "(log missing)"
    FAIL=1
fi

# ── Test 2: opt-in set + clean worktree → merge happens ──────────────────
echo "=== test 2: auto-merge fires when AGENT_AUTO_MERGE_ENABLED=1 and worktree is clean ==="
REPO2="$WORK/repo2"
git init -q -b main "$REPO2"
git -C "$REPO2" config user.email "t@t.com"
git -C "$REPO2" config user.name "T"
git -C "$REPO2" commit --allow-empty -m "init" -q

WT2_BRANCH="worktree-agent-clean-$(date +%s)"
WT2_DIR="$WORK/wt2"
git -C "$REPO2" worktree add -q -b "$WT2_BRANCH" "$WT2_DIR"
git -C "$WT2_DIR" commit --allow-empty -m "agent work" -q

WANT_TIP2=$(git -C "$WT2_DIR" rev-parse "$WT2_BRANCH")
HOMED2=$(home_for 2)
run_hook_in_worktree "$WT2_DIR" "AGENT_AUTO_MERGE_ENABLED=1" "$HOMED2"
MAIN_AFTER2=$(git -C "$REPO2" rev-parse main 2>/dev/null)

if [ "$MAIN_AFTER2" = "$WANT_TIP2" ]; then
    echo "PASS: main fast-forwarded with opt-in set and clean worktree"
else
    echo "FAIL: main not fast-forwarded. want=$WANT_TIP2 got=$MAIN_AFTER2"
    FAIL=1
fi

if grep -q "MERGED" "$HOMED2/.youros/logs/merge-debt.log" 2>/dev/null; then
    echo "PASS: MERGED logged to merge-debt.log"
else
    echo "FAIL: expected MERGED in merge-debt.log"
    cat "$HOMED2/.youros/logs/merge-debt.log" 2>/dev/null || echo "(log missing)"
    FAIL=1
fi

# ── Test 3: opt-in set + untracked files → merge skipped ─────────────────
echo "=== test 3: auto-merge refused when worktree has untracked files ==="
REPO3="$WORK/repo3"
git init -q -b main "$REPO3"
git -C "$REPO3" config user.email "t@t.com"
git -C "$REPO3" config user.name "T"
git -C "$REPO3" commit --allow-empty -m "init" -q

WT3_BRANCH="worktree-agent-dirty-untracked-$(date +%s)"
WT3_DIR="$WORK/wt3"
git -C "$REPO3" worktree add -q -b "$WT3_BRANCH" "$WT3_DIR"
git -C "$WT3_DIR" commit --allow-empty -m "scaffold" -q

# Write a report as an untracked file (never staged)
echo "IMPORTANT REPORT" > "$WT3_DIR/agent-report.md"

MAIN_BEFORE3=$(git -C "$REPO3" rev-parse main 2>/dev/null)
HOMED3=$(home_for 3)
run_hook_in_worktree "$WT3_DIR" "AGENT_AUTO_MERGE_ENABLED=1" "$HOMED3"
MAIN_AFTER3=$(git -C "$REPO3" rev-parse main 2>/dev/null)

if [ "$MAIN_AFTER3" = "$MAIN_BEFORE3" ]; then
    echo "PASS: main unchanged when worktree has untracked files"
else
    echo "FAIL: main was fast-forwarded despite untracked files in worktree"
    FAIL=1
fi

if grep -q "ATTN-DIRTY-SKIP" "$HOMED3/.youros/logs/merge-debt.log" 2>/dev/null; then
    echo "PASS: ATTN-DIRTY-SKIP logged to merge-debt.log"
else
    echo "FAIL: expected ATTN-DIRTY-SKIP in merge-debt.log"
    cat "$HOMED3/.youros/logs/merge-debt.log" 2>/dev/null || echo "(log missing)"
    FAIL=1
fi

# Verify the untracked file survived
if [ -f "$WT3_DIR/agent-report.md" ]; then
    echo "PASS: agent-report.md still exists after hook ran"
else
    echo "FAIL: agent-report.md was lost"
    FAIL=1
fi

# ── Test 4: opt-in set + staged changes → merge skipped ──────────────────
echo "=== test 4: auto-merge refused when worktree has staged (cached) changes ==="
REPO4="$WORK/repo4"
git init -q -b main "$REPO4"
git -C "$REPO4" config user.email "t@t.com"
git -C "$REPO4" config user.name "T"
git -C "$REPO4" commit --allow-empty -m "init" -q

WT4_BRANCH="worktree-agent-dirty-staged-$(date +%s)"
WT4_DIR="$WORK/wt4"
git -C "$REPO4" worktree add -q -b "$WT4_BRANCH" "$WT4_DIR"
git -C "$WT4_DIR" commit --allow-empty -m "scaffold" -q

# Stage a file (never committed)
echo "staged content" > "$WT4_DIR/staged-file.txt"
git -C "$WT4_DIR" add staged-file.txt

MAIN_BEFORE4=$(git -C "$REPO4" rev-parse main 2>/dev/null)
HOMED4=$(home_for 4)
run_hook_in_worktree "$WT4_DIR" "AGENT_AUTO_MERGE_ENABLED=1" "$HOMED4"
MAIN_AFTER4=$(git -C "$REPO4" rev-parse main 2>/dev/null)

if [ "$MAIN_AFTER4" = "$MAIN_BEFORE4" ]; then
    echo "PASS: main unchanged when worktree has staged changes"
else
    echo "FAIL: main was fast-forwarded despite staged changes in worktree"
    FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
    echo ""
    echo "all tests passed"
else
    echo ""
    echo "some tests FAILED"
    exit 1
fi
