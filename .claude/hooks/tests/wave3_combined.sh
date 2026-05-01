#!/bin/bash
# Wave 3 combined-hook tests. Verifies bash-guards.sh, bash-postwatch.sh,
# and edit-postwatch.sh preserve every behavior of the 12 source hooks
# they replace.

set -u
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

assert_eq() {
  if [ "$2" = "$3" ]; then
    echo "  PASS: $1"; PASS=$((PASS+1))
  else
    echo "  FAIL: $1 — expected '$2', got '$3'"; FAIL=$((FAIL+1))
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
    *"$2"*) echo "  FAIL: $1 — needle WAS in haystack"; echo "    needle: $2"; FAIL=$((FAIL+1)) ;;
    *) echo "  PASS: $1"; PASS=$((PASS+1)) ;;
  esac
}

# Run hook with stdin from $1 and capture (stdout+stderr combined, rc).
# Output is "rc|combined". Some source hooks emitted to stdout, some to
# stderr; capturing both preserves behavior comparison.
run_hook() {
  local hook="$1"; local input="$2"; local out_file
  out_file=$(mktemp)
  printf '%s' "$input" | bash "$HOOKS_DIR/$hook" >"$out_file" 2>&1
  local rc=$?
  local out
  out=$(cat "$out_file")
  rm -f "$out_file"
  printf '%s|%s' "$rc" "$out"
}

# --------- bash-guards.sh: PreToolUse ---------
echo "=== bash-guards: zsh-reserved-var-guard ==="

# zsh status= assignment
INPUT='{"tool_name":"Bash","tool_input":{"command":"my_status=$(date)"}}'
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "non-reserved var allowed" "0" "$RC"

INPUT='{"tool_name":"Bash","tool_input":{"command":"foo=1; status=2"}}'
RES=$(run_hook bash-guards.sh "$INPUT")
RC="${RES%%|*}"; ERR="${RES#*|}"
assert_eq "status= blocked" "2" "$RC"
assert_contains "status= says read-only" "read-only variable" "$ERR"

# Monitor matcher
INPUT='{"tool_name":"Monitor","tool_input":{"command":"path=/usr/bin"}}'
RES=$(run_hook bash-guards.sh "$INPUT")
RC="${RES%%|*}"
assert_eq "Monitor + path= blocked" "2" "$RC"

# mcp__ostk__bash matcher (uses cmd not command)
INPUT='{"tool_name":"mcp__ostk__bash","tool_input":{"cmd":"pipestatus=42"}}'
RES=$(run_hook bash-guards.sh "$INPUT")
RC="${RES%%|*}"
assert_eq "mcp_bash + pipestatus= blocked" "2" "$RC"

echo
echo "=== bash-guards: curl-timeouts ==="
INPUT='{"tool_name":"Bash","tool_input":{"command":"curl https://example.com"}}'
RES=$(run_hook bash-guards.sh "$INPUT")
RC="${RES%%|*}"; ERR="${RES#*|}"
assert_eq "curl without timeout blocked" "2" "$RC"
assert_contains "curl says connect-timeout" "connect-timeout" "$ERR"

INPUT='{"tool_name":"Bash","tool_input":{"command":"curl --connect-timeout 3 -m 5 https://x.com"}}'
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "curl with timeout allowed" "0" "$RC"

INPUT='{"tool_name":"Bash","tool_input":{"command":"bash scripts/foo.sh"}}'
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "scripts/ path bypasses curl check" "0" "$RC"

echo
echo "=== bash-guards: no-npm-dev (fail-open if scripts missing) ==="
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
INPUT='{"tool_name":"Bash","tool_input":{"command":"npm run dev"}}'
RC=$(printf '%s' "$INPUT" | CLAUDE_PROJECT_DIR="$SCRATCH" bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "npm run dev + no scripts/ -> exit 0" "0" "$RC"
mkdir -p "$SCRATCH/scripts"; echo '#!/bin/sh' > "$SCRATCH/scripts/dev-backend.sh"
RC=$(printf '%s' "$INPUT" | CLAUDE_PROJECT_DIR="$SCRATCH" bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "npm run dev + scripts/dev-backend.sh -> blocked" "2" "$RC"

echo
echo "=== bash-guards: safe-vitest (fail-open if scripts missing) ==="
SCRATCH2=$(mktemp -d)
INPUT='{"tool_name":"Bash","tool_input":{"command":"npx vitest run"}}'
RC=$(printf '%s' "$INPUT" | CLAUDE_PROJECT_DIR="$SCRATCH2" bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "vitest + no scripts/ -> exit 0" "0" "$RC"
mkdir -p "$SCRATCH2/scripts"; echo '#!/bin/sh' > "$SCRATCH2/scripts/run-vitest.sh"
RC=$(printf '%s' "$INPUT" | CLAUDE_PROJECT_DIR="$SCRATCH2" bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "vitest + scripts/run-vitest.sh -> blocked" "2" "$RC"
rm -rf "$SCRATCH2"

# Process probe bypass
INPUT='{"tool_name":"Bash","tool_input":{"command":"pgrep -fa vitest"}}'
RC=$(printf '%s' "$INPUT" | CLAUDE_PROJECT_DIR="$SCRATCH2" bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "pgrep vitest allowed" "0" "$RC"

echo
echo "=== bash-guards: no-open-source ==="
INPUT='{"tool_name":"Bash","tool_input":{"command":"open foo.py"}}'
RES=$(run_hook bash-guards.sh "$INPUT")
RC="${RES%%|*}"
assert_eq "open foo.py blocked" "2" "$RC"

INPUT='{"tool_name":"Bash","tool_input":{"command":"open report.html"}}'
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "open report.html allowed" "0" "$RC"

INPUT='{"tool_name":"Bash","tool_input":{"command":"open https://example.com"}}'
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "open https URL allowed" "0" "$RC"

echo
echo "=== bash-guards: stash-label-audit (tori-gated) ==="
NO_FLAG="$SCRATCH/no_flag.json"
WITH_FLAG="$SCRATCH/with_flag.json"
echo '{}' > "$NO_FLAG"
echo '{"enable_tori_rules": true}' > "$WITH_FLAG"

INPUT='{"tool_name":"Bash","tool_input":{"command":"git stash"}}'
RC=$(printf '%s' "$INPUT" | MYOS_CONFIG_PATH="$NO_FLAG" bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "bare stash + no flag -> allowed" "0" "$RC"

RC=$(printf '%s' "$INPUT" | MYOS_CONFIG_PATH="$WITH_FLAG" bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "bare stash + flag -> blocked" "2" "$RC"

# Pass-through: stash list shouldn't be blocked
INPUT='{"tool_name":"Bash","tool_input":{"command":"git stash list"}}'
RC=$(printf '%s' "$INPUT" | MYOS_CONFIG_PATH="$WITH_FLAG" bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "git stash list allowed even with flag" "0" "$RC"

echo
echo "=== edit-postwatch: check-tsx (fail-open if tsc missing) ==="
SCRATCH3=$(mktemp -d)
mkdir -p "$SCRATCH3/app/src"
echo '{}' > "$SCRATCH3/app/tsconfig.json"
INPUT="{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$SCRATCH3/app/src/Foo.tsx\"}}"
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/edit-postwatch.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "tsx edit + no tsc -> exit 0" "0" "$RC"
rm -rf "$SCRATCH3"

# Non-tsx files: noop
INPUT='{"tool_name":"Edit","tool_input":{"file_path":"/tmp/foo.py"}}'
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/edit-postwatch.sh" >/dev/null 2>/dev/null; echo $?)
assert_eq "py file ignored by check-tsx" "0" "$RC"

echo
echo "=== edit-postwatch: saa-after-3-hypotheses (tori-gated) ==="
TARGET="$SCRATCH/target_$RANDOM.py"
INPUT_FLAG_ON="{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TARGET\"}}"
STATE="$SCRATCH/cycles.json"
# 1 edit: no reminder
OUT=$(printf '%s' "$INPUT_FLAG_ON" | MYOS_CONFIG_PATH="$WITH_FLAG" MYOS_EDIT_CYCLES_STATE="$STATE" bash "$HOOKS_DIR/edit-postwatch.sh" 2>&1)
assert_not_contains "1 edit no SAA REMINDER" "SAA REMINDER" "$OUT"
# 3 more edits to trip threshold
for i in 1 2 3; do
  OUT=$(printf '%s' "$INPUT_FLAG_ON" | MYOS_CONFIG_PATH="$WITH_FLAG" MYOS_EDIT_CYCLES_STATE="$STATE" bash "$HOOKS_DIR/edit-postwatch.sh" 2>&1)
done
assert_contains "3+ edits + flag -> SAA REMINDER" "SAA REMINDER" "$OUT"

# No flag: no reminder even after many edits
TARGET2="$SCRATCH/no_flag_$RANDOM.py"
INPUT_NF="{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TARGET2\"}}"
STATE2="$SCRATCH/cycles2.json"
for i in 1 2 3 4; do
  OUT=$(printf '%s' "$INPUT_NF" | MYOS_CONFIG_PATH="$NO_FLAG" MYOS_EDIT_CYCLES_STATE="$STATE2" bash "$HOOKS_DIR/edit-postwatch.sh" 2>&1)
done
assert_not_contains "no flag -> no SAA REMINDER ever" "SAA REMINDER" "$OUT"

echo
echo "=== bash-postwatch: api-probe-hint ==="
INPUT='{"tool_name":"Bash","tool_input":{"command":"curl http://localhost:8000/api/agents"},"tool_response":{"stderr":"+ exit:52\n"}}'
OUT=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-postwatch.sh" 2>&1)
assert_contains "exit 52 hint emitted" "→914" "$OUT"

INPUT='{"tool_name":"Bash","tool_input":{"command":"curl http://localhost:8000/api/agents"},"tool_response":{"output":"ok\n"}}'
OUT=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-postwatch.sh" 2>&1)
assert_not_contains "clean response no hint" "→914" "$OUT"

echo
echo "=== bash-postwatch: tool-retry-queue ==="
QF="$SCRATCH/retry-queue.jsonl"
HOME_OVERRIDE="$SCRATCH/home"
mkdir -p "$HOME_OVERRIDE/.myos/subagents"
INPUT='{"tool_name":"Bash","tool_input":{"command":"curl ..."},"tool_response":{"stderr":"connection reset by peer"}}'
OUT=$(printf '%s' "$INPUT" | HOME="$HOME_OVERRIDE" bash "$HOOKS_DIR/bash-postwatch.sh" 2>&1)
assert_contains "connection reset queues retry" "Queued Bash for retry" "$OUT"

echo
echo "=== summary ==="
echo "  passed: $PASS"
echo "  failed: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
