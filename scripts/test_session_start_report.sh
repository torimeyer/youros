#!/bin/bash
# test_session_start_report.sh — unit tests for scripts/session-start-report.sh
# Stubs git and ostk so no real server or state is touched.
#
# Usage: bash scripts/test_session_start_report.sh
# →1556

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_SCRIPT="$SCRIPT_DIR/session-start-report.sh"
PASS=0
FAIL=0

fail() { echo "FAIL: $1"; ((FAIL++)); }
pass() { echo "PASS: $1"; ((PASS++)); }

assert_contains() {
    local label="$1" haystack="$2" needle="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        pass "$label"
    else
        fail "$label (missing: '$needle')"
        echo "---- output ----"
        echo "$haystack"
        echo "----------------"
    fi
}

assert_not_contains() {
    local label="$1" haystack="$2" needle="$3"
    if [[ "$haystack" != *"$needle"* ]]; then
        pass "$label"
    else
        fail "$label (unexpected: '$needle')"
        echo "---- output ----"
        echo "$haystack"
        echo "----------------"
    fi
}

# ---- Build isolated temp environment ----
TMP=$(mktemp -d -t session-report-test.XXXXXX)
STUB_DIR="$TMP/stubs"
FAKE_REPO="$TMP/repo"
mkdir -p "$STUB_DIR" "$FAKE_REPO/.ostk" "$FAKE_REPO/.claude/worktrees"

# Fake git repo with one commit on main
git -C "$FAKE_REPO" init -q
git -C "$FAKE_REPO" config user.email "test@example.com"
git -C "$FAKE_REPO" config user.name "Test"
echo "hello" > "$FAKE_REPO/README.md"
git -C "$FAKE_REPO" add README.md
git -C "$FAKE_REPO" commit -q -m "feat: initial scaffold"
CURRENT_BRANCH=$(git -C "$FAKE_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
if [ "$CURRENT_BRANCH" != "main" ]; then
    git -C "$FAKE_REPO" branch -M main 2>/dev/null || git -C "$FAKE_REPO" checkout -b main 2>/dev/null || true
fi

# Fake worktree with 2 commits ahead of main
FAKE_WT="$FAKE_REPO/.claude/worktrees/agent-test-worktree-abc"
git -C "$FAKE_REPO" worktree add -b wt-branch "$FAKE_WT" main 2>/dev/null || mkdir -p "$FAKE_WT"
if [ -f "$FAKE_WT/.git" ] || [ -d "$FAKE_WT/.git" ]; then
    echo "a" > "$FAKE_WT/a.txt" && git -C "$FAKE_WT" add a.txt && git -C "$FAKE_WT" commit -q -m "wt commit 1" 2>/dev/null || true
    echo "b" > "$FAKE_WT/b.txt" && git -C "$FAKE_WT" add b.txt && git -C "$FAKE_WT" commit -q -m "wt commit 2" 2>/dev/null || true
fi

# Stub ostk: returns one recently-closed needle
NOW=$(python3 -c "from datetime import datetime, timezone; print(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))")
cat > "$STUB_DIR/ostk" << EOF
#!/bin/bash
echo '[{"id":"→999","title":"Fix the session report thing","status":"closed","closed_at":"${NOW}","priority":"P1"}]'
EOF
chmod +x "$STUB_DIR/ostk"

export PATH="$STUB_DIR:$PATH"

# Fake home with a handoff file
ORIG_HOME="$HOME"
export HOME="$TMP/home"
mkdir -p "$HOME/.claude/handoffs"
TODAY=$(date +%Y-%m-%d)
touch "$HOME/.claude/handoffs/${TODAY}-test-handoff.md"

# ---- Test 1: normal run with all signals present ----
OUT=$(bash "$REPORT_SCRIPT" "$FAKE_REPO" 2>/dev/null)

assert_contains "shows session-report header"    "$OUT" "[session-report]"
assert_contains "shows separator line"           "$OUT" "━━━ last 24h"
assert_contains "shows commits section"          "$OUT" "Commits"
assert_contains "includes the commit message"    "$OUT" "initial scaffold"
assert_contains "shows closed needles section"   "$OUT" "Closed needles"
assert_contains "includes the needle title"      "$OUT" "Fix the session report"
assert_contains "shows handoff line"             "$OUT" "Handoff:"
assert_contains "includes today handoff path"    "$OUT" "${TODAY}-test-handoff.md"

# ---- Test 2: nothing-to-report → no output ----
ORIG_HOME2="$HOME"
export HOME="$TMP/empty-home"
mkdir -p "$HOME/.claude/handoffs"
EMPTY_REPO="$TMP/empty-repo"
mkdir -p "$EMPTY_REPO/.claude/worktrees"
git -C "$EMPTY_REPO" init -q
git -C "$EMPTY_REPO" config user.email "test@example.com"
git -C "$EMPTY_REPO" config user.name "Test"
echo "x" > "$EMPTY_REPO/x.txt"
git -C "$EMPTY_REPO" add x.txt
GIT_COMMITTER_DATE="2020-01-01T00:00:00" GIT_AUTHOR_DATE="2020-01-01T00:00:00" \
    git -C "$EMPTY_REPO" commit -q -m "old commit" 2>/dev/null || true
ECURRENT=$(git -C "$EMPTY_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
[ "$ECURRENT" != "main" ] && git -C "$EMPTY_REPO" branch -M main 2>/dev/null || true

# Stub ostk returning empty list for this test
cat > "$STUB_DIR/ostk" << 'EOFSTUB'
#!/bin/bash
echo '[]'
EOFSTUB
chmod +x "$STUB_DIR/ostk"

EMPTY_OUT=$(bash "$REPORT_SCRIPT" "$EMPTY_REPO" 2>/dev/null)
assert_not_contains "no output when nothing to report" "$EMPTY_OUT" "[session-report]"

export HOME="$ORIG_HOME"
rm -rf "$TMP"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
