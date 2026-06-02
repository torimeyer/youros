#!/bin/bash
# Regression test for →977: hooks must source deny.sh via BASH_SOURCE[0],
# not CLAUDE_PROJECT_DIR or git rev-parse, so they work in temp dirs and
# outside any git repo.
#
# (Originally tested via ostk-first.sh copy; that hook was deleted in →1288.
# Now tests deny.sh directly — the actual invariant being guarded is that
# deny.sh is found relative to the hook file's own location, not cwd or git.)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="${SCRIPT_DIR}/.."

# ── Setup: temp dir that is NOT a git repo ───────────────────────────────────
TMPDIR_HOOK=$(mktemp -d)
trap 'rm -rf "$TMPDIR_HOOK"' EXIT

# Create a minimal hook that sources deny.sh via BASH_SOURCE[0] (the pattern
# all real hooks use). This is the property we are testing.
cat > "$TMPDIR_HOOK/test-hook.sh" <<'EOF'
#!/bin/bash
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
. "$LIB/deny.sh"
init_deny_traps
echo "deny_sourced_ok"
EOF
chmod +x "$TMPDIR_HOOK/test-hook.sh"
mkdir -p "$TMPDIR_HOOK/lib"
cp "$HOOKS_DIR/lib/deny.sh" "$TMPDIR_HOOK/lib/deny.sh"

# Working dir with no git repo
TMPDIR_WORK=$(mktemp -d)
trap 'rm -rf "$TMPDIR_HOOK" "$TMPDIR_WORK"' EXIT

# ── Run: no CLAUDE_PROJECT_DIR, cwd outside git, no ostk socket ─────────────
output=$(cd "$TMPDIR_WORK" && unset CLAUDE_PROJECT_DIR && bash "$TMPDIR_HOOK/test-hook.sh" 2>&1)
exit_code=$?

# ── Assert: no "No such file" error from deny.sh sourcing ───────────────────
if echo "$output" | grep -q "No such file"; then
  echo "FAIL: deny.sh source error detected. Output: $output"
  exit 1
fi

# ── Assert: hook found deny.sh and ran successfully ──────────────────────────
if ! echo "$output" | grep -q "deny_sourced_ok"; then
  echo "FAIL: test-hook.sh did not complete (expected 'deny_sourced_ok'). Got: $output"
  exit 1
fi

# ── Assert: init_deny_traps is callable after sourcing ───────────────────────
callable_output=$(bash -c "
  source '$TMPDIR_HOOK/lib/deny.sh'
  init_deny_traps
  echo 'init_deny_traps: ok'
" 2>&1)
if ! echo "$callable_output" | grep -q "init_deny_traps: ok"; then
  echo "FAIL: init_deny_traps not callable after source. Output: $callable_output"
  exit 1
fi

# ── Assert: advise() is callable after sourcing ───────────────────────────────
advise_output=$(bash -c "
  source '$TMPDIR_HOOK/lib/deny.sh'
  advise 'test-advice-message'
  echo 'advise_returned_ok'
" 2>&1)
if ! echo "$advise_output" | grep -q "Advice:"; then
  echo "FAIL: advise() did not emit 'Advice:'. Output: $advise_output"
  exit 1
fi
if ! echo "$advise_output" | grep -q "advise_returned_ok"; then
  echo "FAIL: advise() did not return 0 (should not exit). Output: $advise_output"
  exit 1
fi

echo "PASS: deny.sh sourced correctly via BASH_SOURCE[0] in temp dir outside git repo"
echo "PASS: init_deny_traps callable after source"
echo "PASS: advise() emits Advice: and returns 0"
exit 0
