#!/bin/bash
# Verify that saa-must-spawn.sh derives the transcript slug from
# CLAUDE_PROJECT_DIR (or git toplevel) rather than hard-coding any
# specific username, making the hook work for any user.

set -u
HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/saa-must-spawn.sh"
PASS=0
FAIL=0

assert_eq() {
  if [ "$2" = "$3" ]; then
    echo "  PASS: $1"; PASS=$((PASS+1))
  else
    echo "  FAIL: $1 — expected '$2', got '$3'"; FAIL=$((FAIL+1))
  fi
}

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

WITH_FLAG_CFG="$SCRATCH/config.json"
echo '{"enable_tori_rules": true}' > "$WITH_FLAG_CFG"

SAA_INPUT='{"tool_name":"Bash","tool_input":{"command":"ls"}}'
SAA_MSG='{"type":"user","message":{"role":"user","content":"saa build the thing"}}'

# ── Test 1: slug_is_user_portable_when_user_set (alice) ──────────────────────
# Slug for /Users/alice/myproject is -Users-alice-myproject (/ replaced by -)
ALICE_HOME="$SCRATCH/alice_home"
ALICE_PROJ_DIR="/Users/alice/myproject"
ALICE_SLUG=$(echo "$ALICE_PROJ_DIR" | sed 's|/|-|g')
mkdir -p "$ALICE_HOME/.claude/projects/$ALICE_SLUG"
echo "$SAA_MSG" > "$ALICE_HOME/.claude/projects/$ALICE_SLUG/session.jsonl"

RC=$(printf '%s' "$SAA_INPUT" | \
  HOME="$ALICE_HOME" \
  MYOS_CONFIG_PATH="$WITH_FLAG_CFG" \
  CLAUDE_PROJECT_DIR="$ALICE_PROJ_DIR" \
  bash "$HOOK" >/dev/null 2>/dev/null; echo $?)
assert_eq "slug_is_user_portable_when_user_set: alice slug resolves transcript -> blocks (exit 2)" "2" "$RC"

# ── Test 2: slug_is_user_portable_when_user_changed (bob) ────────────────────
BOB_HOME="$SCRATCH/bob_home"
BOB_PROJ_DIR="/Users/bob/myproject"
BOB_SLUG=$(echo "$BOB_PROJ_DIR" | sed 's|/|-|g')
mkdir -p "$BOB_HOME/.claude/projects/$BOB_SLUG"
echo "$SAA_MSG" > "$BOB_HOME/.claude/projects/$BOB_SLUG/session.jsonl"

# bob slug differs from alice slug
if [ "$BOB_SLUG" = "$ALICE_SLUG" ]; then
  echo "  FAIL: slug_is_user_portable_when_user_changed — alice and bob slugs are identical: $ALICE_SLUG"; FAIL=$((FAIL+1))
else
  echo "  PASS: slug_is_user_portable_when_user_changed — slugs differ (alice=$ALICE_SLUG, bob=$BOB_SLUG)"; PASS=$((PASS+1))
fi

# bob hook also resolves to the right transcript and blocks
RC=$(printf '%s' "$SAA_INPUT" | \
  HOME="$BOB_HOME" \
  MYOS_CONFIG_PATH="$WITH_FLAG_CFG" \
  CLAUDE_PROJECT_DIR="$BOB_PROJ_DIR" \
  bash "$HOOK" >/dev/null 2>/dev/null; echo $?)
assert_eq "slug_is_user_portable_when_user_changed: bob slug resolves transcript -> blocks (exit 2)" "2" "$RC"

# ── Test 3: slug_does_not_hardcode_torimeyer ──────────────────────────────────
if grep -q "torimeyer" "$HOOK"; then
  echo "  FAIL: slug_does_not_hardcode_torimeyer — hook source contains 'torimeyer'"; FAIL=$((FAIL+1))
else
  echo "  PASS: slug_does_not_hardcode_torimeyer — no 'torimeyer' literal in hook source"; PASS=$((PASS+1))
fi

echo
echo "passed: $PASS / failed: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
