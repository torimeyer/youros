#!/usr/bin/env bash
# Regression test for the tori() shell function in ~/.zshrc.
#
# Enforces static invariants so known bugs cannot regress:
#   1. tori() MUST invoke "command claude update" (not npm).
#   2. tori() MUST NOT invoke "npm install -g @anthropic-ai/claude-code".
#   3. tori() MUST try "darwin-universal" for ostk downloads (v3.0.0+ naming).
#   4. tori() MUST NOT hardcode "aarch64-apple-darwin" as the only download path.
#   5. tori() MUST run "ostk boot" visibly (not redirected to /dev/null).

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

# Invariant 3: must try "darwin-universal" for ostk downloads.
# v3.0.0 switched from per-arch tarballs to a universal macOS binary.
if ! grep -qF 'darwin-universal' <<<"$tori_body"; then
  echo "FAIL: tori() does not try 'darwin-universal' for ostk downloads." >&2
  echo "      ostk v3.0.0+ ships a universal macOS binary. The old" >&2
  echo "      aarch64-apple-darwin naming returns 404 on new releases." >&2
  fail=1
fi

# Invariant 4: must NOT hardcode aarch64-apple-darwin as the ONLY download path.
# It is OK as a fallback, but "darwin-universal" must appear first.
if grep -qF 'local arch="aarch64-apple-darwin"' <<<"$tori_body"; then
  echo "FAIL: tori() hardcodes arch='aarch64-apple-darwin' as the sole" >&2
  echo "      download target. ostk v3.0.0+ uses darwin-universal." >&2
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

echo "PASS: tori() updater invariants hold (claude update, darwin-universal, visible boot)."
exit 0
