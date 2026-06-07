#!/bin/bash
# Test install.sh --with-claude-hooks opt-in flag and env var behavior.
# Uses a minimal fake environment to reach the hooks decision point
# without running pip/npm/ostk against real system state.
set -u

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/../.." && pwd)"
INSTALL="$REPO_ROOT/install.sh"

if [ ! -f "$INSTALL" ]; then
  echo "FAIL: install_sh_exists - install.sh not found"
  exit 1
fi

PASS=0
FAIL=0
pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL: $1 - $2"; FAIL=$((FAIL+1)); }

# --- Test 1: --help exits 0 and documents the flag ---
help_out=$(bash "$INSTALL" --help 2>&1)
rc=$?
if [ "$rc" -eq 0 ]; then
  pass "install_help_exits_0"
else
  fail "install_help_exits_0" "exit code: $rc"
fi
if echo "$help_out" | grep -q "\-\-with-claude-hooks"; then
  pass "install_help_documents_with_claude_hooks"
else
  fail "install_help_documents_with_claude_hooks" "flag not in help output"
fi
if echo "$help_out" | grep -q "\-\-without-claude-hooks"; then
  pass "install_help_documents_without_claude_hooks"
else
  fail "install_help_documents_without_claude_hooks" "--without-claude-hooks not in help"
fi

# --- Test 2: unknown flag exits 2 ---
rc=$(bash "$INSTALL" --badarg 2>/dev/null; echo $?)
if [ "$rc" = "2" ]; then
  pass "install_unknown_flag_exits_2"
else
  fail "install_unknown_flag_exits_2" "exit code: $rc"
fi

# --- Build a minimal fake install environment for behavior tests ---
tmp_dir=$(mktemp -d -t install-hooks-test-XXXXXX)
trap 'rm -rf "$tmp_dir"' EXIT

FAKE_HOME="$tmp_dir/home"
FAKE_REPO="$tmp_dir/repo"
FAKE_BIN="$tmp_dir/bin"
SENTINEL="$tmp_dir/hooks-installed.sentinel"

mkdir -p "$FAKE_HOME/.youros" "$FAKE_BIN"
mkdir -p "$FAKE_REPO/api" "$FAKE_REPO/app" "$FAKE_REPO/scripts"
mkdir -p "$FAKE_REPO/.claude/hooks/lib"

# Required repo structure
touch "$FAKE_REPO/start.sh" "$FAKE_REPO/update.sh" "$FAKE_REPO/myos-track.sh" "$FAKE_REPO/myos-claude.sh"
chmod +x "$FAKE_REPO/start.sh" "$FAKE_REPO/update.sh" "$FAKE_REPO/myos-track.sh" "$FAKE_REPO/myos-claude.sh"
echo '{}' > "$FAKE_REPO/settings.default.json"
echo '{"stitch_api_key": ""}' > "$FAKE_REPO/.mcp.json.example"
touch "$FAKE_REPO/api/requirements.txt"
touch "$FAKE_REPO/.claude/hooks/register-agent.sh"
touch "$FAKE_REPO/.claude/hooks/lib/drain-pending.sh"
chmod +x "$FAKE_REPO/.claude/hooks/register-agent.sh"

# Fake install-claude-hooks.sh: touches the sentinel
cat > "$FAKE_REPO/scripts/install-claude-hooks.sh" <<HOOKSCRIPT
#!/bin/bash
touch "$SENTINEL"
exit 0
HOOKSCRIPT
chmod +x "$FAKE_REPO/scripts/install-claude-hooks.sh"

# Fake venv structure
mkdir -p "$FAKE_REPO/api/.venv/bin"
cat > "$FAKE_REPO/api/.venv/bin/activate" <<ACTIVATE
#!/bin/bash
export VIRTUAL_ENV="$FAKE_REPO/api/.venv"
export PATH="\$VIRTUAL_ENV/bin:\$PATH"
deactivate() { unset VIRTUAL_ENV; }
ACTIVATE
printf '#!/bin/bash\nexit 0\n' > "$FAKE_REPO/api/.venv/bin/pip"
chmod +x "$FAKE_REPO/api/.venv/bin/pip"

# Fake commands
for cmd in git node npm ostk; do
  printf '#!/bin/bash\nexit 0\n' > "$FAKE_BIN/$cmd"
  chmod +x "$FAKE_BIN/$cmd"
done
# node -v must return version string
cat > "$FAKE_BIN/node" <<'NODESTUB'
#!/bin/bash
[ "$1" = "-v" ] && echo "v20.0.0" && exit 0
exit 0
NODESTUB
chmod +x "$FAKE_BIN/node"

# python3: intercept version/venv, delegate real scripts
REAL_PY3=$(command -v python3)
cat > "$FAKE_BIN/python3" <<PYSTUB
#!/bin/bash
if [ "\${1:-}" = "-c" ]; then
  case "\${2:-}" in
    *version_info*) echo "3.11"; exit 0 ;;
    *import\ venv*) exit 0 ;;
  esac
fi
if [ "\${1:-}" = "-m" ] && [ "\${2:-}" = "venv" ]; then exit 0; fi
exec "$REAL_PY3" "\$@"
PYSTUB
chmod +x "$FAKE_BIN/python3"

# Copy install.sh to tmp_dir so SCRIPT_DIR != FAKE_REPO (forcing MYOS_DIR path)
cp "$INSTALL" "$tmp_dir/install.sh"

run_fake_install() {
  PATH="$FAKE_BIN:$PATH" HOME="$FAKE_HOME" MYOS_DIR="$FAKE_REPO" \
    bash "$tmp_dir/install.sh" "$@" 2>&1
}

# --- Test 3: default (no flag) skips hooks ---
rm -f "$SENTINEL"
run_fake_install >/dev/null 2>&1 || true
if [ ! -f "$SENTINEL" ]; then
  pass "install_default_skips_claude_hooks"
else
  fail "install_default_skips_claude_hooks" "install-claude-hooks.sh was called without flag"
fi

# --- Test 4: --with-claude-hooks calls install-claude-hooks.sh ---
rm -f "$SENTINEL"
run_fake_install --with-claude-hooks >/dev/null 2>&1 || true
if [ -f "$SENTINEL" ]; then
  pass "install_with_claude_hooks_flag_calls_hook_installer"
else
  fail "install_with_claude_hooks_flag_calls_hook_installer" "sentinel not created"
fi

# --- Test 5: MYOS_INSTALL_CLAUDE_HOOKS=1 env var triggers hook install ---
rm -f "$SENTINEL"
MYOS_INSTALL_CLAUDE_HOOKS=1 PATH="$FAKE_BIN:$PATH" HOME="$FAKE_HOME" \
  MYOS_DIR="$FAKE_REPO" bash "$tmp_dir/install.sh" >/dev/null 2>&1 || true
if [ -f "$SENTINEL" ]; then
  pass "install_env_var_triggers_hook_install"
else
  fail "install_env_var_triggers_hook_install" "MYOS_INSTALL_CLAUDE_HOOKS=1 was ignored"
fi

# --- Test 6: --without-claude-hooks overrides MYOS_INSTALL_CLAUDE_HOOKS=1 ---
rm -f "$SENTINEL"
MYOS_INSTALL_CLAUDE_HOOKS=1 PATH="$FAKE_BIN:$PATH" HOME="$FAKE_HOME" \
  MYOS_DIR="$FAKE_REPO" bash "$tmp_dir/install.sh" --without-claude-hooks >/dev/null 2>&1 || true
if [ ! -f "$SENTINEL" ]; then
  pass "install_without_flag_overrides_env_var"
else
  fail "install_without_flag_overrides_env_var" "--without-claude-hooks did not override env var"
fi

echo ""
echo "passed: $PASS / failed: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
