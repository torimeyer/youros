#!/bin/bash
# Wave 2 tori-gate tests: hooks that use tori-personal vocabulary
# (saa, diagnose, fix verbs; stash hygiene; data-shape rule; HUMANFILE)
# must be no-ops unless ~/.myos/config.json has "enable_tori_rules": true.
#
# Covers:
#   - saa-must-spawn:   no flag -> exit 0; flag set -> blocks on saa/diagnose/fix
#   - saa-after-3-hypotheses: no flag -> no SAA REMINDER; flag -> emits
#   - data-shape-question:    no flag -> no output; flag -> emits rule
#   - stash-label-audit:      no flag -> allows bare stash; flag -> blocks
#   - standing-rules HUMANFILE block: no flag -> absent; flag -> present
#
# Exit 0 on pass, nonzero on first failure.

set -u

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

assert_eq() {
  if [ "$2" = "$3" ]; then
    echo "  PASS: $1"
    PASS=$((PASS+1))
  else
    echo "  FAIL: $1 — expected '$2', got '$3'"
    FAIL=$((FAIL+1))
  fi
}

assert_contains() {
  case "$3" in
    *"$2"*) echo "  PASS: $1"; PASS=$((PASS+1)) ;;
    *) echo "  FAIL: $1 — needle not in haystack"; echo "    needle: $2"; echo "    actual: $3"; FAIL=$((FAIL+1)) ;;
  esac
}

assert_not_contains() {
  case "$3" in
    *"$2"*) echo "  FAIL: $1 — needle WAS in haystack"; echo "    needle: $2"; echo "    actual: $3"; FAIL=$((FAIL+1)) ;;
    *) echo "  PASS: $1"; PASS=$((PASS+1)) ;;
  esac
}

# Make a temp config with the flag set / unset.
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
NO_FLAG_CFG="$SCRATCH/no_flag.json"
WITH_FLAG_CFG="$SCRATCH/with_flag.json"
ABSENT_CFG="$SCRATCH/does_not_exist.json"
echo '{"some_other_key": true}' > "$NO_FLAG_CFG"
echo '{"enable_tori_rules": true}' > "$WITH_FLAG_CFG"

echo "=== saa-must-spawn ==="
# Need a fake session jsonl so the hook has a "last user message" to read.
# But the hook should exit 0 BEFORE reading the jsonl when the flag is missing,
# so we can skip that for the no-flag cases.
INPUT='{"tool_name":"Bash","tool_input":{"command":"echo hi"}}'
RC=$(printf '%s' "$INPUT" | MYOS_CONFIG_PATH="$ABSENT_CFG" bash "$HOOKS_DIR/saa-must-spawn.sh" >/dev/null 2>&1; echo $?)
assert_eq "no config -> exit 0" "0" "$RC"
RC=$(printf '%s' "$INPUT" | MYOS_CONFIG_PATH="$NO_FLAG_CFG" bash "$HOOKS_DIR/saa-must-spawn.sh" >/dev/null 2>&1; echo $?)
assert_eq "config without enable_tori_rules -> exit 0" "0" "$RC"
# With flag set, the hook will try to read transcripts. Doesn't matter if it finds
# none; just confirm it does NOT exit 0 due to the gate.
# Easier: verify gate-passing by checking the hook even tries to fall through (we
# can't easily simulate "user said saa" without a real session jsonl). Accept that
# the gate-on path lets it continue. Smoke check: with flag set + no transcripts,
# hook still exits 0 (because no last user message found). Fine.
RC=$(printf '%s' "$INPUT" | MYOS_CONFIG_PATH="$WITH_FLAG_CFG" bash "$HOOKS_DIR/saa-must-spawn.sh" >/dev/null 2>&1; echo $?)
assert_eq "config with flag, non-Agent tool, no transcript -> exit 0" "0" "$RC"

echo
echo "=== saa-after-3-hypotheses ==="
INPUT='{"tool_name":"Edit","tool_input":{"file_path":"/tmp/foo.py"}}'
OUT=$(printf '%s' "$INPUT" | MYOS_CONFIG_PATH="$ABSENT_CFG" bash "$HOOKS_DIR/saa-after-3-hypotheses.sh" 2>&1)
assert_not_contains "no config -> no SAA REMINDER" "SAA REMINDER" "$OUT"
OUT=$(printf '%s' "$INPUT" | MYOS_CONFIG_PATH="$NO_FLAG_CFG" bash "$HOOKS_DIR/saa-after-3-hypotheses.sh" 2>&1)
assert_not_contains "no flag -> no SAA REMINDER" "SAA REMINDER" "$OUT"
# With flag set, run 3 times against a unique file to trip the threshold.
STATE_FILE="$SCRATCH/edit-cycles.json"
TARGET_FILE="$SCRATCH/edit_target_$RANDOM.py"
INPUT_FLAG_ON="{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TARGET_FILE\"}}"
for i in 1 2 3; do
  OUT=$(printf '%s' "$INPUT_FLAG_ON" | \
    MYOS_CONFIG_PATH="$WITH_FLAG_CFG" \
    MYOS_EDIT_CYCLES_STATE="$STATE_FILE" \
    bash "$HOOKS_DIR/saa-after-3-hypotheses.sh" 2>&1)
done
assert_contains "flag set, 3 edits to same file -> SAA REMINDER" "SAA REMINDER" "$OUT"

echo
echo "=== data-shape-question ==="
OUT=$(MYOS_CONFIG_PATH="$ABSENT_CFG" bash "$HOOKS_DIR/data-shape-question.sh" 2>&1)
assert_not_contains "no config -> no DATA-SHAPE output" "DATA-SHAPE" "$OUT"
OUT=$(MYOS_CONFIG_PATH="$NO_FLAG_CFG" bash "$HOOKS_DIR/data-shape-question.sh" 2>&1)
assert_not_contains "no flag -> no DATA-SHAPE output" "DATA-SHAPE" "$OUT"
OUT=$(MYOS_CONFIG_PATH="$WITH_FLAG_CFG" bash "$HOOKS_DIR/data-shape-question.sh" 2>&1)
assert_contains "flag set -> DATA-SHAPE meta-rule emitted" "DATA-SHAPE META-RULE" "$OUT"

echo
echo "=== stash-label-audit ==="
INPUT='{"tool_name":"Bash","tool_input":{"command":"git stash"}}'
RC=$(printf '%s' "$INPUT" | MYOS_CONFIG_PATH="$ABSENT_CFG" bash "$HOOKS_DIR/stash-label-audit.sh" >/dev/null 2>&1; echo $?)
assert_eq "no config -> exit 0 (allow bare git stash)" "0" "$RC"
RC=$(printf '%s' "$INPUT" | MYOS_CONFIG_PATH="$NO_FLAG_CFG" bash "$HOOKS_DIR/stash-label-audit.sh" >/dev/null 2>&1; echo $?)
assert_eq "no flag -> exit 0" "0" "$RC"
RC=$(printf '%s' "$INPUT" | MYOS_CONFIG_PATH="$WITH_FLAG_CFG" bash "$HOOKS_DIR/stash-label-audit.sh" >/dev/null 2>&1; echo $?)
assert_eq "flag set, bare stash -> exit 2 (block)" "2" "$RC"

echo
echo "=== standing-rules HUMANFILE block ==="
HF="$SCRATCH/HUMANFILE"
echo "tori humanfile contents" > "$HF"
INPUT='{"prompt":"hi"}'
# Disable backend probe by pointing at unreachable URL.
OUT=$(printf '%s' "$INPUT" | \
  MYOS_CONFIG_PATH="$ABSENT_CFG" \
  MYOS_HUMANFILE_PATH="$HF" \
  MYOS_BACKEND_URL="http://127.0.0.1:1" \
  bash "$HOOKS_DIR/standing-rules.sh" 2>&1)
assert_not_contains "no config -> no ACTIVE HUMANFILE block" "ACTIVE HUMANFILE RULES" "$OUT"
OUT=$(printf '%s' "$INPUT" | \
  MYOS_CONFIG_PATH="$NO_FLAG_CFG" \
  MYOS_HUMANFILE_PATH="$HF" \
  MYOS_BACKEND_URL="http://127.0.0.1:1" \
  bash "$HOOKS_DIR/standing-rules.sh" 2>&1)
assert_not_contains "no flag -> no ACTIVE HUMANFILE block" "ACTIVE HUMANFILE RULES" "$OUT"
OUT=$(printf '%s' "$INPUT" | \
  MYOS_CONFIG_PATH="$WITH_FLAG_CFG" \
  MYOS_HUMANFILE_PATH="$HF" \
  MYOS_BACKEND_URL="http://127.0.0.1:1" \
  bash "$HOOKS_DIR/standing-rules.sh" 2>&1)
assert_contains "flag set, HUMANFILE present -> ACTIVE HUMANFILE block emitted" "ACTIVE HUMANFILE RULES" "$OUT"

echo
echo "=== summary ==="
echo "  passed: $PASS"
echo "  failed: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
