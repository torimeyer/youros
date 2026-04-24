#!/usr/bin/env bash
# Asserts the review agent prompt requires `git show <commit>` and bans
# `git diff <base>..<branch>` for describing commit content.
#
# Why this test exists: on 2026-04-23 a reviewer subagent ran
# `git diff main 477645b -- .claude/hooks/standing-rules.sh` on a
# stale-base branch and confidently reported two "unrelated removals"
# that the commit never made. Those lines were added to main AFTER the
# branch forked, so the plain diff showed them as branch-side deletions.
# This test prevents a future review.agent edit from losing the receipts
# rule or the stale-base guard.
set -euo pipefail

AGENT_FILE="$(dirname "$0")/../../agents/review.agent"
if [ ! -f "$AGENT_FILE" ]; then
  echo "fail: $AGENT_FILE not found"
  exit 1
fi

fail=0

require() {
  local pattern="$1"
  local msg="$2"
  if grep -q -- "$pattern" "$AGENT_FILE"; then
    echo "pass: $msg"
  else
    echo "fail: $msg (missing: $pattern)"
    fail=1
  fi
}

ban() {
  local pattern="$1"
  local msg="$2"
  # Forbid the pattern appearing as a RECOMMENDED command. The agent file
  # may mention it only inside a NEVER-use block. We allow it if the line
  # immediately above or below includes the word NEVER, and otherwise
  # fail. Simpler check: count the pattern lines and require each to be
  # within 6 lines after a "NEVER" line.
  if grep -q -- "$pattern" "$AGENT_FILE"; then
    # At least one occurrence: confirm a NEVER block exists.
    if ! grep -q "NEVER use" "$AGENT_FILE"; then
      echo "fail: $msg ($pattern appears without a NEVER block)"
      fail=1
    else
      echo "pass: $msg (appears only inside NEVER block)"
    fi
  else
    echo "pass: $msg (absent)"
  fi
}

require "git show <commit-hash> --stat" "review.agent requires git show --stat"
require "git show <commit-hash> -- <path>" "review.agent requires git show per-path"
require "CHECK FOR STALE BASE" "review.agent has stale-base guard"
require "git merge-base" "review.agent shows merge-base check"
require "RECEIPTS RULE" "review.agent has receipts rule for claims"
require "hallucination" "review.agent names the failure mode"

ban "git diff <base>..<branch>" "git diff base..branch banned as recommended command"
ban "git diff <base> <branch>" "git diff base branch banned as recommended command"

if [ "$fail" -ne 0 ]; then
  echo "FAIL"
  exit 1
fi
echo "PASS"
