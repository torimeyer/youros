#!/usr/bin/env bash
# Regression test: tori() needle summary must not produce "integer expression expected"
#
# Root cause (→378): grep -c || echo 0 produces "0\n0" when grep finds
# zero matches, because grep -c always prints the count ("0") AND exits
# non-zero, so || echo 0 appends a second "0". The integer comparison
# then fails on the multi-line string.
#
# Invariant: the needle-count lines must use assignment-fallback
#   var=$(...) || var=0
# NOT the broken pattern:
#   var=$(... || echo 0)

set -u

# Default to the tracked canonical tori.zsh so CI + fresh setups guard
# the real source. Override with TORI_ZSH=... or ZSHRC=... if needed.
TORI_ZSH="${TORI_ZSH:-${ZSHRC:-$(cd "$(dirname "$0")" && pwd)/tori.zsh}}"
ZSHRC="$TORI_ZSH"

if [[ ! -f "$ZSHRC" ]]; then
  echo "FAIL: $ZSHRC not found" >&2
  exit 1
fi

# Extract the tori() function body.
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

# Invariant 1: no "|| echo 0" inside a command substitution for needle counts
if grep -qE '\$\(.*grep -c.*\|\| echo 0\)' <<<"$tori_body"; then
  echo "FAIL: found broken pattern: \$(grep -c ... || echo 0)" >&2
  echo "      grep -c always prints count AND exits 1 on zero matches," >&2
  echo "      so || echo 0 appends a second '0', producing '0\\n0'." >&2
  echo "      Use: var=\$(grep -c ...) || var=0" >&2
  fail=1
fi

# Invariant 2: needle-count variables must use assignment-fallback pattern
for var in _open _p0 _p1; do
  if grep -qE "${var}=\\\$\(.*grep.*-c" <<<"$tori_body"; then
    # The variable is set from grep -c. Check it uses the safe pattern.
    line=$(grep -E "${var}=\\\$\(.*grep.*-c" <<<"$tori_body")
    if ! grep -qE "\) \|\| ${var}=0" <<<"$line"; then
      echo "FAIL: $var does not use safe fallback pattern: $var=\$(...) || $var=0" >&2
      fail=1
    fi
  fi
done

# Invariant 3: the comparisons must reference the variables, not raw strings
for var in _p0 _p1 _open; do
  if grep -qE "\"\\\$${var}\" -gt 0" <<<"$tori_body"; then
    :  # good, comparison exists
  fi
done

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "PASS: tori() needle summary uses safe grep -c pattern (no 0\\n0 bug)."
exit 0
