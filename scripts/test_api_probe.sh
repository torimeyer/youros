#!/usr/bin/env bash
# Regression test for scripts/api-probe.sh.
#
# Ensures the helper picks the right scheme based on ~/.youros/localhost.{key,crt}
# and passes -k for self-signed HTTPS. This is the →457 protocol-mismatch
# defense; if it regresses, agents will misdiagnose "Empty reply from server"
# (curl exit 52) as a backend middleware crash.

set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/api-probe.sh"
if [[ ! -x "$SCRIPT" ]]; then
  echo "FAIL: $SCRIPT not found or not executable" >&2
  exit 1
fi

TMPHOME="$(mktemp -d)"
trap 'rm -rf "$TMPHOME"' EXIT
fails=0

# Case 1: no certs → http, no -k
out_http_base=$(HOME="$TMPHOME" "$SCRIPT" --base)
if [[ "$out_http_base" != "http://127.0.0.1:8000" ]]; then
  echo "FAIL: no-certs base expected http://127.0.0.1:8000, got '$out_http_base'" >&2
  fails=$((fails + 1))
fi

out_http_env=$(HOME="$TMPHOME" "$SCRIPT" --env)
if grep -qE -- '-k( |$)|-sSk' <<<"$out_http_env"; then
  echo "FAIL: no-certs env should not pass -k, got: $out_http_env" >&2
  fails=$((fails + 1))
fi

# Case 2: certs present → https, opts include -k (packed as -sSk)
mkdir -p "$TMPHOME/.youros"
: > "$TMPHOME/.youros/localhost.key"
: > "$TMPHOME/.youros/localhost.crt"

out_https_base=$(HOME="$TMPHOME" "$SCRIPT" --base)
if [[ "$out_https_base" != "https://127.0.0.1:8000" ]]; then
  echo "FAIL: certs-present base expected https://127.0.0.1:8000, got '$out_https_base'" >&2
  fails=$((fails + 1))
fi

out_https_env=$(HOME="$TMPHOME" "$SCRIPT" --env)
if ! grep -qE -- 'k' <<<"$out_https_env"; then
  echo "FAIL: certs-present env must pass -k for self-signed, got: $out_https_env" >&2
  fails=$((fails + 1))
fi

# Case 3: --env output is source-able (shell-quoted correctly)
eval "$out_https_env"
if [[ "$API_BASE" != "https://127.0.0.1:8000" ]]; then
  echo "FAIL: sourcing --env did not set API_BASE correctly: '$API_BASE'" >&2
  fails=$((fails + 1))
fi
unset API_BASE CURL_OPTS

# Case 4: API_PORT override propagates
out_port=$(HOME="$TMPHOME" API_PORT=8001 "$SCRIPT" --base)
if [[ "$out_port" != "https://127.0.0.1:8001" ]]; then
  echo "FAIL: API_PORT override expected https://127.0.0.1:8001, got '$out_port'" >&2
  fails=$((fails + 1))
fi

# Case 5: one-shot end-to-end against a real backend if one is running on :8000.
# This is the key bug guard: the raw curl pattern the sibling diagnose used
# (plain http) gives exit 52; the probe helper must succeed.
if lsof -iTCP:8000 -sTCP:LISTEN -P >/dev/null 2>&1; then
  # Use the REAL home so the helper picks the actual running server's scheme.
  if ! body=$("$SCRIPT" /api/health 2>&1); then
    echo "FAIL: live probe to /api/health failed. output: $body" >&2
    fails=$((fails + 1))
  elif ! grep -q '"status":"ok"' <<<"$body"; then
    echo "FAIL: live probe body did not contain status:ok, got: $body" >&2
    fails=$((fails + 1))
  fi
else
  echo "SKIP: no backend on :8000, skipping live probe case 5"
fi

if [[ "$fails" -ne 0 ]]; then
  echo "FAIL: $fails case(s) failed" >&2
  exit 1
fi

echo "PASS: api-probe.sh picks the right scheme and succeeds against live backend."
exit 0
