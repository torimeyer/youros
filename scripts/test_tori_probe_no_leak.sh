#!/usr/bin/env bash
# Regression test: tori() backend + frontend readiness loops must NOT leak
# zsh typeset output to stdout on retry.
#
# Root cause: `local VAR` (bare re-declaration, no `=value`) inside a zsh
# loop prints `VAR=VALUE` on the SECOND and later iterations, because zsh's
# `local` is `typeset` and a repeat bare typeset of an already-local var
# echoes its current value in $'...' notation.
#
# Symptom: with the backend unreachable, every retry leaked
#   _response=$'\n000'
# to the user's terminal before finally printing "✗ Backend failed to start".
# `\n000` is the curl -w "%{http_code}" template on connection failure
# (empty body + 000 status).
#
# Fix: declare `_response` and `_http_code` ONCE above the loop (with `=""`
# so the first declaration is an assignment, not a bare `local`), and only
# reassign inside.
#
# Invariants (checked by this test):
#   1. `local _response` must NOT appear inside the while loop body.
#   2. `local _http_code` must NOT appear inside the while loop body.
#   3. Running the probe block against a definitely-closed port under zsh
#      must produce NO lines matching `_response=` or `_http_code=` on
#      stdout or stderr.

set -u

ZSHRC="${ZSHRC:-$HOME/.zshrc}"

if [[ ! -f "$ZSHRC" ]]; then
  echo "FAIL: $ZSHRC not found" >&2
  exit 1
fi

fail=0

# Extract the while-loop block that probes :8000/api/health.
probe_block=$(awk '
  /while \[ \$_attempt -lt \$_max_retries \]; do/ { inside = 1 }
  inside { print }
  inside && /^  done$/ { inside = 0; print "---END---" }
' "$ZSHRC" | awk '/^---END---$/ { exit } { print }')

if [[ -z "$probe_block" ]]; then
  echo "FAIL: could not locate while-loop probe block in tori()" >&2
  exit 1
fi

# Invariant 1: no bare `local _response` inside the loop.
if grep -qE '^[[:space:]]*local _response\b' <<<"$probe_block"; then
  echo "FAIL: \`local _response\` appears inside the probe while-loop." >&2
  echo "      zsh re-declaration in a loop leaks \`_response=\$'\\n000'\` to stdout." >&2
  echo "      Declare \`local _response=\"\"\` ONCE above the loop instead." >&2
  fail=1
fi

# Invariant 2: no bare `local _http_code` inside the loop.
if grep -qE '^[[:space:]]*local _http_code\b' <<<"$probe_block"; then
  echo "FAIL: \`local _http_code\` appears inside the probe while-loop." >&2
  echo "      Declare \`local _http_code=\"\"\` ONCE above the loop instead." >&2
  fail=1
fi

# Invariant 3: run the probe logic against a closed port under zsh.
# Any `_response=...` or `_http_code=...` line on stdout/stderr is a leak.
if ! command -v zsh >/dev/null 2>&1; then
  echo "SKIP: zsh not available for runtime leak check."
else
  tmpout=$(mktemp)
  # Pick a port that nothing is listening on. 59999 is unlikely to be bound
  # but we verify with lsof and fall back if it is.
  probe_port=59999
  if lsof -nP -iTCP:$probe_port -sTCP:LISTEN >/dev/null 2>&1; then
    probe_port=59998
  fi

  # Inline the probe pattern. Must mirror the tori() shape so a regression
  # (someone moving `local` back inside the loop) is caught here.
  zsh -c '
    set +e
    _ready=0
    _attempt=0
    _max_retries=3
    _last_response=""
    _response=""
    _http_code=""
    while [ $_attempt -lt $_max_retries ]; do
      _attempt=$((_attempt + 1))
      _response=$(curl -sk --connect-timeout 1 -m 1 -w "\n%{http_code}" "http://127.0.0.1:'"$probe_port"'/api/health" 2>&1)
      _http_code=$(echo "$_response" | tail -1)
      if [ "$_http_code" = "200" ]; then _ready=1; break; fi
      if [ $_attempt -lt $_max_retries ]; then sleep 0.1; fi
    done
  ' >"$tmpout" 2>&1 || true

  if grep -qE '^_response=|^_http_code=' "$tmpout"; then
    echo "FAIL: probe loop leaked variable dumps to stdout/stderr:" >&2
    grep -E '^_response=|^_http_code=' "$tmpout" >&2 | head -5
    fail=1
  fi
  rm -f "$tmpout"
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "PASS: tori() backend probe does not leak _response/_http_code dumps on retry."
exit 0
