#!/bin/bash
# Test uninstall.sh: artifacts removed, aliases stripped, flags parsed.
set -u

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/../.." && pwd)"
UNINSTALL_SRC="$REPO_ROOT/uninstall.sh"

if [ ! -f "$UNINSTALL_SRC" ]; then
  echo "FAIL: uninstall_sh_exists - uninstall.sh not found"
  exit 1
fi

PASS=0
FAIL=0
pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL: $1 - $2"; FAIL=$((FAIL+1)); }

tmp_dir=$(mktemp -d -t uninstall-test-XXXXXX)
trap 'rm -rf "$tmp_dir"' EXIT

setup_fake_install() {
  local dir="$1"
  local home="$2"
  mkdir -p "$dir/api/.venv" "$dir/app/node_modules" "$dir/app/dist"
  touch "$dir/.mcp.json"
  printf '#!/bin/bash\nexit 0\n' > "$dir/stop.sh"
  chmod +x "$dir/stop.sh"
  cp "$UNINSTALL_SRC" "$dir/uninstall.sh"
  mkdir -p "$home"
  cat > "$home/.zshrc" <<'ZSHRC'
# some existing config
alias myos='/fake/path/start.sh'
alias myos-update='/fake/path/update.sh'
alias myos-track='/fake/path/myos-track.sh'
alias myos-claude='/fake/path/myos-claude.sh'
export FOO=bar
ZSHRC
}

FAKE_INSTALL="$tmp_dir/repo"
FAKE_HOME="$tmp_dir/home"
setup_fake_install "$FAKE_INSTALL" "$FAKE_HOME"

# --- Test 1: default run removes artifacts ---
HOME="$FAKE_HOME" bash "$FAKE_INSTALL/uninstall.sh" --yes >/dev/null 2>&1
if [ ! -d "$FAKE_INSTALL/api/.venv" ]; then
  pass "uninstall_removes_api_venv"
else
  fail "uninstall_removes_api_venv" "api/.venv still present"
fi
if [ ! -d "$FAKE_INSTALL/app/node_modules" ]; then
  pass "uninstall_removes_node_modules"
else
  fail "uninstall_removes_node_modules" "app/node_modules still present"
fi
if [ ! -d "$FAKE_INSTALL/app/dist" ]; then
  pass "uninstall_removes_app_dist"
else
  fail "uninstall_removes_app_dist" "app/dist still present"
fi
if [ ! -f "$FAKE_INSTALL/.mcp.json" ]; then
  pass "uninstall_removes_mcp_json"
else
  fail "uninstall_removes_mcp_json" ".mcp.json still present"
fi

# --- Test 2: aliases removed from .zshrc ---
ZSHRC_FILE="$FAKE_HOME/.zshrc"
if ! grep -q "alias myos=" "$ZSHRC_FILE" 2>/dev/null; then
  pass "uninstall_removes_myos_alias"
else
  fail "uninstall_removes_myos_alias" "alias myos= still in .zshrc"
fi
if ! grep -q "alias myos-update=" "$ZSHRC_FILE" 2>/dev/null; then
  pass "uninstall_removes_myos_update_alias"
else
  fail "uninstall_removes_myos_update_alias" "alias myos-update= still in .zshrc"
fi
if ! grep -q "alias myos-track=" "$ZSHRC_FILE" 2>/dev/null; then
  pass "uninstall_removes_myos_track_alias"
else
  fail "uninstall_removes_myos_track_alias" "alias myos-track= still in .zshrc"
fi
if ! grep -q "alias myos-claude=" "$ZSHRC_FILE" 2>/dev/null; then
  pass "uninstall_removes_myos_claude_alias"
else
  fail "uninstall_removes_myos_claude_alias" "alias myos-claude= still in .zshrc"
fi

# --- Test 3: non-alias lines preserved ---
if grep -q "export FOO=bar" "$ZSHRC_FILE" 2>/dev/null; then
  pass "uninstall_preserves_unrelated_zshrc_lines"
else
  fail "uninstall_preserves_unrelated_zshrc_lines" "unrelated line was removed"
fi

# --- Test 4: unknown flag exits 2 ---
rc=$(HOME="$FAKE_HOME" bash "$UNINSTALL_SRC" --badarg 2>/dev/null; echo $?)
if [ "$rc" = "2" ]; then
  pass "uninstall_unknown_flag_exits_2"
else
  fail "uninstall_unknown_flag_exits_2" "exit code: $rc"
fi

# --- Test 5: --help exits 0 ---
rc=$(bash "$UNINSTALL_SRC" --help >/dev/null 2>&1; echo $?)
if [ "$rc" = "0" ]; then
  pass "uninstall_help_exits_0"
else
  fail "uninstall_help_exits_0" "exit code: $rc"
fi

echo ""
echo "passed: $PASS / failed: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
