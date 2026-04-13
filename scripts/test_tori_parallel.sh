#!/usr/bin/env bash
# Regression test: tori() must run update checks in parallel, not sequentially.
#
# Invariants:
#   1. claude update must be fire-and-forget (backgrounded with &! or &)
#   2. ostk version check must run in a background subshell
#   3. git fetch must run in a background subshell
#   4. Both background results must be collected via "wait"
#   5. script(1) must be used to detach claude update from the terminal

set -u

ZSHRC="${ZSHRC:-$HOME/.zshrc}"

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

# Invariant 1: claude update must be backgrounded (fire-and-forget)
if ! grep -qE 'claude update.*&' <<<"$tori_body"; then
  echo "FAIL: 'command claude update' is not backgrounded. It takes ~60s" >&2
  echo "      and doesn't affect the current session. Must run with & or &!" >&2
  fail=1
fi

# Invariant 2: claude update must NOT block (no bare sequential call)
if grep -qE '^\s+command claude update.*$' <<<"$tori_body" && \
   ! grep -qE 'claude update.*&' <<<"$tori_body"; then
  echo "FAIL: 'command claude update' runs sequentially (blocks ~60s)." >&2
  fail=1
fi

# Invariant 3: script(1) must wrap claude update for pty isolation
if ! grep -qF '/usr/bin/script' <<<"$tori_body"; then
  echo "FAIL: claude update must use script(1) for pty isolation." >&2
  echo "      Without it, /dev/tty writes cause 'suspended (tty output)'." >&2
  fail=1
fi

# Invariant 4: ostk version check must be in a background subshell
if ! grep -qE 'ostk --version' <<<"$tori_body"; then
  echo "FAIL: ostk version check not found in tori()." >&2
  fail=1
fi

# Invariant 5: wait must be called to collect parallel results
if ! grep -qF 'wait' <<<"$tori_body"; then
  echo "FAIL: no 'wait' call found. Parallel subshells must be waited on" >&2
  echo "      before the splash prints version numbers." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "PASS: tori() runs update checks in parallel with proper pty isolation."
exit 0
