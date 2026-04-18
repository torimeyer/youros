#!/usr/bin/env bash
#
# test_agent_chat_roundtrip.sh
#
# End-to-end proof that the full inline agent chat round-trip works:
#   user  -> POST /nudge       (message goes to the agent)
#   agent -> POST /reply       (agent posts its answer)
#   user  -> GET  /nudges      (frontend renders the reply)
#
# The live demo failure Tori hit was: she would send "how is it going?"
# and the reply never arrived. The test runs every hop of the contract
# with ordinary curl so a future regression anywhere in the chain fails
# this script on its own, not a unit test inside a mock.
#
# Usage:
#   scripts/test_agent_chat_roundtrip.sh
#
# Requires the backend to be running on localhost:8000. Exits non-zero
# on any broken hop and prints which hop failed.

set -u

BASE_URL="https://127.0.0.1:8000"
AGENT_NAME="roundtrip-$$-$RANDOM"

die() {
    printf 'round-trip test failed: %s\n' "$1" >&2
    cleanup
    exit 1
}

cleanup() {
    curl -sk --connect-timeout 3 -m 5 \
        -X POST "$BASE_URL/api/agents/$AGENT_NAME/complete" \
        -H 'Content-Type: application/json' \
        -d '{"summary": "round-trip test done"}' >/dev/null 2>&1 || true
}

# Confirm backend is up.
if ! curl -sk --connect-timeout 3 -m 5 -o /dev/null "$BASE_URL/api/health"; then
    die "backend unreachable at $BASE_URL (start it with scripts/dev-backend.sh)"
fi

# Step 1: register the agent.
REGISTER_BODY=$(printf '{"name":"%s","status":"running","task":"round-trip self-test","source":"test"}' "$AGENT_NAME")
if ! curl -sk --connect-timeout 3 -m 5 \
    -X POST "$BASE_URL/api/agents/register" \
    -H 'Content-Type: application/json' \
    -d "$REGISTER_BODY" >/dev/null; then
    die "hop 1 (register) failed"
fi

# Step 2: post a user nudge.
if ! curl -sk --connect-timeout 3 -m 5 \
    -X POST "$BASE_URL/api/agents/$AGENT_NAME/nudge" \
    -H 'Content-Type: application/json' \
    -d '{"message":"how is it going?"}' >/dev/null; then
    die "hop 2 (POST /nudge) failed"
fi

# Step 3: simulate the agent posting a reply. This is the hop most
# likely to break in production because agents under claude --print
# often never call /reply on their own. The script exercises it
# directly so the backend contract is proven even when the live LLM
# forgets.
REPLY_BODY='{"message":"all good, still working on it"}'
REPLY_RESP=$(curl -sk --connect-timeout 3 -m 5 \
    -X POST "$BASE_URL/api/agents/$AGENT_NAME/reply" \
    -H 'Content-Type: application/json' \
    -d "$REPLY_BODY")
if [ -z "$REPLY_RESP" ]; then
    die "hop 3 (POST /reply) returned empty body"
fi

# Step 4: poll /nudges as the frontend does. The reply must surface.
POLL_OUT=$(curl -sk --connect-timeout 3 -m 5 \
    "$BASE_URL/api/agents/$AGENT_NAME/nudges")

if ! printf '%s' "$POLL_OUT" | grep -q "all good, still working on it"; then
    printf 'response body was:\n%s\n' "$POLL_OUT" >&2
    die "hop 4 (GET /nudges) never surfaced the reply"
fi

if ! printf '%s' "$POLL_OUT" | grep -q "how is it going?"; then
    printf 'response body was:\n%s\n' "$POLL_OUT" >&2
    die "hop 4 (GET /nudges) lost the original user nudge"
fi

# Step 5 (bridge test): complete with a summary. The backend should
# mirror the summary into /reply so agents that forget to call /reply
# still get their final word shown in the thread. A second agent keeps
# this clean and decoupled from the previous reply above.
BRIDGE_AGENT="bridge-$$-$RANDOM"
BRIDGE_REG=$(printf '{"name":"%s","status":"running","task":"bridge test","source":"test"}' "$BRIDGE_AGENT")
curl -sk --connect-timeout 3 -m 5 \
    -X POST "$BASE_URL/api/agents/register" \
    -H 'Content-Type: application/json' \
    -d "$BRIDGE_REG" >/dev/null || die "hop 5 (bridge agent register) failed"

curl -sk --connect-timeout 3 -m 5 \
    -X POST "$BASE_URL/api/agents/$BRIDGE_AGENT/complete" \
    -H 'Content-Type: application/json' \
    -d '{"summary":"I finished the task. Final answer here."}' >/dev/null \
    || die "hop 5 (bridge agent complete) failed"

BRIDGE_POLL=$(curl -sk --connect-timeout 3 -m 5 \
    "$BASE_URL/api/agents/$BRIDGE_AGENT/nudges")
if ! printf '%s' "$BRIDGE_POLL" | grep -q "I finished the task"; then
    printf 'bridge poll response:\n%s\n' "$BRIDGE_POLL" >&2
    die "hop 5 (complete-summary bridge) did not mirror summary into /reply"
fi

cleanup

printf 'agent chat round-trip: ok\n'
exit 0
