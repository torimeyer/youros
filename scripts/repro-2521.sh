#!/usr/bin/env bash
# →2521 Reproduction: vite frontend freeze after backend restart.
#
# Launches vite on :3999 (proxy target :8999) + stub backend on :8999.
# Fires CONCURRENT_REQUESTS concurrent slow proxy requests, then kills
# the stub while they are in-flight.  Immediately polls GET / on vite —
# vite must stay responsive even though the backend is down.
#
# Exit 0 = PASS (vite stayed responsive)
# Exit 1 = FAIL (vite wedged — GET / timed out)

set -euo pipefail

VITE_TEST_PORT=3999
STUB_PORT=8999
HOST=127.0.0.1
SCHEME=https

# Number of concurrent requests held in-flight when we kill the stub
CONCURRENT_REQUESTS=30
# Stub holds each response this long so requests are in-flight at kill time
STUB_DELAY_MS=6000
# How many GET / polls to make after the kill (0.25s apart)
POLL_COUNT=20

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_DIR="$REPO_DIR/app"

# Locate vite binary — handles worktrees where node_modules are in main repo
VITE_BIN="$APP_DIR/node_modules/.bin/vite"
MAIN_APP_DIR="$APP_DIR"
if [[ ! -x "$VITE_BIN" ]]; then
  GIT_COMMON="$(git -C "$REPO_DIR" rev-parse --git-common-dir 2>/dev/null || true)"
  if [[ -n "$GIT_COMMON" ]]; then
    MAIN_REPO="$(cd "$GIT_COMMON/.." && pwd)"
    VITE_BIN="$MAIN_REPO/app/node_modules/.bin/vite"
    MAIN_APP_DIR="$MAIN_REPO/app"
  fi
fi

if [[ ! -x "$VITE_BIN" ]]; then
  echo "[repro-2521] SKIP: vite binary not found (run npm install in $APP_DIR)"
  exit 0
fi

STUB_PID=""
VITE_PID=""
VITE_REPRO_CONFIG=""
PASS=0
FAIL=0

cleanup() {
  [[ -n "$VITE_PID" ]]   && kill "$VITE_PID"   2>/dev/null || true
  [[ -n "$STUB_PID" ]]   && kill "$STUB_PID"   2>/dev/null || true
  [[ -n "$VITE_REPRO_CONFIG" ]] && rm -f "$VITE_REPRO_CONFIG"
  for PORT in $VITE_TEST_PORT $STUB_PORT; do
    stale=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
    [[ -n "$stale" ]] && kill -9 $stale 2>/dev/null || true
  done
}
trap cleanup EXIT

# --- Write temp vite config ---
VITE_REPRO_CONFIG="$(mktemp /tmp/vite-repro-2521-XXXXXX.mjs)"

# IMPORTANT: this config mirrors app/vite.config.ts exactly for the proxy
# section (same backendAgent options) but points at the stub backend on
# :8999 and listens on :3999 so it doesn't collide with the live dev server.
cat > "$VITE_REPRO_CONFIG" << 'VITEEOF'
import https from 'node:https';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const myosDir = path.join(os.homedir(), '.youros');
const keyPath = path.join(myosDir, 'localhost.key');
const certPath = path.join(myosDir, 'localhost.crt');
const httpsConfig = fs.existsSync(keyPath) && fs.existsSync(certPath)
  ? { key: fs.readFileSync(keyPath), cert: fs.readFileSync(certPath) }
  : undefined;

// Mirror of backendAgent from app/vite.config.ts (→1431 fix)
const backendAgent = new https.Agent({
  keepAlive: true,
  keepAliveMsecs: 5_000,
  maxSockets: 10,
  rejectUnauthorized: false,
});

export default {
  server: {
    port: 3999,
    host: '127.0.0.1',
    https: httpsConfig,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'https://127.0.0.1:8999',
        secure: false,
        changeOrigin: true,
        proxyTimeout: 5000,
        ws: true,
        agent: backendAgent,
        configure: (proxy) => {
          proxy.on('error', (err, _req, res) => {
            // Mirror of the production error handler
            console.error('[vite proxy /api] error:', err?.message ?? String(err));
            try {
              if (res && 'writeHead' in res && !res.headersSent) {
                res.writeHead(502, { 'Content-Type': 'text/plain' });
                res.end('Upstream unavailable');
              }
            } catch {
              // response already torn down
            }
          });
        },
      },
    },
  },
};
VITEEOF

# --- Free test ports if anything is lingering ---
for PORT in $VITE_TEST_PORT $STUB_PORT; do
  stale=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$stale" ]]; then
    echo "[repro-2521] Freeing port $PORT from stale listener: $stale"
    kill $stale 2>/dev/null || true; sleep 0.5
  fi
done

# --- Start stub backend ---
echo "[repro-2521] Starting stub backend on :$STUB_PORT (response delay=${STUB_DELAY_MS}ms)..."
node "$SCRIPT_DIR/repro-2521-stub.mjs" "$STUB_PORT" "$STUB_DELAY_MS" \
  >/tmp/repro-2521-stub.log 2>&1 &
STUB_PID=$!

STUB_READY=0
for i in $(seq 1 15); do
  RC=$(curl --connect-timeout 1 -m 2 -sk \
    "${SCHEME}://${HOST}:${STUB_PORT}/health" \
    -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
  if [[ "$RC" == "200" ]]; then STUB_READY=1; break; fi
  sleep 0.3
done
if [[ $STUB_READY -eq 0 ]]; then
  echo "[repro-2521] FAIL: stub backend did not start"
  cat /tmp/repro-2521-stub.log
  exit 1
fi
echo "[repro-2521] Stub backend ready."

# --- Start vite ---
echo "[repro-2521] Starting vite on :$VITE_TEST_PORT..."
cd "$MAIN_APP_DIR"
node "$VITE_BIN" --config "$VITE_REPRO_CONFIG" \
  >/tmp/repro-2521-vite.log 2>&1 &
VITE_PID=$!
cd "$REPO_DIR"

VITE_READY=0
for i in $(seq 1 40); do
  RC=$(curl --connect-timeout 2 -m 4 -sk \
    "${SCHEME}://${HOST}:${VITE_TEST_PORT}/" \
    -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
  if [[ "$RC" =~ ^[23] ]]; then VITE_READY=1; break; fi
  sleep 1
done
if [[ $VITE_READY -eq 0 ]]; then
  echo "[repro-2521] FAIL: vite did not start within 40s"
  tail -20 /tmp/repro-2521-vite.log | sed 's/\x1b\[[0-9;]*m//g'
  exit 1
fi
echo "[repro-2521] Vite ready."

# --- Phase 1: warm-up GET / to confirm baseline ---
echo "[repro-2521] Phase 1: baseline GET / check..."
RC=$(curl --connect-timeout 2 -m 3 -sk \
  "${SCHEME}://${HOST}:${VITE_TEST_PORT}/" \
  -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
if [[ "$RC" =~ ^[23] ]]; then
  echo "[repro-2521]   baseline: $RC OK"
else
  echo "[repro-2521]   baseline: $RC FAIL (vite not serving static files?)"
  exit 1
fi

# --- Phase 2: fire CONCURRENT_REQUESTS slow proxy requests in background ---
echo "[repro-2521] Phase 2: firing $CONCURRENT_REQUESTS concurrent /api requests (will stay in-flight for ${STUB_DELAY_MS}ms)..."
declare -a CURL_PIDS=()
for i in $(seq 1 $CONCURRENT_REQUESTS); do
  curl --connect-timeout 3 -m 9 -sk \
    "${SCHEME}://${HOST}:${VITE_TEST_PORT}/api/test?req=$i" \
    -o /dev/null >/dev/null 2>&1 &
  CURL_PIDS+=($!)
done

# Let connections establish against the stub before killing it
sleep 0.5

# --- Phase 3: kill stub backend (simulates launchctl kickstart) ---
echo "[repro-2521] Phase 3: killing stub backend (launchctl restart simulation)..."
kill "$STUB_PID" 2>/dev/null || true
STUB_PID=""

# --- Phase 4: immediately poll GET / on vite ---
# If vite's event loop is blocked by the proxy error cascade, these will time out.
echo "[repro-2521] Phase 4: polling GET / for ${POLL_COUNT} iterations (backend is DOWN)..."
wedge_count=0
ok_count=0
for i in $(seq 1 $POLL_COUNT); do
  T_START=$(python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0)
  RC=$(curl --connect-timeout 2 -m 3 -sk \
    "${SCHEME}://${HOST}:${VITE_TEST_PORT}/" \
    -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
  T_END=$(python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || echo 0)
  MS=$(( T_END - T_START ))
  if [[ "$RC" =~ ^[23] ]]; then
    ok_count=$((ok_count + 1))
    echo "[repro-2521]   poll $i: $RC OK (${MS}ms)"
    PASS=$((PASS + 1))
  else
    wedge_count=$((wedge_count + 1))
    echo "[repro-2521]   poll $i: $RC WEDGED <-- exit 28 = event-loop blocked (${MS}ms)"
    FAIL=$((FAIL + 1))
  fi
  sleep 0.25
done

# Clean up background curl jobs
for pid in "${CURL_PIDS[@]}"; do
  kill "$pid" 2>/dev/null || true
done

echo ""
echo "[repro-2521] Results: ${PASS} OK, ${FAIL} WEDGED"
if [[ $FAIL -gt 0 ]]; then
  echo "[repro-2521] FAIL: vite event loop was blocked $FAIL time(s)"
  echo "[repro-2521] Root cause reproduced — vite.config.ts fix needed."
  echo "[repro-2521] Vite log tail:"
  tail -30 /tmp/repro-2521-vite.log | sed 's/\x1b\[[0-9;]*m//g' || true
  exit 1
fi
echo "[repro-2521] PASS: vite stayed responsive throughout backend restart simulation."
exit 0
