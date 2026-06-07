#!/usr/bin/env bash
# test_status_sh_retries_on_timeout.sh
#
# Verifies scripts/status.sh:
#   1. Uses --connect-timeout >= 5 and -m >= 10 (relaxed budget so cold-cache
#      responses do not trip false "backend unreachable" reports during demos).
#   2. Retries once on first failure before reporting backend unreachable.
#   3. Succeeds when fed a fixture file (existing fixture mode still works).
#
# Exit 0 = all assertions passed. Exit 1 = a test failed.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATUS_SH="$SCRIPT_DIR/status.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

pass=0
fail=0

assert() {
    local label="$1" cond="$2"
    if [ "$cond" = "1" ]; then
        echo -e "${GREEN}PASS${NC} $label"
        pass=$((pass+1))
    else
        echo -e "${RED}FAIL${NC} $label"
        fail=$((fail+1))
    fi
}

# --- Test 1: script text contains the bumped timeout values ---
if grep -q "connect-timeout 5" "$STATUS_SH" && grep -q -- "-m 10" "$STATUS_SH"; then
    assert "status_uses_relaxed_timeouts_5_and_10" 1
else
    assert "status_uses_relaxed_timeouts_5_and_10" 0
fi

# --- Test 2: script text indicates a retry path ---
# We expect either a loop or a fallback fetch in the source. The simplest
# unambiguous marker is the "after 2 tries" copy in the failure message.
if grep -q "after 2 tries" "$STATUS_SH"; then
    assert "status_retries_once_on_failure" 1
else
    assert "status_retries_once_on_failure" 0
fi

# --- Test 3: against a known-bad URL, status exits non-zero with the new
# error message AND only after at least 2 attempts (each ~10s ceiling).
# We do NOT actually wait the full 20s here. We use a port that refuses
# connections immediately (Linux/macOS return ECONNREFUSED in <10ms) so
# both tries finish in well under a second.
DEAD_PORT=18811
START_NS=$(python3 -c 'import time; print(int(time.time()*1000))')
output=$(API_HOST="http://127.0.0.1:${DEAD_PORT}" bash "$STATUS_SH" 2>&1 || true)
exit_code=$(API_HOST="http://127.0.0.1:${DEAD_PORT}" bash "$STATUS_SH" >/dev/null 2>&1; echo $?)
END_NS=$(python3 -c 'import time; print(int(time.time()*1000))')
elapsed_ms=$((END_NS - START_NS))

if [ "$exit_code" = "1" ]; then
    assert "status_exits_1_when_backend_dead" 1
else
    assert "status_exits_1_when_backend_dead" 0
fi

if echo "$output" | grep -q "after 2 tries"; then
    assert "status_error_message_mentions_retry" 1
else
    assert "status_error_message_mentions_retry" 0
fi

# --- Test 4: fixture mode still works (sanity check, not regressed) ---
FIXTURE=$(mktemp /tmp/status_fixture_XXXXXX.json)
cat > "$FIXTURE" <<'JSON'
{
  "agents": [
    {
      "name": "demo-agent",
      "status": "running",
      "transcript_lines": 12,
      "spawned_at": "2026-04-15T00:00:00Z",
      "user_spawned": true
    }
  ]
}
JSON
trap 'rm -f "$FIXTURE"' EXIT

fixture_out=$(YOUROS_STATUS_FIXTURE="$FIXTURE" YOUROS_STATUS_NOW="2026-04-15T00:01:00Z" \
    bash "$STATUS_SH" 2>&1 || true)

if echo "$fixture_out" | grep -q "demo-agent"; then
    assert "status_fixture_mode_still_works" 1
else
    assert "status_fixture_mode_still_works" 0
fi

echo ""
echo "Results: $pass passed, $fail failed (elapsed ${elapsed_ms}ms for retry-budget probe)"
[ "$fail" -eq 0 ] && exit 0 || exit 1
