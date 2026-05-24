#!/usr/bin/env bash
# liveness-check.sh — probe vite + uvicorn at every ostk boot / session start.
#
# Prints a green ✓ when both are up, or a red ✗ with the exact start command
# for anything that's down. Exits 0 always (advisory, not blocking).
#
# Integration surfaces:
#   1. Called from .claude/hooks/session-start.sh on each new Claude session.
#   2. Can be run standalone: scripts/liveness-check.sh
#
# Why we check URLs instead of ports (→check-servers.sh):
#   lsof gives false positives — a process making a request TO port 8000 shows
#   up in lsof -ti:8000 even though it's not the backend. Checking the URL is truth.
#
# Source retro F10, needle →1545: vite was down all day until 10:55 AM PT
# and nobody noticed because no check fires inside an active Claude session.

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

# Honour env overrides so tests can point at a stub server.
BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}/api/health"
FRONTEND_URL="${MYOS_FRONTEND_URL:-https://localhost:3010}/"

all_ok=true

# --- Backend probe ---
backend_code=$(curl -sSk --connect-timeout 2 -m 4 \
    -o /dev/null -w "%{http_code}" \
    "$BACKEND_URL" 2>/dev/null || echo "000")

if [ "$backend_code" = "200" ]; then
    backend_status="${GREEN}✓ backend  up${NC}"
else
    backend_status="${RED}✗ backend  DOWN (HTTP ${backend_code}) — start with: scripts/dev-backend.sh${NC}"
    all_ok=false
fi

# --- Frontend probe ---
# Accept 200 or any 3xx redirect (Vite may redirect / → /index.html).
frontend_code=$(curl -sSk --connect-timeout 2 -m 4 \
    -o /dev/null -w "%{http_code}" \
    "$FRONTEND_URL" 2>/dev/null || echo "000")

frontend_2xx_or_3xx=false
case "$frontend_code" in
    2[0-9][0-9]|3[0-9][0-9]) frontend_2xx_or_3xx=true ;;
esac

if $frontend_2xx_or_3xx; then
    frontend_status="${GREEN}✓ frontend up${NC}"
else
    frontend_status="${RED}✗ frontend DOWN (HTTP ${frontend_code}) — start with: scripts/dev-frontend.sh${NC}"
    all_ok=false
fi

# --- Output ---
if $all_ok; then
    printf "${GREEN}[liveness]${NC} backend ✓  frontend ✓\n"
else
    printf "${RED}[liveness]${NC} service(s) down:\n"
    printf "  %b\n" "$backend_status"
    printf "  %b\n" "$frontend_status"
fi

exit 0
