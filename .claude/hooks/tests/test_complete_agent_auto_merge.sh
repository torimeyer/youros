#!/bin/bash
# Tests: complete-agent.sh auto-merges agent worktree branches onto main.
set -uo pipefail

HOOK="$(cd "$(dirname "$0")/.." && pwd)/complete-agent.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Mock curl: always succeeds, avoids the 7s retry-backoff in the hook.
BIN="$WORK/bin"
mkdir -p "$BIN"
printf '#!/bin/bash\nexit 0\n' > "$BIN/curl"
chmod +x "$BIN/curl"

FAIL=0

# run_hook REPO AGENT_NAME → prints hook's stderr output.
run_hook() {
    local repo="$1" name="$2"
    local homed="$WORK/home-$name"
    mkdir -p "$homed/.myos/subagents"
    printf '%s' "$name" > "$homed/.myos/subagents/last.name"
    local payload
    payload='{"tool_use_id":"tid-'"$name"'","tool_response":{"output":"done"}}'
    # 2>&1 makes hook stderr go to captured stdout; >/dev/null discards hook stdout.
    PATH="$BIN:$PATH" HOME="$homed" CLAUDE_PROJECT_DIR="$repo" \
        bash "$HOOK" <<< "$payload" 2>&1 >/dev/null || true
}

# ── Test 1: fast-forward — main must advance to the branch tip ──────────
echo "=== test 1: fast-forward merge ==="
REPO1="$WORK/repo1"
git init -q "$REPO1"
git -C "$REPO1" config user.email "t@t.com"
git -C "$REPO1" config user.name "T"
git -C "$REPO1" commit --allow-empty -m "init" -q

NAME1="test-ff-$$"
WB1="worktree-agent-$NAME1"
git -C "$REPO1" checkout -q -b "$WB1"
git -C "$REPO1" commit --allow-empty -m "agent work" -q
WANT_TIP=$(git -C "$REPO1" rev-parse "$WB1")
git -C "$REPO1" checkout -q main

STDERR1=$(run_hook "$REPO1" "$NAME1")
GOT_TIP=$(git -C "$REPO1" rev-parse main)

if [ "$GOT_TIP" = "$WANT_TIP" ]; then
    echo "PASS: main fast-forwarded to $GOT_TIP"
    echo "  hook says: $STDERR1"
else
    echo "FAIL: main not fast-forwarded. want=$WANT_TIP got=$GOT_TIP"
    echo "  hook says: $STDERR1"
    FAIL=1
fi

# ── Test 2: diverged history — main must NOT change; skip in stderr ──────
echo "=== test 2: non-fast-forward skip ==="
REPO2="$WORK/repo2"
git init -q "$REPO2"
git -C "$REPO2" config user.email "t@t.com"
git -C "$REPO2" config user.name "T"
git -C "$REPO2" commit --allow-empty -m "init" -q

NAME2="test-nff-$$"
WB2="worktree-agent-$NAME2"

# Branch diverges from main: each side gets a unique commit.
git -C "$REPO2" checkout -q -b "$WB2"
git -C "$REPO2" commit --allow-empty -m "branch commit" -q
git -C "$REPO2" checkout -q main
git -C "$REPO2" commit --allow-empty -m "main diverged" -q

MAIN_BEFORE=$(git -C "$REPO2" rev-parse main)
STDERR2=$(run_hook "$REPO2" "$NAME2")
MAIN_AFTER=$(git -C "$REPO2" rev-parse main)

if [ "$MAIN_AFTER" != "$MAIN_BEFORE" ]; then
    echo "FAIL: main changed when it should not. before=$MAIN_BEFORE after=$MAIN_AFTER"
    echo "  hook says: $STDERR2"
    FAIL=1
elif ! echo "$STDERR2" | grep -qi "skip\|not fast-forward"; then
    echo "FAIL: expected 'skip' or 'not fast-forward' in stderr"
    echo "  hook says: $STDERR2"
    FAIL=1
else
    echo "PASS: main unchanged, stderr mentions skip"
    echo "  hook says: $STDERR2"
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
