#!/bin/bash
# Tests: ostk-agent-stop.sh auto-merges worktree branches onto main via SubagentStop.
#
# Verifies the fix for →1548: background/worktree agents were skipped by
# complete-agent.sh (PostToolUse fires at launch, not finish). SubagentStop
# fires in the CHILD session when the agent actually exits. The hook uses
# git plumbing (git-common-dir + git branch --show-current) to find the
# main repo and branch — no side-channel name files required.
set -uo pipefail

HOOK="$(cd "$(dirname "$0")/.." && pwd)/ostk-agent-stop.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Mock external commands: ostk (kernel write), curl (/complete call).
BIN="$WORK/bin"
mkdir -p "$BIN"
printf '#!/bin/bash\nexit 0\n' > "$BIN/ostk"
chmod +x "$BIN/ostk"
printf '#!/bin/bash\nexit 0\n' > "$BIN/curl"
chmod +x "$BIN/curl"

FAIL=0

# run_hook_in_worktree WORKTREE_DIR AGENT_NAME → prints hook stderr
run_hook_in_worktree() {
    local wt_dir="$1" name="$2"
    local homed="$WORK/home-$name"
    mkdir -p "$homed/.myos/subagents"
    printf '%s' "$name" > "$homed/.myos/subagents/last.name"
    local payload='{"session_id":"test-session-'"$name"'"}'
    # Run hook with CWD inside the worktree so git commands see worktree context.
    PATH="$BIN:$PATH" HOME="$homed" \
        bash -c "cd '$wt_dir' && bash '$HOOK'" <<< "$payload" 2>&1 >/dev/null || true
}

# ── Test 1: worktree branch fast-forwards onto main ──────────────────────
echo "=== test 1: SubagentStop auto-merges worktree branch onto main ==="
REPO1="$WORK/repo1"
git init -q "$REPO1"
git -C "$REPO1" config user.email "test@test.com"
git -C "$REPO1" config user.name "Test"
git -C "$REPO1" commit --allow-empty -m "init" -q

# Create a worktree on a branch named worktree-agent-<slug>
WT_BRANCH="worktree-agent-test-bg-agent-$(date +%s)"
WT_DIR="$WORK/wt1"
git -C "$REPO1" worktree add -q -b "$WT_BRANCH" "$WT_DIR"
git -C "$WT_DIR" commit --allow-empty -m "agent work in worktree" -q

WANT_TIP=$(git -C "$WT_DIR" rev-parse "$WT_BRANCH")
MAIN_BEFORE=$(git -C "$REPO1" rev-parse main 2>/dev/null || git -C "$REPO1" rev-parse HEAD)

STDERR1=$(run_hook_in_worktree "$WT_DIR" "test-bg-agent")
MAIN_AFTER=$(git -C "$REPO1" rev-parse main 2>/dev/null || git -C "$REPO1" rev-parse HEAD)

if [ "$MAIN_AFTER" = "$WANT_TIP" ]; then
    echo "PASS: main fast-forwarded to $MAIN_AFTER"
    echo "  hook says: $STDERR1"
else
    echo "FAIL: main not fast-forwarded. want=$WANT_TIP got=$MAIN_AFTER (before=$MAIN_BEFORE)"
    echo "  hook says: $STDERR1"
    FAIL=1
fi

# Verify MERGED entry in merge-debt.log
HOMED1="$WORK/home-test-bg-agent"
if grep -q "MERGED" "$HOMED1/.myos/logs/merge-debt.log" 2>/dev/null; then
    echo "PASS: merge-debt.log has MERGED entry"
else
    echo "FAIL: merge-debt.log missing MERGED entry"
    cat "$HOMED1/.myos/logs/merge-debt.log" 2>/dev/null || echo "(log missing)"
    FAIL=1
fi

# ── Test 2: non-worktree-agent branch is silently skipped ────────────────
echo "=== test 2: non-worktree-agent branch skipped without logging ==="
REPO2="$WORK/repo2"
git init -q "$REPO2"
git -C "$REPO2" config user.email "test@test.com"
git -C "$REPO2" config user.name "Test"
git -C "$REPO2" commit --allow-empty -m "init" -q

# Worktree on a branch that does NOT start with worktree-agent-
WT_BRANCH2="feature-foo-$(date +%s)"
WT_DIR2="$WORK/wt2"
git -C "$REPO2" worktree add -q -b "$WT_BRANCH2" "$WT_DIR2"
git -C "$WT_DIR2" commit --allow-empty -m "non-agent work" -q

MAIN_BEFORE2=$(git -C "$REPO2" rev-parse main 2>/dev/null || git -C "$REPO2" rev-parse HEAD)
STDERR2=$(run_hook_in_worktree "$WT_DIR2" "test-skip-agent")
MAIN_AFTER2=$(git -C "$REPO2" rev-parse main 2>/dev/null || git -C "$REPO2" rev-parse HEAD)

if [ "$MAIN_AFTER2" = "$MAIN_BEFORE2" ]; then
    echo "PASS: main unchanged for non-worktree-agent branch"
else
    echo "FAIL: main changed for non-worktree-agent branch. before=$MAIN_BEFORE2 after=$MAIN_AFTER2"
    FAIL=1
fi

# ── Test 3: diverged history — main unchanged, ATTN-NOT-FF in debt log ───
echo "=== test 3: diverged branch logs ATTN and leaves main unchanged ==="
REPO3="$WORK/repo3"
git init -q "$REPO3"
git -C "$REPO3" config user.email "test@test.com"
git -C "$REPO3" config user.name "Test"
git -C "$REPO3" commit --allow-empty -m "init" -q

WT_BRANCH3="worktree-agent-diverged-$(date +%s)"
WT_DIR3="$WORK/wt3"
git -C "$REPO3" worktree add -q -b "$WT_BRANCH3" "$WT_DIR3"
git -C "$WT_DIR3" commit --allow-empty -m "worktree commit" -q
# Diverge main
git -C "$REPO3" commit --allow-empty -m "main diverged commit" -q

MAIN_BEFORE3=$(git -C "$REPO3" rev-parse main 2>/dev/null || git -C "$REPO3" rev-parse HEAD)
STDERR3=$(run_hook_in_worktree "$WT_DIR3" "test-div-agent")
MAIN_AFTER3=$(git -C "$REPO3" rev-parse main 2>/dev/null || git -C "$REPO3" rev-parse HEAD)

if [ "$MAIN_AFTER3" = "$MAIN_BEFORE3" ]; then
    echo "PASS: main unchanged for diverged branch"
    echo "  hook says: $STDERR3"
else
    echo "FAIL: main changed for diverged branch. before=$MAIN_BEFORE3 after=$MAIN_AFTER3"
    FAIL=1
fi

HOMED3="$WORK/home-test-div-agent"
if grep -q "ATTN-NOT-FF" "$HOMED3/.myos/logs/merge-debt.log" 2>/dev/null; then
    echo "PASS: merge-debt.log has ATTN-NOT-FF entry"
else
    echo "FAIL: merge-debt.log missing ATTN-NOT-FF entry"
    cat "$HOMED3/.myos/logs/merge-debt.log" 2>/dev/null || echo "(log missing)"
    FAIL=1
fi

# ── Test 4: empty AGENT_NAME (bridge-guard case) still auto-merges ───────
# This is the primary regression that caused 8 of 12 agents to miss auto-merge.
# When register-agent.sh exits via bridge guard, last.name is never written.
# Auto-merge must still fire because it uses git plumbing, not last.name.
echo "=== test 4: empty AGENT_NAME (bridge-guard case) still auto-merges ==="
REPO4="$WORK/repo4"
git init -q "$REPO4"
git -C "$REPO4" config user.email "test@test.com"
git -C "$REPO4" config user.name "Test"
git -C "$REPO4" commit --allow-empty -m "init" -q

WT_BRANCH4="worktree-agent-bridge-guard-$(date +%s)"
WT_DIR4="$WORK/wt4"
git -C "$REPO4" worktree add -q -b "$WT_BRANCH4" "$WT_DIR4"
git -C "$WT_DIR4" commit --allow-empty -m "agent work (bridge-guard case)" -q

WANT_TIP4=$(git -C "$WT_DIR4" rev-parse "$WT_BRANCH4")

# Use a fresh home dir with NO last.name written (simulating bridge guard)
HOMED4="$WORK/home-bridge-no-name"
mkdir -p "$HOMED4/.myos/subagents"
# Intentionally do NOT write last.name

STDERR4=$(PATH="$BIN:$PATH" HOME="$HOMED4" bash -c "cd '$WT_DIR4' && bash '$HOOK'" <<< '{}' 2>&1 >/dev/null || true)
MAIN_AFTER4=$(git -C "$REPO4" rev-parse main 2>/dev/null || git -C "$REPO4" rev-parse HEAD)

if [ "$MAIN_AFTER4" = "$WANT_TIP4" ]; then
    echo "PASS: main fast-forwarded even with empty AGENT_NAME"
    echo "  hook says: $STDERR4"
else
    echo "FAIL: main not fast-forwarded for bridge-guard case. want=$WANT_TIP4 got=$MAIN_AFTER4"
    echo "  hook says: $STDERR4"
    FAIL=1
fi

if grep -q "MERGED" "$HOMED4/.myos/logs/merge-debt.log" 2>/dev/null; then
    echo "PASS: merge-debt.log has MERGED entry with unknown agent label"
else
    echo "FAIL: merge-debt.log missing MERGED entry"
    cat "$HOMED4/.myos/logs/merge-debt.log" 2>/dev/null || echo "(log missing)"
    FAIL=1
fi

# ── Done ─────────────────────────────────────────────────────────────────
if [ "$FAIL" -eq 0 ]; then
    echo ""
    echo "all tests passed"
else
    echo ""
    echo "some tests FAILED"
    exit 1
fi
