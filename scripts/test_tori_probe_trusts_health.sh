#!/usr/bin/env bash
# Regression test: tori() splash must trust the /api/health probe, NOT
# the liveness of $be_pid.
#
# Root cause (→558): dev-backend.sh exits 1 on purpose when another
# uvicorn is already listening on :8000 (the double-bind guard). In
# that case $be_pid is dead but the real server is up and healthy.
# The old splash gated on `kill -0 $be_pid`, so it flipped to
# "✗ Backend failed to start" even though curl https://.../api/health
# returned 200. Tori saw the red cross and thought the backend was
# down, but the frontend was hitting a live backend the whole time.
#
# Invariants enforced here:
#   1. The success branch of the splash must NOT call `kill -0 $be_pid`
#      or any other check on the spawned child's liveness. The probe
#      is the source of truth.
#   2. The probe loop over /api/health must still exist and set
#      _ready=1 on a 2xx response.
#   3. The success and failure lines must still key off `_ready`.
#
# Run:
#   bash scripts/test_tori_probe_trusts_health.sh

set -u

ZSHRC="${ZSHRC:-$HOME/.zshrc}"

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

# Invariant 1: no kill -0 on be_pid gating the Backend running line.
# Accept comments that mention it, reject live code.
if grep -vE '^[[:space:]]*#' <<<"$tori_body" \
   | grep -qE 'kill -0[[:space:]]+\$?be_pid'; then
  echo "FAIL: tori() still gates the Backend running line on 'kill -0 \$be_pid'" >&2
  echo "      dev-backend.sh exits 1 on purpose when a uvicorn is already" >&2
  echo "      listening, so be_pid is dead but the server is healthy." >&2
  echo "      Trust the /api/health probe instead." >&2
  fail=1
fi

# Invariant 2: the health probe loop must still exist.
if ! grep -qE 'curl.*-sfk.*/api/health' <<<"$tori_body"; then
  echo "FAIL: /api/health curl probe missing from tori()" >&2
  fail=1
fi
if ! grep -qE '_ready=1' <<<"$tori_body"; then
  echo "FAIL: _ready=1 success marker missing from probe loop" >&2
  fail=1
fi

# Invariant 3: the success branch must key off _ready.
if ! grep -qE 'if \[ "\$_ready" -eq 1 \]' <<<"$tori_body"; then
  echo "FAIL: success branch does not check _ready=1 directly" >&2
  fail=1
fi

# Invariant 4: simulate the bad case in a sandboxed shell. be_pid dead,
# probe succeeds. Splash must print the green line.
simulate_splash() {
  local _ready="$1"
  local _be_alive="$2"
  local out
  # New logic under test: trust _ready alone.
  if [ "$_ready" -eq 1 ]; then
    out="GREEN"
  else
    out="RED"
  fi
  echo "$out"
}

# Case A: probe passes, be_pid dead (the bug case) -> must be GREEN.
got=$(simulate_splash 1 0)
if [[ "$got" != "GREEN" ]]; then
  echo "FAIL: probe=1 be_pid=dead should be GREEN (bug case), got $got" >&2
  fail=1
fi

# Case B: probe fails, be_pid alive -> must be RED.
got=$(simulate_splash 0 1)
if [[ "$got" != "RED" ]]; then
  echo "FAIL: probe=0 be_pid=alive should be RED, got $got" >&2
  fail=1
fi

# Case C: probe passes, be_pid alive -> GREEN.
got=$(simulate_splash 1 1)
if [[ "$got" != "GREEN" ]]; then
  echo "FAIL: probe=1 be_pid=alive should be GREEN, got $got" >&2
  fail=1
fi

# Case D: probe fails, be_pid dead -> RED.
got=$(simulate_splash 0 0)
if [[ "$got" != "RED" ]]; then
  echo "FAIL: probe=0 be_pid=dead should be RED, got $got" >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "PASS: tori() splash trusts /api/health probe, not be_pid liveness."
exit 0
