#!/bin/bash
# Test myos-track.sh: enable, idempotent, status, remove, missing hook.
set -u

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/../.." && pwd)"
TRACK="$REPO_ROOT/myos-track.sh"

if [ ! -f "$TRACK" ]; then
  echo "FAIL: myos_track_exists - myos-track.sh not found"
  exit 1
fi

PASS=0
FAIL=0
pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL: $1 - $2"; FAIL=$((FAIL+1)); }

tmp_dir=$(mktemp -d -t myos-track-test-XXXXXX)
trap 'rm -rf "$tmp_dir"' EXIT

FAKE_HOME="$tmp_dir/home"
FAKE_HOOK="$FAKE_HOME/.myos/hooks/register-agent.sh"
FAKE_REPO="$tmp_dir/repo"
SETTINGS="$FAKE_REPO/.claude/settings.local.json"

mkdir -p "$FAKE_HOME/.myos/hooks"
printf '#!/bin/bash\nexit 0\n' > "$FAKE_HOOK"
chmod +x "$FAKE_HOOK"
mkdir -p "$FAKE_REPO"

run_track() {
  HOME="$FAKE_HOME" bash "$TRACK" "$@"
}

# --- Test 1: enable creates settings.local.json with hook entry ---
(cd "$FAKE_REPO" && run_track) >/dev/null 2>&1
if [ -f "$SETTINGS" ]; then
  pass "track_enable_creates_settings_file"
else
  fail "track_enable_creates_settings_file" "settings.local.json not created"
fi

# --- Test 2: settings file contains register-agent.sh hook ---
if grep -q "register-agent.sh" "$SETTINGS" 2>/dev/null; then
  pass "track_enable_wires_register_agent_hook"
else
  fail "track_enable_wires_register_agent_hook" "register-agent.sh not in settings: $(cat "$SETTINGS" 2>/dev/null)"
fi

# --- Test 3: matcher is "Agent" ---
if grep -q '"matcher"' "$SETTINGS" && grep -q '"Agent"' "$SETTINGS"; then
  pass "track_enable_matcher_is_Agent"
else
  fail "track_enable_matcher_is_Agent" "Agent matcher not found in: $(cat "$SETTINGS" 2>/dev/null)"
fi

# --- Test 4: idempotent — running enable twice doesn't double-add ---
(cd "$FAKE_REPO" && run_track) >/dev/null 2>&1
count=$(grep -c "register-agent.sh" "$SETTINGS" 2>/dev/null || echo 0)
if [ "$count" -eq 1 ]; then
  pass "track_enable_idempotent"
else
  fail "track_enable_idempotent" "register-agent.sh appears $count times"
fi

# --- Test 5: --status reports tracking ---
out=$(cd "$FAKE_REPO" && run_track --status 2>&1)
if echo "$out" | grep -q "tracking in"; then
  pass "track_status_reports_tracking"
else
  fail "track_status_reports_tracking" "output: $out"
fi

# --- Test 6: --remove strips the entry ---
(cd "$FAKE_REPO" && run_track --remove) >/dev/null 2>&1
if [ ! -f "$SETTINGS" ] || ! grep -q "register-agent.sh" "$SETTINGS" 2>/dev/null; then
  pass "track_remove_strips_hook_entry"
else
  fail "track_remove_strips_hook_entry" "register-agent.sh still in settings after remove"
fi

# --- Test 7: --status after remove reports NOT tracking ---
out=$(cd "$FAKE_REPO" && run_track --status 2>&1)
if echo "$out" | grep -q "NOT tracking"; then
  pass "track_status_reports_not_tracking_after_remove"
else
  fail "track_status_reports_not_tracking_after_remove" "output: $out"
fi

# --- Test 8: missing hook file exits 1 ---
MISSING_HOME="$tmp_dir/missing-home"
mkdir -p "$MISSING_HOME"
rc=$(cd "$FAKE_REPO" && HOME="$MISSING_HOME" bash "$TRACK" 2>/dev/null; echo $?)
if [ "$rc" = "1" ]; then
  pass "track_missing_hook_file_exits_1"
else
  fail "track_missing_hook_file_exits_1" "exit code: $rc"
fi

# --- Test 9: unknown flag exits 2 ---
rc=$(cd "$FAKE_REPO" && HOME="$FAKE_HOME" bash "$TRACK" --badarg 2>/dev/null; echo $?)
if [ "$rc" = "2" ]; then
  pass "track_unknown_flag_exits_2"
else
  fail "track_unknown_flag_exits_2" "exit code: $rc"
fi

echo ""
echo "passed: $PASS / failed: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
