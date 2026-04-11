#!/usr/bin/env bash
# Regression test for the tori() shell function in ~/.zshrc.
#
# Bug: tori() used npm (npm info / npm install -g) to check and update
# Claude Code, but Claude Code on this machine is installed via the
# native installer, not npm. npm would have installed a second copy
# that the PATH symlink would not resolve to, silently failing to
# update the real binary. Fix: let "command claude update" handle it.
#
# This test enforces two static invariants on the tori() function in
# ~/.zshrc so the bug cannot regress:
#   1. tori() MUST invoke "command claude update".
#   2. tori() MUST NOT invoke "npm install -g @anthropic-ai/claude-code".

set -u

ZSHRC="${ZSHRC:-$HOME/.zshrc}"

if [[ ! -f "$ZSHRC" ]]; then
  echo "FAIL: $ZSHRC not found" >&2
  exit 1
fi

# Extract the tori() function body. Start at the line beginning with
# "tori() {" and stop at the first line that is exactly "}" at column 0.
tori_body=$(awk '
  /^tori\(\) \{/ { inside = 1 }
  inside { print }
  inside && /^\}$/ { exit }
' "$ZSHRC")

if [[ -z "$tori_body" ]]; then
  echo "FAIL: could not locate tori() function in $ZSHRC" >&2
  exit 1
fi

fail=0

# Invariant 1: must call "command claude update".
if ! grep -qF 'command claude update' <<<"$tori_body"; then
  echo "FAIL: tori() is missing 'command claude update'. The native" >&2
  echo "      Claude Code updater must be invoked so the real binary" >&2
  echo "      gets updated instead of an npm side-copy." >&2
  fail=1
fi

# Invariant 2: must NOT call "npm install -g @anthropic-ai/claude-code".
if grep -qF 'npm install -g @anthropic-ai/claude-code' <<<"$tori_body"; then
  echo "FAIL: tori() calls 'npm install -g @anthropic-ai/claude-code'." >&2
  echo "      Claude Code is installed via the native installer here," >&2
  echo "      so npm installs a parallel copy that the PATH symlink" >&2
  echo "      does not resolve to. Use 'command claude update' instead." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "PASS: tori() uses 'command claude update' and does not shell out to npm for Claude Code."
exit 0
