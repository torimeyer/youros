#!/usr/bin/env bash
# test_worktree_reaper_base.sh
# Regression test for --base <branch> flag on worktree-reaper.sh (needle →1307).
#
# Creates an isolated git repo fixture, runs the reaper with different --base
# values, and confirms the classification changes correctly.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAPER="$SCRIPT_DIR/worktree-reaper.sh"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ---- fixture setup ----------------------------------------------------------
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

cd "$TMPDIR"
git init -b main . >/dev/null 2>&1
git config user.email "test@test.local"
git config user.name "Test"

# initial commit on main
echo "root" > README.md
git add README.md
git commit -m "root commit" >/dev/null 2>&1

# agent branch: one commit ahead of main
git checkout -b worktree-agent-feature >/dev/null 2>&1
echo "agent work" > agent-file.txt
git add agent-file.txt
git commit -m "agent: add file" >/dev/null 2>&1

# custom-base: same content as worktree-agent-feature (fast-forward of agent branch)
# so worktree-agent-feature has 0 commits ahead of custom-base
git checkout -b custom-base worktree-agent-feature >/dev/null 2>&1

git checkout main >/dev/null 2>&1

# wire up the worktree under the path the reaper expects
mkdir -p .claude/worktrees
git worktree add .claude/worktrees/agent-feature worktree-agent-feature >/dev/null 2>&1

# ---- test 1: default (--base main) → agent branch should be "unique" --------
output1="$("$REAPER" 2>&1 || true)"
if echo "$output1" | grep -q "unique"; then
  pass "default --base main: agent branch classified as unique (has commits not on main)"
else
  fail "default --base main: expected 'unique' in output, got: $output1"
fi

# ---- test 2: --base main explicit → same as default -------------------------
output2="$("$REAPER" --base main 2>&1 || true)"
if echo "$output2" | grep -q "unique"; then
  pass "--base main explicit: agent branch classified as unique"
else
  fail "--base main explicit: expected 'unique' in output, got: $output2"
fi

# ---- test 3: --base custom-base → agent branch is absorbed (0 commits ahead) -
output3="$("$REAPER" --base custom-base 2>&1 || true)"
if echo "$output3" | grep -q "absorbed"; then
  pass "--base custom-base: agent branch classified as absorbed (0 commits ahead of custom-base)"
else
  fail "--base custom-base: expected 'absorbed' in output, got: $output3"
fi

# ---- test 4: --base with no arg → error exit 2 ------------------------------
output4="$("$REAPER" --base 2>&1 || true)"
if echo "$output4" | grep -q "error"; then
  pass "--base with no arg: prints error message"
else
  fail "--base with no arg: expected error message, got: $output4"
fi

# ---- test 5: unknown arg still rejected after --base is parsed --------------
output5="$("$REAPER" --base main --unknown-arg 2>&1 || true)"
if echo "$output5" | grep -q "error"; then
  pass "unknown arg after --base still rejected"
else
  fail "unknown arg after --base not rejected, got: $output5"
fi

# ---- summary ----------------------------------------------------------------
echo
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
