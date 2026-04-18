#!/usr/bin/env bash
# myOS health check: pings backend (port 8000) and frontend (port 3010).
#
# Prints green OK or red FAIL for each service. Exits 0 only if both are up.
#
# Usage:
#   scripts/health.sh          # read-only status check (default)
#   scripts/health.sh --fix    # auto-restart any down service, then re-check
#   API_PORT=8001 FRONTEND_PORT=3011 scripts/health.sh   # override ports
#
# This is a fast check, not a full smoke test. Run scripts/e2e_smoke.sh
# before a release. Run this anytime you want a quick status snapshot.

set -euo pipefail

FIX=0
for arg in "$@"; do
    case "$arg" in
        --fix) FIX=1 ;;
    esac
done

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3010}"

# Auto-detect HTTPS for backend (same logic as e2e_smoke.sh).
# Callers can override API_BASE, FRONTEND_BASE, and CURL_OPTS directly
# (useful for tests that start plain-HTTP servers on non-standard ports).
SSL_KEY="$HOME/.myos/localhost.key"
SSL_CERT="$HOME/.myos/localhost.crt"
if [ -f "$SSL_KEY" ] && [ -f "$SSL_CERT" ]; then
    API_BASE="${API_BASE:-https://127.0.0.1:${API_PORT}}"
    CURL_OPTS="${CURL_OPTS:--sSk}"
    FRONTEND_BASE="${FRONTEND_BASE:-https://localhost:${FRONTEND_PORT}}"
else
    API_BASE="${API_BASE:-http://127.0.0.1:${API_PORT}}"
    CURL_OPTS="${CURL_OPTS:--sS}"
    FRONTEND_BASE="${FRONTEND_BASE:-http://localhost:${FRONTEND_PORT}}"
fi

ok=0

# --- Backend ---
backend_code=$(curl --connect-timeout 3 -m 5 $CURL_OPTS -o /dev/null -w "%{http_code}" \
    "${API_BASE}/api/health" 2>/dev/null || echo "000")
if [ "$backend_code" = "200" ]; then
    echo -e "${GREEN}[OK]${NC}   Backend  ${API_BASE}/api/health -> HTTP $backend_code"
else
    echo -e "${RED}[FAIL]${NC} Backend  ${API_BASE}/api/health -> HTTP $backend_code (not running or unreachable)"
    ok=1
fi

# --- Frontend ---
frontend_code=$(curl --connect-timeout 3 -m 5 $CURL_OPTS -o /dev/null -w "%{http_code}" \
    "${FRONTEND_BASE}/" 2>/dev/null || echo "000")
if [ "$frontend_code" = "200" ]; then
    echo -e "${GREEN}[OK]${NC}   Frontend ${FRONTEND_BASE}/ -> HTTP $frontend_code"
else
    echo -e "${RED}[FAIL]${NC} Frontend ${FRONTEND_BASE}/ -> HTTP $frontend_code (not running)"
    if [ "$FIX" = "1" ]; then
        echo "  --fix: launching watch-frontend.sh in background..."
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        nohup "$SCRIPT_DIR/watch-frontend.sh" >> /tmp/torios-frontend.log 2>&1 &
        echo "  Waiting up to 15 seconds for frontend to come up..."
        for i in $(seq 1 15); do
            sleep 1
            recheck=$(curl --connect-timeout 2 -m 3 $CURL_OPTS -o /dev/null -w "%{http_code}" \
                "${FRONTEND_BASE}/" 2>/dev/null || echo "000")
            if [ "$recheck" = "200" ]; then
                echo -e "  ${GREEN}[OK]${NC}   Frontend is up (took ${i}s)"
                ok=0
                break
            fi
        done
        if [ "$ok" != "0" ]; then
            echo -e "  ${RED}[FAIL]${NC} Frontend still down after 15s. Check /tmp/torios-frontend.log"
        fi
    else
        echo "         Restart with: nohup scripts/watch-frontend.sh > /tmp/torios-frontend.log 2>&1 &"
        echo "         Or auto-fix:  scripts/health.sh --fix"
        ok=1
    fi
fi

# --- Process check (informational, never blocks exit) ---
be_pid=$(lsof -tiTCP:${API_PORT} -sTCP:LISTEN 2>/dev/null | head -1 || true)
fe_pid=$(lsof -tiTCP:${FRONTEND_PORT} -sTCP:LISTEN 2>/dev/null | head -1 || true)
echo ""
echo "  Backend  PID: ${be_pid:-none}"
echo "  Frontend PID: ${fe_pid:-none}"

exit $ok
