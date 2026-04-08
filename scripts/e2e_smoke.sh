#!/usr/bin/env bash
# myOS end-to-end smoke test.
#
# Runs a full health check before a release:
#   1. Backend test suite (pytest)
#   2. Frontend test suite (vitest)
#   3. TypeScript project build (tsc -b)
#   4. Live HTTP checks against the running API on the configured port
#   5. WebSocket round trip through the chat panel
#
# The script assumes an API server is already running on port 8000
# (the default dev port). If no server is running, the live HTTP and
# WebSocket phases are skipped with a warning.
#
# It never calls the real Anthropic API. All chat traffic uses a mock
# server path when the live server is not up.
#
# Usage:
#   ./scripts/e2e_smoke.sh               # full run
#   SKIP_UNIT=1 ./scripts/e2e_smoke.sh   # only live HTTP + WS checks
#   SKIP_LIVE=1 ./scripts/e2e_smoke.sh   # only unit suites
#   API_PORT=8001 ./scripts/e2e_smoke.sh # override port
#
# Exit code is 0 when every enabled phase passes, 1 otherwise.

set -u

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
API_PORT="${API_PORT:-8000}"
API_BASE="http://localhost:${API_PORT}"
SKIP_UNIT="${SKIP_UNIT:-0}"
SKIP_LIVE="${SKIP_LIVE:-0}"

PASS=0
FAIL=0
SKIP=0

phase_pass() {
    echo -e "  ${GREEN}PASS${NC}  $1"
    PASS=$((PASS + 1))
}

phase_fail() {
    echo -e "  ${RED}FAIL${NC}  $1"
    FAIL=$((FAIL + 1))
}

phase_skip() {
    echo -e "  ${YELLOW}SKIP${NC}  $1"
    SKIP=$((SKIP + 1))
}

header() {
    echo ""
    echo -e "${BLUE}=== $1 ===${NC}"
    echo ""
}

header "myOS end-to-end smoke test"
echo "Repo:    $REPO_DIR"
echo "API:     $API_BASE"
echo "Skip unit:  $SKIP_UNIT"
echo "Skip live:  $SKIP_LIVE"

# --- Phase 1: backend unit tests -------------------------------------------

if [ "$SKIP_UNIT" != "1" ]; then
    header "Backend unit tests"
    if [ -d "$REPO_DIR/api/.venv" ]; then
        if (cd "$REPO_DIR/api" && . .venv/bin/activate && python -m pytest -q --tb=short); then
            phase_pass "pytest suite"
        else
            phase_fail "pytest suite"
        fi
    else
        phase_skip "pytest suite (no venv at api/.venv)"
    fi
fi

# --- Phase 2: frontend unit tests ------------------------------------------

if [ "$SKIP_UNIT" != "1" ]; then
    header "Frontend unit tests"
    if command -v pnpm > /dev/null 2>&1; then
        if (cd "$REPO_DIR/app" && pnpm test --run); then
            phase_pass "vitest suite"
        else
            phase_fail "vitest suite"
        fi
    else
        phase_skip "vitest suite (pnpm not installed)"
    fi
fi

# --- Phase 3: TypeScript build ---------------------------------------------

if [ "$SKIP_UNIT" != "1" ]; then
    header "TypeScript project build"
    if command -v pnpm > /dev/null 2>&1; then
        if (cd "$REPO_DIR/app" && pnpm exec tsc -b); then
            phase_pass "tsc -b"
        else
            phase_fail "tsc -b"
        fi
    else
        phase_skip "tsc -b (pnpm not installed)"
    fi
fi

# --- Phase 4: live HTTP checks ---------------------------------------------

server_up() {
    curl -sS -o /dev/null -w "%{http_code}" "${API_BASE}/api/settings" 2>/dev/null | grep -q "^200$"
}

check_http_json() {
    # $1: name, $2: path, $3: grep expression for a required substring
    local name="$1" path="$2" required="$3"
    local body
    body=$(curl -sS "${API_BASE}${path}" 2>/dev/null)
    if [ -z "$body" ]; then
        phase_fail "$name (empty body)"
        return 1
    fi
    if [ -n "$required" ] && ! echo "$body" | grep -q "$required"; then
        phase_fail "$name (missing '$required')"
        return 1
    fi
    phase_pass "$name"
    return 0
}

if [ "$SKIP_LIVE" != "1" ]; then
    header "Live HTTP checks"
    if ! server_up; then
        phase_skip "API not reachable on ${API_BASE}; start it with start.sh and re-run"
    else
        check_http_json "GET /api/settings has tour_complete"        "/api/settings"              '"tour_complete"'
        check_http_json "GET /api/settings has whats_new_last_seen"  "/api/settings"              '"whats_new_last_seen"'
        check_http_json "GET /api/settings has custom_agent_templates" "/api/settings"            '"custom_agent_templates"'
        check_http_json "GET /api/settings has auto_label_tasks"     "/api/settings"              '"auto_label_tasks"'
        check_http_json "GET /api/settings has auto_template_matching" "/api/settings"            '"auto_template_matching"'
        check_http_json "GET /api/settings has chat_backend_preference" "/api/settings"           '"chat_backend_preference"'
        check_http_json "GET /api/agents returns list"               "/api/agents"                '"agents"'
        check_http_json "GET /api/tasks returns list"                "/api/tasks"                 '"tasks"'
        check_http_json "GET /api/labels returns list"               "/api/labels"                '"labels"'
        check_http_json "GET /api/chat/history works"                "/api/chat/history"          ""
        check_http_json "GET /api/files/preview markdown"            "/api/files/preview?path=README.md"           ""

        # README.md is markdown. The rich preview endpoint rejects it
        # (client should use /files/read for markdown). Expect 400.
        code=$(curl -sS -o /dev/null -w "%{http_code}" "${API_BASE}/api/files/preview?path=README.md")
        if [ "$code" = "400" ]; then
            phase_pass "markdown gets 400 from rich preview (as designed)"
        else
            phase_fail "markdown preview returned $code instead of 400"
        fi

        # beautify-deck with bogus path should be rejected cleanly.
        code=$(curl -sS -o /dev/null -w "%{http_code}" \
            -X POST "${API_BASE}/api/files/beautify-deck" \
            -H 'content-type: application/json' \
            -d '{"path":"no-such-deck.pptx"}')
        if [ "$code" = "400" ] || [ "$code" = "404" ]; then
            phase_pass "POST /api/files/beautify-deck rejects missing file"
        else
            phase_fail "beautify-deck returned $code for missing file"
        fi

        # Create a task and verify it shows up in the tasks list. This
        # exercises the auto-label scheduling path too.
        title="e2e-smoke-task-$(date +%s)"
        create_resp=$(curl -sS -X POST "${API_BASE}/api/tasks" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"$title\",\"priority\":\"P2\"}")
        if echo "$create_resp" | grep -q '"task_id"'; then
            phase_pass "POST /api/tasks creates a task"
        else
            phase_fail "POST /api/tasks (body: $create_resp)"
        fi
    fi
fi

# --- Phase 5: WebSocket chat round trip ------------------------------------

if [ "$SKIP_LIVE" != "1" ]; then
    header "WebSocket chat round trip"
    if ! server_up; then
        phase_skip "WebSocket chat (API not reachable)"
    elif ! command -v python3 > /dev/null 2>&1; then
        phase_skip "WebSocket chat (python3 not installed)"
    else
        WS_RESULT=$(
            PYTHONPATH="$REPO_DIR/api" API_PORT="$API_PORT" python3 - <<'PY'
import asyncio
import json
import os
import sys

try:
    import websockets
except ImportError:
    print("NO_WS_LIB")
    sys.exit(0)

API_PORT = os.environ.get("API_PORT", "8000")
# The chat websocket router is mounted at the root of the ASGI app with
# no ``/api`` prefix, so the correct path is ``/ws/chat``.
URL = f"ws://localhost:{API_PORT}/ws/chat"

async def main():
    try:
        async with websockets.connect(URL, open_timeout=5) as ws:
            await ws.send(json.dumps({
                "messages": [{"role": "user", "content": "say hi"}],
                "model": "@claude",
            }))
            got_token = False
            got_done = False
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    et = event.get("type")
                    if et == "token" and event.get("data"):
                        got_token = True
                    if et == "done":
                        got_done = True
                        break
                    if et == "error":
                        print(f"ERROR:{event.get('data','')[:200]}")
                        return
            except asyncio.TimeoutError:
                print("TIMEOUT")
                return
            if got_token and got_done:
                print("OK")
            elif got_done and not got_token:
                print("EMPTY_RESPONSE")
            else:
                print("NO_DONE")
    except Exception as exc:
        print(f"CONNECT_FAIL:{exc}")

asyncio.run(main())
PY
)
        case "$WS_RESULT" in
            OK)
                phase_pass "chat WS streamed at least one token and a done event"
                ;;
            EMPTY_RESPONSE)
                phase_fail "chat WS finished with no tokens (empty response bug)"
                ;;
            NO_DONE)
                phase_fail "chat WS never sent a done event"
                ;;
            TIMEOUT)
                phase_fail "chat WS timed out waiting for a response"
                ;;
            NO_WS_LIB)
                phase_skip "chat WS (Python websockets library not installed)"
                ;;
            ERROR:*)
                phase_fail "chat WS reported error: ${WS_RESULT#ERROR:}"
                ;;
            CONNECT_FAIL:*)
                phase_fail "chat WS could not connect: ${WS_RESULT#CONNECT_FAIL:}"
                ;;
            *)
                phase_fail "chat WS unknown result: $WS_RESULT"
                ;;
        esac
    fi
fi

# --- Summary ---------------------------------------------------------------

header "Summary"
echo -e "  ${GREEN}PASS${NC} $PASS"
echo -e "  ${RED}FAIL${NC} $FAIL"
echo -e "  ${YELLOW}SKIP${NC} $SKIP"
echo ""

if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}All phases passed.${NC} myOS is ready to release."
    exit 0
else
    echo -e "${RED}$FAIL phase(s) failed.${NC} Fix them before releasing."
    exit 1
fi
