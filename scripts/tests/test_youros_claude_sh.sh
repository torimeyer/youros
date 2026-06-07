#!/bin/bash
# Test youros-claude.sh: settings written before claude, restored on exit, guards.
set -u

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/../.." && pwd)"
YOUROS_CLAUDE="$REPO_ROOT/youros-claude.sh"

if [ ! -f "$YOUROS_CLAUDE" ]; then
  echo "FAIL: youros_claude_exists - youros-claude.sh not found"
  exit 1
fi

PASS=0
FAIL=0
pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL: $1 - $2"; FAIL=$((FAIL+1)); }

tmp_dir=$(mktemp -d -t youros-claude-test-XXXXXX)
trap 'rm -rf "$tmp_dir"' EXIT

FAKE_HOME="$tmp_dir/home"
FAKE_HOOK="$FAKE_HOME/.youros/hooks/register-agent.sh"
FAKE_REPO="$tmp_dir/repo"
SETTINGS="$FAKE_REPO/.claude/settings.local.json"

mkdir -p "$FAKE_HOME/.youros/hooks" "$FAKE_REPO"
printf '#!/bin/bash\nexit 0\n' > "$FAKE_HOOK"
chmod +x "$FAKE_HOOK"

FAKE_BIN="$tmp_dir/bin"
mkdir -p "$FAKE_BIN"

# --- Test 1: missing hook file exits 1 ---
rc=$(cd "$FAKE_REPO" && HOME="$tmp_dir/no-home" bash "$YOUROS_CLAUDE" 2>/dev/null; echo $?)
if [ "$rc" = "1" ]; then
  pass "youros_claude_missing_hook_exits_1"
else
  fail "youros_claude_missing_hook_exits_1" "exit code: $rc"
fi

# --- Test 2: missing claude CLI exits 1 ---
# Use system bins so bash/python3 work, but exclude ~/.local/bin where claude lives.
rc=$(cd "$FAKE_REPO" && HOME="$FAKE_HOME" PATH="/usr/bin:/bin" bash "$YOUROS_CLAUDE" 2>/dev/null; echo $?)
if [ "$rc" = "1" ]; then
  pass "youros_claude_missing_claude_exits_1"
else
  fail "youros_claude_missing_claude_exits_1" "exit code: $rc"
fi

# --- Test 3: --help exits 0 ---
rc=$(cd "$FAKE_REPO" && HOME="$FAKE_HOME" bash "$YOUROS_CLAUDE" --help >/dev/null 2>&1; echo $?)
if [ "$rc" = "0" ]; then
  pass "youros_claude_help_exits_0"
else
  fail "youros_claude_help_exits_0" "exit code: $rc"
fi

# --- Test 4: settings.local.json is written before claude runs ---
SEEN_SETTINGS="$tmp_dir/seen-settings.json"
# Fake claude: records settings content at run time then exits 0
cat > "$FAKE_BIN/claude" <<CLAUDE_STUB
#!/bin/bash
if [ -f "$SETTINGS" ]; then
  cp "$SETTINGS" "$SEEN_SETTINGS"
fi
exit 0
CLAUDE_STUB
chmod +x "$FAKE_BIN/claude"

(cd "$FAKE_REPO" && HOME="$FAKE_HOME" PATH="$FAKE_BIN:/usr/bin:/bin" bash "$YOUROS_CLAUDE") >/dev/null 2>&1

if [ -f "$SEEN_SETTINGS" ]; then
  pass "youros_claude_writes_settings_before_claude_runs"
else
  fail "youros_claude_writes_settings_before_claude_runs" "claude did not see settings.local.json"
fi
if grep -q "register-agent.sh" "$SEEN_SETTINGS" 2>/dev/null; then
  pass "youros_claude_settings_contains_hook"
else
  fail "youros_claude_settings_contains_hook" "hook not in settings: $(cat "$SEEN_SETTINGS" 2>/dev/null)"
fi

# --- Test 5: pre-existing settings are preserved (merged, not overwritten) ---
# youros-claude.sh merges the hook into existing settings rather than replacing them.
ORIGINAL_SETTINGS='{"somekey": "somevalue"}'
mkdir -p "$FAKE_REPO/.claude"
echo "$ORIGINAL_SETTINGS" > "$SETTINGS"

(cd "$FAKE_REPO" && HOME="$FAKE_HOME" PATH="$FAKE_BIN:/usr/bin:/bin" bash "$YOUROS_CLAUDE") >/dev/null 2>&1

if [ -f "$SETTINGS" ]; then
  merged=$(cat "$SETTINGS")
  if echo "$merged" | grep -q "somevalue" && echo "$merged" | grep -q "register-agent.sh"; then
    pass "youros_claude_merges_hook_into_existing_settings"
  else
    fail "youros_claude_merges_hook_into_existing_settings" "settings content: $merged"
  fi
else
  fail "youros_claude_merges_hook_into_existing_settings" "settings.local.json was deleted"
fi

echo ""
echo "passed: $PASS / failed: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
