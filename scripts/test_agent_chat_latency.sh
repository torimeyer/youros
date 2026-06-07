#!/usr/bin/env bash
#
# test_agent_chat_latency.sh
#
# Measures round-trip latency from "user types a message" to "the
# message is visible to the agent". This is the demo scenario: Tori
# types into an agent chat, the agent should see it within about two
# seconds, not thirty.
#
# How it measures:
#   1. Registers a throwaway agent over HTTP.
#   2. In the background, starts a long-poll on GET /nudges?wait=30.
#   3. Sends a POST /nudge with a timestamped body.
#   4. Waits for the long-poll to return and records the elapsed time.
#   5. Asserts the round trip is under 2000 ms.
#
# The script talks to the running yourOS backend on localhost:8000. If
# the backend is not up it prints a clear error instead of hanging.
#
# Usage:
#   scripts/test_agent_chat_latency.sh [threshold_ms]
#
# Default threshold is 2000 ms. Pass a different value to tighten or
# loosen the budget.

set -u

THRESHOLD_MS="${1:-2000}"
BASE_URL="https://127.0.0.1:8000"
AGENT_NAME="latency-test-$$-$RANDOM"

die() {
    printf 'latency test failed: %s\n' "$1" >&2
    cleanup_agent
    exit 1
}

cleanup_agent() {
    curl -sk --connect-timeout 3 -m 5 \
        -X POST "$BASE_URL/api/agents/$AGENT_NAME/complete" \
        -H 'Content-Type: application/json' \
        -d '{"summary": "latency test over"}' >/dev/null 2>&1 || true
}

# Confirm the backend is reachable. If not, bail with a human error.
if ! curl -sk --connect-timeout 3 -m 5 -o /dev/null "$BASE_URL/api/health"; then
    die "backend unreachable at $BASE_URL (start it with scripts/dev-backend.sh)"
fi

# 1. Register the agent so /nudge has somewhere to land.
REGISTER_BODY=$(printf '{"name":"%s","status":"running","task":"latency self-test","source":"test"}' "$AGENT_NAME")
if ! curl -sk --connect-timeout 3 -m 5 \
    -X POST "$BASE_URL/api/agents/register" \
    -H 'Content-Type: application/json' \
    -d "$REGISTER_BODY" >/dev/null; then
    die "could not register test agent"
fi

# 2. Kick off a long-poll in the background. Use ``since`` with an old
#    timestamp so the server will block until a new nudge arrives.
TMP_OUT=$(mktemp)
START_FILE=$(mktemp)
END_FILE=$(mktemp)

# Capture a monotonic start just before the long-poll opens its socket.
python3 -c "import time; print(time.monotonic_ns())" >"$START_FILE"

(
    curl -sk --connect-timeout 3 -m 35 \
        "$BASE_URL/api/agents/$AGENT_NAME/nudges?wait=30&since=2000-01-01T00:00:00+00:00" \
        >"$TMP_OUT"
    python3 -c "import time; print(time.monotonic_ns())" >"$END_FILE"
) &
LONG_POLL_PID=$!

# Give the long-poll a moment to park on the event before we POST. A
# tiny sleep is enough to guarantee the GET has arrived at the server.
sleep 0.1

# 3. Send a nudge. This must wake the long-poll.
NUDGE_BODY='{"message":"hey, also add the login flow"}'
curl -sk --connect-timeout 3 -m 5 \
    -X POST "$BASE_URL/api/agents/$AGENT_NAME/nudge" \
    -H 'Content-Type: application/json' \
    -d "$NUDGE_BODY" >/dev/null || die "nudge POST failed"

# 4. Wait for the long-poll process to finish and compute elapsed ms.
wait "$LONG_POLL_PID" || die "long-poll curl exited non-zero"

START_NS=$(cat "$START_FILE")
END_NS=$(cat "$END_FILE")
ELAPSED_MS=$(( (END_NS - START_NS) / 1000000 ))

# 5. Confirm the payload actually contains the nudge. A silent empty
#    return would be worse than a slow return, so we guard it.
if ! grep -q "hey, also add the login flow" "$TMP_OUT"; then
    printf 'response body was:\n%s\n' "$(cat "$TMP_OUT")" >&2
    die "long-poll returned without the expected nudge message"
fi

cleanup_agent
rm -f "$TMP_OUT" "$START_FILE" "$END_FILE"

printf 'agent chat latency: %d ms (threshold %d ms)\n' "$ELAPSED_MS" "$THRESHOLD_MS"

if [ "$ELAPSED_MS" -gt "$THRESHOLD_MS" ]; then
    printf 'latency over budget\n' >&2
    exit 2
fi

exit 0
