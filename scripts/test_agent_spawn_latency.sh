#!/usr/bin/env bash
# Live latency regression for the agent spawn path.
#
# Hits the running backend with a trivial /agents/register and /agents/heartbeat
# and asserts the round trip stays under the demo-critical budget:
#   register  < 500 ms
#   heartbeat < 3000 ms
#
# If either budget is exceeded the script exits non-zero with a plain
# language reason so CI and release-prep scripts can block the demo on
# a real speed regression. Uses --connect-timeout 3 -m 5 per project rules.
#
# Usage:
#   scripts/test_agent_spawn_latency.sh                 # assert budgets
#   scripts/test_agent_spawn_latency.sh --print         # print numbers only
#   YOUROS_API=https://127.0.0.1:8000 scripts/test_agent_spawn_latency.sh
set -euo pipefail

API_BASE="${YOUROS_API:-https://127.0.0.1:8000}"
AGENT_NAME="latency-test-$$-$RANDOM"
REGISTER_BUDGET_MS=500
HEARTBEAT_BUDGET_MS=3000
PRINT_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --print) PRINT_ONLY=1 ;;
    *) ;;
  esac
done

curl_ms() {
  # Run curl and print the total time in milliseconds. Never hang.
  local url="$1"
  shift
  local t
  t=$(curl -sk --connect-timeout 3 -m 5 -o /dev/null \
    -w '%{time_total}' \
    "$@" "$url") || return 1
  # Convert seconds.xxx to integer ms without bc (zsh/bash portable).
  awk -v t="$t" 'BEGIN{printf "%d", t*1000}'
}

# 1) Backend reachable?
if ! curl -sk --connect-timeout 3 -m 5 -o /dev/null "$API_BASE/api/health"; then
  echo "FAIL: backend unreachable at $API_BASE. Start it with scripts/dev-backend.sh" >&2
  exit 2
fi

# 2) Time /agents/register
REGISTER_MS=$(curl_ms "$API_BASE/api/agents/register" \
  -X POST \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"$AGENT_NAME\",\"status\":\"running\",\"task\":\"latency probe\",\"source\":\"test\"}")

# 3) Time /agents/{name}/heartbeat
HEARTBEAT_MS=$(curl_ms "$API_BASE/api/agents/$AGENT_NAME/heartbeat" \
  -X POST \
  -H 'Content-Type: application/json' \
  -d '{"step":"heartbeat probe"}')

# Best effort cleanup. Never fails the test.
curl -sk --connect-timeout 3 -m 5 -o /dev/null -X POST \
  -H 'Content-Type: application/json' \
  -d '{"summary":"latency probe done"}' \
  "$API_BASE/api/agents/$AGENT_NAME/complete" || true

echo "register latency  : ${REGISTER_MS} ms (budget ${REGISTER_BUDGET_MS} ms)"
echo "heartbeat latency : ${HEARTBEAT_MS} ms (budget ${HEARTBEAT_BUDGET_MS} ms)"

if [ "$PRINT_ONLY" = "1" ]; then
  exit 0
fi

FAIL=0
if [ "$REGISTER_MS" -ge "$REGISTER_BUDGET_MS" ]; then
  echo "FAIL: register took ${REGISTER_MS} ms, budget is ${REGISTER_BUDGET_MS} ms. Fix: profile the spawn path in api/routers/agents.py::register_agent, check for new sync I/O." >&2
  FAIL=1
fi
if [ "$HEARTBEAT_MS" -ge "$HEARTBEAT_BUDGET_MS" ]; then
  echo "FAIL: heartbeat took ${HEARTBEAT_MS} ms, budget is ${HEARTBEAT_BUDGET_MS} ms. Fix: check api/routers/agents.py::heartbeat for new sync work on the hot path." >&2
  FAIL=1
fi

exit "$FAIL"
