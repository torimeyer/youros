#!/usr/bin/env bash
# Regression test: vite dev server must stay responsive under concurrent load
# and after an idle period.
#
# Needle 1145 root cause: optimizeDeps.holdUntilCrawlEnd:true (vite default)
# defers dep-optimization results until the 50ms crawl-end idle timer fires.
# A burst of simultaneous module requests (browser reconnecting after idle, or
# concurrent agent activity) keeps resetting that timer indefinitely.
# depOptimizationProcessing.promise never resolves and every request for a
# pre-bundled dep hangs forever. Fix: holdUntilCrawlEnd:false.
#
# This test covers both failure modes:
#   Phase 1 — concurrent burst: fire 10 requests in parallel immediately
#             after startup to trigger the crawl-end stall.
#   Phase 2 — post-idle:       idle 30s, then burst again to verify the
#             server is still alive (original IPv4-binding scenario).
#
# Usage: ./scripts/test-vite-no-wedge.sh
# Exit 0 = pass, exit 1 = fail.

set -euo pipefail

IDLE_SECONDS=30
BURST_COUNT=10
# Use a test-only port so the regression test doesn't conflict with a running
# dev server.
PORT=3013
HOST=127.0.0.1
PASS=0
FAIL=0
VITE_PID=""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_DIR="$REPO_DIR/app"
VITE_BIN="$APP_DIR/node_modules/.bin/vite"
if [[ ! -x "$VITE_BIN" ]]; then
  # Worktrees share the git tree but not node_modules; fall back to main repo.
  GIT_COMMON="$(git -C "$REPO_DIR" rev-parse --git-common-dir 2>/dev/null)"
  if [[ -n "$GIT_COMMON" ]]; then
    MAIN_REPO="$(cd "$GIT_COMMON/.." && pwd)"
    VITE_BIN="$MAIN_REPO/app/node_modules/.bin/vite"
  fi
fi

cleanup() {
  if [[ -n "$VITE_PID" ]]; then
    kill "$VITE_PID" 2>/dev/null || true
    wait "$VITE_PID" 2>/dev/null || true
    # Free the port in case something lingered
    stale=$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null || true)
    [[ -n "$stale" ]] && kill -9 $stale 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [[ ! -x "$VITE_BIN" ]]; then
  echo "[vite-wedge-test] SKIP: vite binary not found at $VITE_BIN (run npm install in $APP_DIR)"
  exit 0
fi

# Kill any existing listener on the port before starting
stale=$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null || true)
if [[ -n "$stale" ]]; then
  echo "[vite-wedge-test] Freeing port $PORT from stale listener(s): $stale"
  kill $stale 2>/dev/null || true
  sleep 1
fi

# When running from a worktree, vite must be invoked from the main repo's
# app/ directory (where node_modules live) but pointed at the worktree's
# vite.config.ts so we test the patched version.
VITE_CONFIG="$APP_DIR/vite.config.ts"
GIT_COMMON_DIR="$(git -C "$REPO_DIR" rev-parse --git-common-dir 2>/dev/null)"
if [[ -n "$GIT_COMMON_DIR" ]]; then
  MAIN_APP_DIR="$(cd "$GIT_COMMON_DIR/.." && pwd)/app"
else
  MAIN_APP_DIR="$APP_DIR"
fi

echo "[vite-wedge-test] Starting vite dev server on ${HOST}:${PORT}..."
echo "[vite-wedge-test] Config: $VITE_CONFIG"
cd "$MAIN_APP_DIR"
# --config points at the worktree's (patched) config.
# --port overrides so the test doesn't conflict with the live dev server on 3010.
node "$VITE_BIN" --config "$VITE_CONFIG" --port "${PORT}" &>/tmp/vite-wedge-test.log &
VITE_PID=$!

# Detect HTTPS vs HTTP based on cert presence
MYOS_DIR="$HOME/.myos"
if [[ -f "$MYOS_DIR/localhost.key" && -f "$MYOS_DIR/localhost.crt" ]]; then
  SCHEME=https
else
  SCHEME=http
fi

# Wait for vite to be ready (up to 30s)
READY=0
for i in $(seq 1 30); do
  HTTP=$(curl --connect-timeout 2 -m 3 -sk "${SCHEME}://${HOST}:${PORT}/" -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
  if echo "$HTTP" | grep -qE "^[23]"; then
    READY=1
    break
  fi
  sleep 1
done

if [[ $READY -eq 0 ]]; then
  echo "[vite-wedge-test] FAIL: vite did not become ready within 30s (scheme=${SCHEME})"
  echo "[vite-wedge-test] vite log tail:"
  tail -20 /tmp/vite-wedge-test.log | sed 's/\x1b\[[0-9;]*m//g' || true
  exit 1
fi
echo "[vite-wedge-test] vite ready (scheme=${SCHEME})."

# ---------- Phase 1: concurrent burst immediately after startup ----------
# This is the holdUntilCrawlEnd test. Each curl fires in the background so
# all N requests are in-flight simultaneously, mimicking a browser that
# opens many connections at once and keeps resetting the 50ms crawl-end
# idle timer. With holdUntilCrawlEnd:true these would all hang; with
# holdUntilCrawlEnd:false they resolve immediately.
echo "[vite-wedge-test] Phase 1: firing ${BURST_COUNT} concurrent requests..."
declare -a PIDS
declare -a TMPFILES
for i in $(seq 1 "$BURST_COUNT"); do
  TMPFILE=$(mktemp /tmp/vite-wedge-burst-XXXXXX)
  TMPFILES+=("$TMPFILE")
  curl --connect-timeout 3 -m 8 -sk "${SCHEME}://${HOST}:${PORT}/" \
    -o /dev/null -w "%{http_code}" >"$TMPFILE" 2>/dev/null &
  PIDS+=($!)
done

# Collect results
phase1_fail=0
for i in "${!PIDS[@]}"; do
  wait "${PIDS[$i]}" 2>/dev/null || true
  HTTP=$(cat "${TMPFILES[$i]}" 2>/dev/null || echo "000")
  rm -f "${TMPFILES[$i]}"
  req=$((i + 1))
  if echo "$HTTP" | grep -qE "^[23]"; then
    echo "[vite-wedge-test]   burst $req: $HTTP OK"
    PASS=$((PASS + 1))
  else
    echo "[vite-wedge-test]   burst $req: $HTTP FAIL (holdUntilCrawlEnd wedge?)"
    FAIL=$((FAIL + 1))
    phase1_fail=$((phase1_fail + 1))
  fi
done

if [[ $phase1_fail -gt 0 ]]; then
  echo "[vite-wedge-test] Phase 1 FAIL: ${phase1_fail}/${BURST_COUNT} concurrent requests timed out."
  echo "[vite-wedge-test] This indicates the holdUntilCrawlEnd:false fix is missing."
  echo "[vite-wedge-test] vite log tail:"
  tail -20 /tmp/vite-wedge-test.log | sed 's/\x1b\[[0-9;]*m//g' || true
  exit 1
fi
echo "[vite-wedge-test] Phase 1 PASS: all ${BURST_COUNT} concurrent requests returned 2xx/3xx."

# ---------- Phase 2: post-idle sequential check ----------
echo "[vite-wedge-test] Phase 2: idling ${IDLE_SECONDS}s then verifying server is still alive..."
sleep "${IDLE_SECONDS}"

phase2_fail=0
for i in $(seq 1 5); do
  HTTP=$(curl --connect-timeout 3 -m 5 -sk "${SCHEME}://${HOST}:${PORT}/" \
    -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
  if echo "$HTTP" | grep -qE "^[23]"; then
    echo "[vite-wedge-test]   post-idle hit $i: $HTTP OK"
    PASS=$((PASS + 1))
  else
    echo "[vite-wedge-test]   post-idle hit $i: $HTTP FAIL"
    FAIL=$((FAIL + 1))
    phase2_fail=$((phase2_fail + 1))
  fi
done

echo "[vite-wedge-test] Results: ${PASS} passed, ${FAIL} failed"
if [[ $FAIL -gt 0 ]]; then
  echo "[vite-wedge-test] FAIL"
  exit 1
fi
echo "[vite-wedge-test] PASS"
exit 0
