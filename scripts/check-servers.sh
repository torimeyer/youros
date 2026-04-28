#!/usr/bin/env bash
# Probe the real torios URLs instead of checking port occupancy.
#
# Why not lsof? lsof -ti:PORT returns any process touching that port —
# listeners, clients, TIME_WAIT sockets. A process making a request TO the
# backend shows up in lsof -ti:8000 even though it is not the backend. And
# port 5173 is Vite's default fallback, not the torios frontend (which runs
# on 3010). Checking ports gives false positives; checking URLs gives truth.

set -euo pipefail

BACKEND_URL="https://127.0.0.1:8000/api/health"
FRONTEND_URL="https://localhost:3010"
ok=true

http_status=$(curl -sk --connect-timeout 2 -m 3 -o /dev/null -w "%{http_code}" "$BACKEND_URL" 2>/dev/null || echo "000")
if [ "$http_status" != "200" ]; then
    echo "backend down (got HTTP $http_status from $BACKEND_URL)" >&2
    ok=false
fi

if ! curl -sk --connect-timeout 2 -m 3 "$FRONTEND_URL" -o /dev/null 2>/dev/null; then
    echo "frontend down ($FRONTEND_URL unreachable)" >&2
    ok=false
fi

if [ "$ok" = "false" ]; then
    exit 1
fi

echo "backend up, frontend up"
