#!/bin/bash
# Regression test: heartbeat-agent.sh uses MYOS_AGENT_NAME when set.
#
# Root cause (commit that introduced this test): worktree subagents are
# registered under a custom name (e.g. "settings-nav-dcdbdb") while
# heartbeat-agent.sh was deriving a session_id-based name
# ("claude-code-<prefix>"). Heartbeats went to a 404 endpoint and were
# silently dropped, leaving last_heartbeat_at null for the entire run.
#
# Run manually:
#   bash .claude/hooks/tests/heartbeat-myos-agent-name.sh

set -u

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$HOOKS_DIR/heartbeat-agent.sh"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

ok() {
    printf 'ok: %s\n' "$1"
}

# ------------------------------------------------------------------
# Test 1: MYOS_AGENT_NAME is used when set, not session_id-derived name
# ------------------------------------------------------------------
# Intercept curl calls by injecting a fake curl into PATH that logs the URL.
FAKE_BIN="$TMP_DIR/bin"
mkdir -p "$FAKE_BIN"
CURL_LOG="$TMP_DIR/curl-calls.log"
cat > "$FAKE_BIN/curl" <<'EOF'
#!/bin/bash
echo "$@" >> "$CURL_LOG"
exit 0
EOF
chmod +x "$FAKE_BIN/curl"

PAYLOAD='{"tool_name":"Bash","session_id":"abc1234567890","tool_input":{"command":"echo test"}}'

# With MYOS_AGENT_NAME set to a custom name.
# Note: env var prefixes before a pipeline apply to the FIRST command (printf),
# not bash. Export explicitly so bash and its background subshells inherit them.
export CURL_LOG FAKE_BIN
MYOS_AGENT_NAME="my-custom-agent-abc123" \
    printf '%s' "$PAYLOAD" | \
    MYOS_AGENT_NAME="my-custom-agent-abc123" PATH="$FAKE_BIN:$PATH" bash "$HOOK" >/dev/null 2>&1 || true

# Give the detached background subshells a moment to write.
sleep 0.5

if [ ! -s "$CURL_LOG" ]; then
    fail "curl was never called (log empty)"
fi

if grep -q "my-custom-agent-abc123/heartbeat" "$CURL_LOG"; then
    ok "MYOS_AGENT_NAME routed heartbeat to custom agent name"
else
    fail "heartbeat did not use MYOS_AGENT_NAME. curl calls were: $(cat "$CURL_LOG")"
fi

if grep -qE "claude-code-abc/heartbeat" "$CURL_LOG"; then
    fail "session_id-derived name was used even though MYOS_AGENT_NAME was set"
fi
ok "session_id-derived name was NOT used when MYOS_AGENT_NAME was set"

# ------------------------------------------------------------------
# Test 2: Falls back to session_id-derived name when MYOS_AGENT_NAME absent
# ------------------------------------------------------------------
rm -f "$CURL_LOG"

# env -u unsets MYOS_AGENT_NAME; PATH injection goes to bash (after pipe).
printf '%s' "$PAYLOAD" | \
    env -u MYOS_AGENT_NAME PATH="$FAKE_BIN:$PATH" bash "$HOOK" >/dev/null 2>&1 || true

sleep 0.5

# Session prefix is first 10 chars of "abc1234567890" = "abc1234567".
if grep -q "claude-code-abc1234567/heartbeat" "$CURL_LOG"; then
    ok "fell back to session_id-derived name when MYOS_AGENT_NAME absent"
elif grep -q "claude-code-" "$CURL_LOG"; then
    ok "fell back to session_id-derived name (claude-code- prefix present)"
else
    fail "expected claude-code- fallback name. curl calls: $(cat "$CURL_LOG" 2>/dev/null)"
fi

if grep -q "my-custom-agent-abc123/heartbeat" "$CURL_LOG"; then
    fail "custom name was used even though MYOS_AGENT_NAME was not set"
fi
ok "custom name was NOT used when MYOS_AGENT_NAME absent"

printf 'PASS: heartbeat-agent.sh MYOS_AGENT_NAME routing works correctly\n'
