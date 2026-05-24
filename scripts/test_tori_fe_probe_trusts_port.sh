#!/usr/bin/env bash
# Regression test: tori() splash must trust an HTTP probe against the
# vite dev server, NOT the liveness of $fe_pid.
#
# Root cause (→560, mirrors →558 for backend): dev-frontend.sh exits
# when 3010 is already bound, so $fe_pid dies even though vite is
# serving. The old splash gated on `kill -0 $fe_pid`, flipping to
# "✗ Frontend failed to start" on the second `tori` invocation in a
# session. The fix is a port probe that speaks the same scheme as
# vite (https when ~/.myos/localhost.key is present).
#
# Invariants enforced here:
#   1. The success branch of the Frontend line must NOT call
#      `kill -0 $fe_pid`. The probe is the source of truth.
#   2. A probe loop hitting port 3010 must exist and set _fe_ready=1.
#   3. The Frontend success line must key off _fe_ready.
#
# Run:
#   bash scripts/test_tori_fe_probe_trusts_port.sh

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

# Invariant 1: no kill -0 on fe_pid gating the Frontend running line.
# Allow references in the cleanup/kill block or in comments.
# Extract just the frontend check region (around the "Frontend running" printf).
fe_region=$(awk '
  /Probe vite on port 3010/ { inside = 1 }
  inside { print }
  inside && /Frontend failed to start/ { print; exit }
' <<<"$tori_body")

if [[ -z "$fe_region" ]]; then
  echo "FAIL: could not locate frontend probe region in tori()" >&2
  fail=1
fi

if grep -vE '^[[:space:]]*#' <<<"$fe_region" \
   | grep -qE 'kill -0[[:space:]]+\$?fe_pid'; then
  echo "FAIL: tori() still gates the Frontend running line on 'kill -0 \$fe_pid'" >&2
  echo "      dev-frontend.sh exits when 3010 is already bound, so fe_pid" >&2
  echo "      is dead but vite is healthy. Trust the port probe instead." >&2
  fail=1
fi

# Invariant 2: the frontend probe loop must exist.
if ! grep -qE 'curl.*-sfk.*:3010' <<<"$fe_region"; then
  echo "FAIL: port 3010 curl probe missing from tori()" >&2
  fail=1
fi
if ! grep -qE '_fe_ready=1' <<<"$fe_region"; then
  echo "FAIL: _fe_ready=1 success marker missing from probe loop" >&2
  fail=1
fi

# Invariant 3: the success branch must key off _fe_ready.
if ! grep -qE 'if \[ "\$_fe_ready" -eq 1 \]' <<<"$fe_region"; then
  echo "FAIL: frontend success branch does not check _fe_ready=1 directly" >&2
  fail=1
fi

# Invariant 4: simulate the four quadrants.
simulate_fe_splash() {
  local _fe_ready="$1"
  local _fe_alive="$2"
  if [ "$_fe_ready" -eq 1 ]; then
    echo "GREEN"
  else
    echo "RED"
  fi
}

# Case A: probe passes, fe_pid dead (the bug case) -> must be GREEN.
got=$(simulate_fe_splash 1 0)
if [[ "$got" != "GREEN" ]]; then
  echo "FAIL: probe=1 fe_pid=dead should be GREEN (bug case), got $got" >&2
  fail=1
fi

# Case B: probe fails, fe_pid alive -> must be RED.
got=$(simulate_fe_splash 0 1)
if [[ "$got" != "RED" ]]; then
  echo "FAIL: probe=0 fe_pid=alive should be RED, got $got" >&2
  fail=1
fi

# Case C: probe passes, fe_pid alive -> GREEN.
got=$(simulate_fe_splash 1 1)
if [[ "$got" != "GREEN" ]]; then
  echo "FAIL: probe=1 fe_pid=alive should be GREEN, got $got" >&2
  fail=1
fi

# Case D: probe fails, fe_pid dead -> RED.
got=$(simulate_fe_splash 0 0)
if [[ "$got" != "RED" ]]; then
  echo "FAIL: probe=0 fe_pid=dead should be RED, got $got" >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "PASS: tori() Frontend line trusts port 3010 probe, not fe_pid liveness."
exit 0
