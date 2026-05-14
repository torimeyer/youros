#!/usr/bin/env bash
# check-ostk-cache-coverage.sh
# Audits which running claude PIDs are routing through the ostk-cache proxy.
# Usage: bash scripts/check-ostk-cache-coverage.sh
# See: feedback_ostk_cache_existing_sessions_unaffected.md
set -euo pipefail

PROXY_ENV_VAR="ANTHROPIC_BASE_URL"
PROXY_PORT="${OSTK_CACHE_PORT:-8080}"

echo "=== ostk-cache coverage audit ($(date)) ==="
echo ""

# 1. Check if proxy is running on expected port
if lsof -nP -iTCP:${PROXY_PORT} -sTCP:LISTEN 2>/dev/null | grep -q "${PROXY_PORT}"; then
  PROXY_PID=$(lsof -nP -iTCP:${PROXY_PORT} -sTCP:LISTEN 2>/dev/null | awk 'NR==2{print $2}')
  echo "[OK] Proxy listening on :${PROXY_PORT} (PID ${PROXY_PID:-unknown})"
else
  echo "[!!] No process listening on :${PROXY_PORT} — proxy is NOT running"
  echo "     Start with: launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/io.ostk.cache.plist"
  echo ""
fi

# 2. Check live connections to proxy
CONN_COUNT=$(lsof -nP -iTCP:${PROXY_PORT} -sTCP:ESTABLISHED 2>/dev/null | wc -l | tr -d ' ')
echo "[INFO] Active connections to proxy: $((CONN_COUNT > 0 ? CONN_COUNT - 1 : 0))"
echo ""

# 3. Audit all claude PIDs
echo "=== Claude process coverage ==="
CLAUDE_PIDS=$(pgrep -f "claude" 2>/dev/null || true)

if [ -z "$CLAUDE_PIDS" ]; then
  echo "No claude processes found."
  exit 0
fi

ROUTED=0
NOT_ROUTED=0
TOTAL=0

for pid in $CLAUDE_PIDS; do
  TOTAL=$((TOTAL + 1))
  # Get the env var value for this specific PID
  ENV_VAL=$(ps eww -p "$pid" 2>/dev/null | grep -o "${PROXY_ENV_VAR}=[^ ]*" | head -1 || true)
  CMD=$(ps -p "$pid" -o comm= 2>/dev/null | head -1 || echo "unknown")

  if [ -n "$ENV_VAL" ]; then
    echo "  [ROUTED]     PID $pid ($CMD) — $ENV_VAL"
    ROUTED=$((ROUTED + 1))
  else
    echo "  [NOT ROUTED] PID $pid ($CMD) — ${PROXY_ENV_VAR} not set"
    NOT_ROUTED=$((NOT_ROUTED + 1))
  fi
done

echo ""
echo "=== Summary ==="
echo "  Total claude PIDs: $TOTAL"
echo "  Routed through proxy: $ROUTED"
echo "  NOT routed (direct to api.anthropic.com): $NOT_ROUTED"
echo ""

if [ "$NOT_ROUTED" -gt 0 ]; then
  echo "[ACTION] $NOT_ROUTED session(s) are bypassing the proxy."
  echo "         Open a new terminal tab (which will pick up ~/.zshrc) and restart those sessions."
fi

if [ "$CONN_COUNT" -gt 0 ] && [ "$ROUTED" -eq 0 ]; then
  echo "[WARN] Proxy has connections but no claude PID shows the env var. Check for other API clients."
fi
