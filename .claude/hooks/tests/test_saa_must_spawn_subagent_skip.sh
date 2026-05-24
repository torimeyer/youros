#!/bin/bash
# Verify saa-must-spawn skips subagent sessions (CLAUDE_PROJECT_DIR
# inside .claude/worktrees/*) so the hook doesn't make subagents
# spawn grandchildren in cascade.

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

WITH_FLAG_CFG="$SCRATCH/with_flag.json"
echo '{"enable_tori_rules": true}' > "$WITH_FLAG_CFG"

# Set up a fake jsonl session with last user message starting with "saa "
# at a path that matches the existing hook's hardcoded transcript glob.
# But under HOME override, the path becomes <HOME>/.claude/projects/-Users-torimeyer-claude-torios/.
FAKE_HOME="$SCRATCH/home"
PROJ_TRANSCRIPT_DIR="$FAKE_HOME/.claude/projects/-Users-torimeyer-claude-torios"
mkdir -p "$PROJ_TRANSCRIPT_DIR"
TRANSCRIPT="$PROJ_TRANSCRIPT_DIR/test.jsonl"
cat > "$TRANSCRIPT" <<'JSONL'
{"type":"user","message":{"role":"user","content":"saa fix the thing"}}
JSONL

# 1. Parent project dir (NOT a worktree) + flag set + Bash tool + saa user msg → blocks (exit 2)
INPUT='{"tool_name":"Bash","tool_input":{"command":"ls"}}'
RC=$(printf '%s' "$INPUT" | \
    HOME="$FAKE_HOME" \
    MYOS_CONFIG_PATH="$WITH_FLAG_CFG" \
    CLAUDE_PROJECT_DIR="/Users/torimeyer/claude/torios" \
    bash "$HOOK" >/dev/null 2>/dev/null; echo $?)
assert_eq "parent dir + flag + saa msg -> exit 2 (blocks)" "2" "$RC"

# 2. Subagent worktree dir + flag set + Bash tool + saa user msg → exit 0 (skipped)
RC=$(printf '%s' "$INPUT" | \
    HOME="$FAKE_HOME" \
    MYOS_CONFIG_PATH="$WITH_FLAG_CFG" \
    CLAUDE_PROJECT_DIR="/Users/torimeyer/claude/torios/.claude/worktrees/agent-foo-bar-abc123" \
    bash "$HOOK" >/dev/null 2>/dev/null; echo $?)
assert_eq "worktree dir + flag + saa msg -> exit 0 (subagent skip)" "0" "$RC"

# 3. Subagent worktree under different repo path also skips
RC=$(printf '%s' "$INPUT" | \
    HOME="$FAKE_HOME" \
    MYOS_CONFIG_PATH="$WITH_FLAG_CFG" \
    CLAUDE_PROJECT_DIR="/some/other/repo/.claude/worktrees/agent-x" \
    bash "$HOOK" >/dev/null 2>/dev/null; echo $?)
assert_eq "any worktree path -> exit 0" "0" "$RC"

# 4. CLAUDE_PROJECT_DIR unset + flag + parent transcripts → still blocks (regression: subagent
#    skip must NOT swallow the regular block path)
RC=$(printf '%s' "$INPUT" | \
    HOME="$FAKE_HOME" \
    MYOS_CONFIG_PATH="$WITH_FLAG_CFG" \
    bash "$HOOK" >/dev/null 2>/dev/null; echo $?)
assert_eq "no project dir + flag + saa msg -> exit 2 (still blocks)" "2" "$RC"

echo
echo "passed: $PASS / failed: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
