#!/usr/bin/env bash
# Regression test: tori() backend readiness probe must match the server scheme.
#
# Root cause (→457): tori() probed http://127.0.0.1:8000/api/health, but
# dev-backend.sh serves HTTPS whenever ~/.myos/localhost.{key,crt} exist.
# curl returned rc=52 (empty reply / speaking HTTP to HTTPS) so tori
# falsely reported "✗ Backend failed to start" even though uvicorn was
# healthy. See feedback_readiness_probe.md.
#
# Invariants:
#   1. tori() must pick the probe scheme from the SSL cert files,
#      not hard-code http.
#   2. curl must pass -k (insecure) so self-signed cert is accepted.
#   3. The probe URL must still hit /api/health on port 8000.

set -u

# Default to the tracked canonical tori.zsh so CI + fresh setups guard
# the real source. Override with TORI_ZSH=... or ZSHRC=... if needed.
TORI_ZSH="${TORI_ZSH:-${ZSHRC:-$(cd "$(dirname "$0")" && pwd)/tori.zsh}}"
ZSHRC="$TORI_ZSH"

if [[ ! -f "$ZSHRC" ]]; then
  echo "FAIL: $ZSHRC not found" >&2
  exit 1
fi

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

# Invariant 1: probe URL is NOT a hard-coded http://
if grep -qE 'curl[^|]*http://127\.0\.0\.1:8000/api/health' <<<"$tori_body"; then
  echo "FAIL: tori() probes hard-coded http://127.0.0.1:8000/api/health" >&2
  echo "      dev-backend.sh serves https when SSL certs exist." >&2
  echo "      Use a scheme var derived from ~/.myos/localhost.{key,crt}." >&2
  fail=1
fi

# Invariant 2: the probe must branch on localhost.key + localhost.crt
if ! grep -qE 'localhost\.key' <<<"$tori_body" \
   || ! grep -qE 'localhost\.crt' <<<"$tori_body"; then
  echo "FAIL: tori() does not check for ~/.myos/localhost.{key,crt} before probing" >&2
  fail=1
fi

# Invariant 3: the readiness curl must use -k so self-signed cert is accepted
readiness_line=$(grep -E 'curl.*127\.0\.0\.1:8000/api/health' <<<"$tori_body" | head -1)
if [[ -z "$readiness_line" ]]; then
  echo "FAIL: readiness curl for /api/health not found in tori()" >&2
  fail=1
elif ! grep -qE '\-s[a-z]*k|\-k' <<<"$readiness_line"; then
  echo "FAIL: readiness curl does not pass -k (required for self-signed HTTPS cert)" >&2
  echo "      line: $readiness_line" >&2
  fail=1
fi

# Invariant 4: port and path are still 8000 and /api/health
if ! grep -qE '127\.0\.0\.1:8000/api/health' <<<"$tori_body"; then
  echo "FAIL: readiness probe URL changed away from 127.0.0.1:8000/api/health" >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "PASS: tori() backend probe matches server scheme (https when certs exist, http otherwise)."
exit 0
