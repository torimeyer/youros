#!/bin/bash
# Budget test for prompt-header.sh (UserPromptSubmit).
#
# Every byte this hook prints is re-added to the conversation on every user
# message and never leaves it, so long sessions fill up from this block alone.
# Verifies, with every rule block enabled and the backend unreachable:
#   1. Total output stays under BUDGET chars.
#   2. The load-bearing rules are still present.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../prompt-header.sh"
BUDGET=1300
FAILED=0

T=$(mktemp -d -t prompt-budget.XXXXXX)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/.youros"
cat > "$T/HUMANFILE" <<'EOF'
IDENTITY test
- Git commits: save verified wins as you go.
EOF
cat > "$T/.youros/rules.json" <<EOF
{"schema_version":1,"rules":{
  "adhd_monitor_pairing":{"enabled":true},
  "humanfile_render":{"enabled":true,"path":"$T/HUMANFILE"},
  "standing_rules_blocks":{"receipts_check":true,"stall_death_check":true,"zero_byte_transcript_check":true}
}}
EOF

OUT=$(echo '{"prompt":"hi"}' | HOME="$T" YOUROS_BACKEND_URL="https://127.0.0.1:19743" bash "$HOOK" 2>/dev/null)
LEN=${#OUT}

fail() { echo "FAIL: $1"; FAILED=1; }
pass() { echo "  pass: $1"; }

[ "$LEN" -le "$BUDGET" ] && pass "output $LEN chars <= $BUDGET" || fail "output $LEN chars > $BUDGET"

for needle in "ostk tools first" "saa" "mcp__ostk__spawn" "RECEIPTS" "commit hash" \
              "0-byte" "ANTHROPIC_API_KEY" "3 of 5" "Every 60s" "save verified wins"; do
  echo "$OUT" | grep -qF "$needle" && pass "has: $needle" || fail "missing: $needle"
done

if [ "$FAILED" -eq 0 ]; then echo "PASS"; exit 0; else echo "FAILED"; exit 1; fi
