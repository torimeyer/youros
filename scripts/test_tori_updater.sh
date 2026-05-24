#!/usr/bin/env bash
# Regression test for the tori() shell function in ~/.zshrc.
#
# Enforces static invariants so known bugs cannot regress:
#   1. tori() MUST invoke "command claude update" (not npm).
#   2. tori() MUST NOT invoke "npm install -g @anthropic-ai/claude-code".
#   3. tori() MUST try "aarch64-apple-darwin-fuse" for ostk downloads (v6.0.0+ naming).
#   4. tori() MUST NOT use "curl -fsL" (silences errors; use -fSL instead).
#   5. tori() MUST run "ostk boot" visibly (not redirected to /dev/null).

set -u

# Default to the tracked canonical tori.zsh so CI + fresh setups guard
# the real source. Override with TORI_ZSH=... or ZSHRC=... if needed.
TORI_ZSH="${TORI_ZSH:-${ZSHRC:-$(cd "$(dirname "$0")" && pwd)/tori.zsh}}"
ZSHRC="$TORI_ZSH"

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

# Invariant 3: must try "aarch64-apple-darwin-fuse" for ostk downloads.
# v6.0.0 switched from darwin-universal to aarch64-apple-darwin-fuse (no v-prefix).
if ! grep -qF 'aarch64-apple-darwin-fuse' <<<"$tori_body"; then
  echo "FAIL: tori() does not try 'aarch64-apple-darwin-fuse' for ostk downloads." >&2
  echo "      ostk v6.0.0+ ships ostk-X.Y.Z-aarch64-apple-darwin-fuse.tar.gz." >&2
  echo "      The old darwin-universal pattern returns 404 on v6+ releases." >&2
  fail=1
fi

# Invariant 4: must NOT silence curl errors with lowercase -s in the upgrade block.
# -fsL silences curl error messages; -fSL shows them so users see why downloads fail.
if grep -qF 'curl -fsL' <<<"$tori_body"; then
  echo "FAIL: tori() uses 'curl -fsL' which silences error output." >&2
  echo "      Use 'curl -fSL' so download errors surface to the user." >&2
  fail=1
fi

# Invariant 5: ostk boot must run visibly (not silenced).
# The boot output is part of the startup experience.
if grep -q 'ostk boot.*/dev/null' <<<"$tori_body"; then
  echo "FAIL: tori() silences 'ostk boot' output. Boot status should" >&2
  echo "      be visible so the user sees kernel state at startup." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "PASS: tori() updater invariants hold (claude update, aarch64-apple-darwin-fuse, -fSL, visible boot)."
exit 0
