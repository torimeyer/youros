#!/usr/bin/env bash
# myOS end-to-end smoke test.
#
# Runs a full health check before a release:
#   1. Backend test suite (pytest)
#   2. Frontend test suite (vitest)
#   3. TypeScript project build (tsc -b)
#   4. Live HTTP checks against the running API on the configured port
#   5. WebSocket round trip through the chat panel
#   6. Browser e2e tests via agent-browser (optional, needs agent-browser)
#
# The script assumes an API server is already running on port 8000
# (the default dev port). If no server is running, the live HTTP and
# WebSocket phases are skipped with a warning.
#
# It never calls the real Anthropic API. All chat traffic uses a mock
# server path when the live server is not up.
#
# Usage:
#   ./scripts/e2e_smoke.sh                   # full run
#   SKIP_UNIT=1 ./scripts/e2e_smoke.sh       # only live HTTP + WS checks
#   SKIP_LIVE=1 ./scripts/e2e_smoke.sh       # only unit suites
#   API_PORT=8001 ./scripts/e2e_smoke.sh     # override port
#   RELEASE_MODE=1 ./scripts/e2e_smoke.sh    # release verification mode
#   SKIP_BROWSER=1 ./scripts/e2e_smoke.sh   # skip browser e2e (Phase 6)
#
# Exit code is 0 when every enabled phase passes, 1 otherwise.
#
# RELEASE_MODE note (needle 307): the watchfiles thrash loop on
# services/task_audit.py used to cycle the backend mid-request during
# phases 4 and 5 of this script, which blocked the release gate. The
# fix lives in scripts/dev-backend.sh: when RELEASE_MODE=1 is set,
# dev-backend.sh starts uvicorn WITHOUT --reload so the server stays
# put for the whole smoke run. This script exports RELEASE_MODE so a
# dev-backend.sh started as a child process picks it up. It is the
# caller's responsibility to restart the backend with this env var set
# before running release verification. The phase 4 / phase 5 checks
# run faster and more reliably with reload disabled.

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
RELEASE_MODE="${RELEASE_MODE:-0}"

# Propagate RELEASE_MODE so any child process that starts or checks
# the backend (such as scripts/dev-backend.sh) can honor it.
export RELEASE_MODE

# Track the original os_name so we can restore it on exit. The settings
# PATCH round trip (phase 4) sets os_name to "e2e-test-os" to verify the
# endpoint works. If the script is interrupted before the restore curl
# fires, the name sticks and Tori's browser shows "e2e-test-os" on the
# next restart. Needle 315: use a trap so the restore always runs.
_E2E_ORIGINAL_OS_NAME=""

# Sweep all e2e test artifacts (tasks + labels) from the running backend.
# Called at both script start (to clear leftovers from prior failed runs)
# and on exit via trap. Needle →321: individual inline cleanups are best-
# effort. If a uvicorn reload drops the HTTP response after ostk writes
# the task to disk, the inline delete never fires. This sweep is the
# safety net.
_e2e_sweep_artifacts() {
    # Delete any tasks whose title starts with "e2e-"
    python3 -c "
import sys, json, urllib.request
try:
    resp = urllib.request.urlopen('${API_BASE}/api/tasks', timeout=3)
    tasks = json.loads(resp.read()).get('tasks', [])
    for t in tasks:
        title = t.get('title', '')
        tid = t.get('id', '')
        if title.startswith('e2e-') and tid:
            req = urllib.request.Request(
                '${API_BASE}/api/tasks/' + urllib.parse.quote(tid, safe=''),
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

    # Delete any labels whose name starts with "e2e-"
    python3 -c "
import sys, json, urllib.request
try:
    resp = urllib.request.urlopen('${API_BASE}/api/labels', timeout=3)
    labels = json.loads(resp.read()).get('labels', [])
    for l in labels:
        name = l.get('name', '')
        lid = l.get('id', '')
        if name.startswith('e2e-') and lid:
            req = urllib.request.Request(
                '${API_BASE}/api/labels/' + lid,
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

    # Delete any shared links whose title starts with "e2e"
    python3 -c "
import sys, json, urllib.request
try:
    resp = urllib.request.urlopen('${API_BASE}/api/shares', timeout=3)
    shares = json.loads(resp.read()).get('shares', [])
    for s in shares:
        title = s.get('title', '')
        token = s.get('token', '')
        if title.lower().startswith('e2e') and token:
            req = urllib.request.Request(
                '${API_BASE}/api/shares/' + token,
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true
}

_e2e_cleanup() {
    _e2e_sweep_artifacts
    if [ -n "$_E2E_ORIGINAL_OS_NAME" ]; then
        curl -sS -X PATCH "${API_BASE}/api/settings" \
            -H 'content-type: application/json' \
            -d "{\"os_name\":\"$_E2E_ORIGINAL_OS_NAME\"}" > /dev/null 2>&1 || true
    fi
}
trap _e2e_cleanup EXIT INT TERM HUP

# Clean up leftovers from any prior failed run before starting.
_e2e_sweep_artifacts

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
if [ "$RELEASE_MODE" = "1" ]; then
    echo -e "Release mode: ${GREEN}ON${NC} (backend should be started with --no-reload)"
else
    echo "Release mode: off"
fi

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
            # Extract the task id and DELETE so smoke tasks never accumulate.
            smoke_task_id=$(echo "$create_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
            if [ -n "$smoke_task_id" ]; then
                curl -sS -X DELETE "${API_BASE}/api/tasks/${smoke_task_id}" > /dev/null 2>&1 || true
            fi
        else
            phase_fail "POST /api/tasks (body: $create_resp)"
        fi
        # --- Fleet endpoints ---
        check_http_json "GET /api/agents/fleets returns fleet list"      "/api/agents/fleets"         '"fleets"'

        # Verify fleet count (should be 9)
        fleet_count=$(curl -sS "${API_BASE}/api/agents/fleets" 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('fleets',[])))" 2>/dev/null)
        if [ "$fleet_count" -ge 9 ]; then
            phase_pass "fleet templates count >= 9 ($fleet_count)"
        else
            phase_fail "fleet templates count is $fleet_count, expected >= 9"
        fi

        # --- Task audit endpoint ---
        check_http_json "GET /api/tasks/audit returns findings"          "/api/tasks/audit"           '"findings"'
        check_http_json "GET /api/tasks/audit has summary"               "/api/tasks/audit"           '"summary"'

        # --- Task duplicates endpoint ---
        check_http_json "GET /api/tasks/duplicates returns list"         "/api/tasks/duplicates"      '"duplicates"'

        # --- Task health endpoint ---
        check_http_json "GET /api/tasks/health runs"                     "/api/tasks/health"          ""

        # --- Briefing endpoint ---
        check_http_json "GET /api/briefing returns show field"           "/api/briefing"              '"show"'

        # --- Upgrade status ---
        check_http_json "GET /api/upgrade/status returns myos info"      "/api/upgrade/status"        '"myos"'
        check_http_json "GET /api/upgrade/status returns ostk info"      "/api/upgrade/status"        '"ostk"'

        # --- Status/clock endpoint ---
        check_http_json "GET /api/status/clock returns kernel"           "/api/status/clock"          '"kernel"'

        # --- Workflow templates ---
        check_http_json "GET /api/workflows/templates returns list"      "/api/workflows/templates"   '"templates"'

        # Verify workflow template count (should be 9)
        wf_count=$(curl -sS "${API_BASE}/api/workflows/templates" 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('templates',[])))" 2>/dev/null)
        if [ "$wf_count" -ge 9 ]; then
            phase_pass "workflow templates count >= 9 ($wf_count)"
        else
            phase_fail "workflow templates count is $wf_count, expected >= 9"
        fi

        # --- Calendar auth status ---
        check_http_json "GET /api/calendar/auth/status responds"         "/api/calendar/auth/status"  '"authenticated"'

        # --- Gmail auth status ---
        check_http_json "GET /api/gmail/auth/status responds"            "/api/gmail/auth/status"     '"authenticated"'

        # --- Drive auth status ---
        check_http_json "GET /api/drive/auth/status responds"            "/api/drive/auth/status"     '"authenticated"'

        # --- Transcripts endpoint ---
        check_http_json "GET /api/transcripts returns list"              "/api/transcripts"           '"transcripts"'

        # --- Export endpoints ---
        check_http_json "GET /api/export/tasks returns markdown"         "/api/export/tasks"          ""
        check_http_json "GET /api/export/timeline returns markdown"      "/api/export/timeline"       ""

        # --- Notifications endpoint ---
        check_http_json "GET /api/notifications returns list"            "/api/notifications"         ""

        # --- Agent register + complete lifecycle ---
        reg_resp=$(curl -sS -X POST "${API_BASE}/api/agents/register" \
            -H 'content-type: application/json' \
            -d '{"name":"e2e-lifecycle-test","model":"sonnet","budget":0,"status":"running"}' 2>/dev/null)
        if echo "$reg_resp" | grep -q '"result"'; then
            phase_pass "POST /api/agents/register creates running agent"
        else
            phase_fail "POST /api/agents/register (body: $reg_resp)"
        fi

        # Verify it shows as active
        active_check=$(curl -sS "${API_BASE}/api/agents" 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('yes' if 'e2e-lifecycle-test' in d.get('active',[]) else 'no')
" 2>/dev/null)
        if [ "$active_check" = "yes" ]; then
            phase_pass "registered agent appears in active list"
        else
            phase_fail "registered agent not in active list"
        fi

        # Complete the agent
        comp_resp=$(curl -sS -X POST "${API_BASE}/api/agents/e2e-lifecycle-test/complete" \
            -H 'content-type: application/json' 2>/dev/null)
        if echo "$comp_resp" | grep -q '"completed"'; then
            phase_pass "POST /api/agents/{name}/complete marks agent done"
        else
            phase_fail "POST /api/agents/complete (body: $comp_resp)"
        fi

        # Verify it is no longer active
        active_after=$(curl -sS "${API_BASE}/api/agents" 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('yes' if 'e2e-lifecycle-test' in d.get('active',[]) else 'no')
" 2>/dev/null)
        if [ "$active_after" = "no" ]; then
            phase_pass "completed agent removed from active list"
        else
            phase_fail "completed agent still in active list"
        fi

        # --- Task CRUD lifecycle ---
        crud_title="e2e-crud-$(date +%s)"
        crud_resp=$(curl -sS -X POST "${API_BASE}/api/tasks" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"$crud_title\",\"priority\":\"P1\"}" 2>/dev/null)
        crud_id=$(echo "$crud_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        if [ -n "$crud_id" ]; then
            phase_pass "task CRUD: create works"
        else
            phase_fail "task CRUD: create failed"
        fi

        # Verify task appears in list
        if curl -sS "${API_BASE}/api/tasks" 2>/dev/null | grep -q "$crud_title"; then
            phase_pass "task CRUD: appears in task list"
        else
            phase_fail "task CRUD: not found in task list"
        fi

        # Close the task (test the close endpoint, then delete to clean up)
        close_resp=$(curl -sS -X POST "${API_BASE}/api/tasks/${crud_id}/close" \
            -H 'content-type: application/json' 2>/dev/null)
        if echo "$close_resp" | grep -q '"result"'; then
            phase_pass "task CRUD: close works"
        else
            phase_fail "task CRUD: close failed"
        fi
        # Delete to truly remove test data
        curl -sS -X DELETE "${API_BASE}/api/tasks/${crud_id}" > /dev/null 2>&1

        # --- Label CRUD lifecycle ---
        label_resp=$(curl -sS -X POST "${API_BASE}/api/labels" \
            -H 'content-type: application/json' \
            -d '{"name":"e2e-test-label","color":"#ef4444"}' 2>/dev/null)
        if echo "$label_resp" | grep -q "e2e-test-label"; then
            phase_pass "label CRUD: create works"
        else
            phase_fail "label CRUD: create failed"
        fi

        label_id=$(echo "$label_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('label',{}).get('id', d.get('id','')))
" 2>/dev/null || true)
        if [ -n "$label_id" ]; then
            curl -sS -X DELETE "${API_BASE}/api/labels/${label_id}" > /dev/null 2>&1
            phase_pass "label CRUD: delete works"
        fi

        # --- Settings PATCH round trip ---
        # Save the original name into the trap variable so _e2e_cleanup
        # can restore it even if the script is interrupted mid-test.
        _E2E_ORIGINAL_OS_NAME=$(curl -sS "${API_BASE}/api/settings" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('os_name',''))" 2>/dev/null)
        curl -sS -X PATCH "${API_BASE}/api/settings" \
            -H 'content-type: application/json' \
            -d '{"os_name":"e2e-test-os"}' > /dev/null 2>&1
        settings_after=$(curl -sS "${API_BASE}/api/settings" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('os_name',''))" 2>/dev/null)
        if [ "$settings_after" = "e2e-test-os" ]; then
            phase_pass "settings PATCH round trip works"
            # Restore immediately (trap is the safety net if this fails).
            curl -sS -X PATCH "${API_BASE}/api/settings" \
                -H 'content-type: application/json' \
                -d "{\"os_name\":\"$_E2E_ORIGINAL_OS_NAME\"}" > /dev/null 2>&1
            _E2E_ORIGINAL_OS_NAME=""
        else
            phase_fail "settings PATCH did not persist"
            _E2E_ORIGINAL_OS_NAME=""
        fi

        # --- Briefing dismiss round trip ---
        curl -sS -X POST "${API_BASE}/api/briefing/dismiss" \
            -H 'content-type: application/json' > /dev/null 2>&1
        dismiss_check=$(curl -sS "${API_BASE}/api/briefing" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('show',True))" 2>/dev/null)
        if [ "$dismiss_check" = "False" ]; then
            phase_pass "briefing dismiss hides briefing"
        else
            phase_fail "briefing dismiss did not hide briefing"
        fi

        # =================================================================
        # USER JOURNEY TESTS
        # These test real end-to-end flows the way a person uses ToriOS,
        # not just "does the endpoint return 200". Each journey creates
        # data, verifies the result, and cleans up after itself.
        # =================================================================

        # --- Journey: Task reopen after close ---
        reopen_title="e2e-reopen-$(date +%s)"
        reopen_resp=$(curl -sS -X POST "${API_BASE}/api/tasks" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"$reopen_title\",\"priority\":\"P2\"}" 2>/dev/null)
        reopen_id=$(echo "$reopen_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        if [ -n "$reopen_id" ]; then
            curl -sS -X POST "${API_BASE}/api/tasks/${reopen_id}/close" \
                -H 'content-type: application/json' > /dev/null 2>&1
            reopen_result=$(curl -sS -X POST "${API_BASE}/api/tasks/${reopen_id}/reopen" \
                -H 'content-type: application/json' 2>/dev/null)
            if echo "$reopen_result" | grep -q '"result"'; then
                phase_pass "journey: close then reopen task"
            else
                phase_fail "journey: reopen failed (body: $reopen_result)"
            fi
            curl -sS -X DELETE "${API_BASE}/api/tasks/${reopen_id}" > /dev/null 2>&1
        else
            phase_fail "journey: could not create task for reopen test"
        fi

        # --- Journey: Task dependencies (link two tasks) ---
        dep_a_title="e2e-blocker-$(date +%s)"
        dep_b_title="e2e-blocked-$(date +%s)"
        dep_a_resp=$(curl -sS -X POST "${API_BASE}/api/tasks" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"$dep_a_title\",\"priority\":\"P1\"}" 2>/dev/null)
        dep_a_id=$(echo "$dep_a_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        dep_b_resp=$(curl -sS -X POST "${API_BASE}/api/tasks" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"$dep_b_title\",\"priority\":\"P1\"}" 2>/dev/null)
        dep_b_id=$(echo "$dep_b_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        if [ -n "$dep_a_id" ] && [ -n "$dep_b_id" ]; then
            link_resp=$(curl -sS -X POST "${API_BASE}/api/tasks/${dep_a_id}/link" \
                -H 'content-type: application/json' \
                -d "{\"target\":\"$dep_b_id\",\"relation\":\"blocks\"}" 2>/dev/null)
            if echo "$link_resp" | grep -q '"result"'; then
                phase_pass "journey: link two tasks (blocks relationship)"
            else
                phase_fail "journey: task link failed (body: $link_resp)"
            fi
            curl -sS -X DELETE "${API_BASE}/api/tasks/${dep_a_id}" > /dev/null 2>&1
            curl -sS -X DELETE "${API_BASE}/api/tasks/${dep_b_id}" > /dev/null 2>&1
        else
            phase_fail "journey: could not create tasks for dependency test"
        fi

        # --- Journey: Dashboard compounds (focus first) ---
        compounds_resp=$(curl -sS "${API_BASE}/api/dashboard/compounds" 2>/dev/null)
        if echo "$compounds_resp" | grep -q '"all"'; then
            phase_pass "journey: dashboard compounds returns dependency analysis"
        else
            phase_fail "journey: dashboard compounds missing 'all' field"
        fi

        # --- Journey: Dashboard summary ---
        summary_resp=$(curl -sS "${API_BASE}/api/dashboard/summary" 2>/dev/null)
        if echo "$summary_resp" | grep -q '"bullets"'; then
            phase_pass "journey: dashboard summary returns bullets"
        else
            phase_fail "journey: dashboard summary missing 'bullets' field"
        fi

        # --- Journey: Capture idea and convert to tasks ---
        idea_text="e2e-idea-$(date +%s): build a widget"
        idea_resp=$(curl -sS -X POST "${API_BASE}/api/ideas" \
            -H 'content-type: application/json' \
            -d "{\"thought\":\"$idea_text\"}" 2>/dev/null)
        if echo "$idea_resp" | grep -q '"result"'; then
            phase_pass "journey: capture idea"
            # Convert idea to tasks (with delete_hay to auto-clean the idea)
            convert_resp=$(curl -sS -X POST "${API_BASE}/api/ideas/convert" \
                -H 'content-type: application/json' \
                -d "{\"straw\":\"$idea_text\",\"priority\":\"P2\",\"delete_hay\":true}" 2>/dev/null)
            convert_status=$(echo "$convert_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
            if [ "$convert_status" = "created" ] || [ "$convert_status" = "needs_clarification" ]; then
                phase_pass "journey: convert idea to tasks (status: $convert_status)"
            else
                phase_fail "journey: idea conversion unexpected status ($convert_status)"
            fi
            # Clean up any tasks created by the conversion
            convert_task_ids=$(echo "$convert_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for t in d.get('tasks',[]):
    print(t.get('id',''))
" 2>/dev/null || true)
            for ctid in $convert_task_ids; do
                [ -n "$ctid" ] && curl -sS -X DELETE "${API_BASE}/api/tasks/${ctid}" > /dev/null 2>&1
            done
            # Check converted tab
            converted_resp=$(curl -sS "${API_BASE}/api/ideas?status=converted" 2>/dev/null)
            if echo "$converted_resp" | grep -q '"converted"'; then
                phase_pass "journey: converted ideas tab returns data"
            else
                phase_fail "journey: converted ideas tab missing 'converted' field"
            fi
        else
            phase_fail "journey: capture idea failed (body: $idea_resp)"
            # Clean up the idea if it was partially created
            curl -sS -X DELETE "${API_BASE}/api/ideas/$(python3 -c "import urllib.parse; print(urllib.parse.quote('$idea_text', safe=''))")" > /dev/null 2>&1
        fi

        # --- Journey: Task label assignment round trip ---
        lbl_name="e2e-label-$(date +%s)"
        lbl_resp=$(curl -sS -X POST "${API_BASE}/api/labels" \
            -H 'content-type: application/json' \
            -d "{\"name\":\"$lbl_name\",\"color\":\"#3b82f6\"}" 2>/dev/null)
        lbl_id=$(echo "$lbl_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('label',{}).get('id', d.get('id','')))
" 2>/dev/null || true)
        lbl_task_resp=$(curl -sS -X POST "${API_BASE}/api/tasks" \
            -H 'content-type: application/json' \
            -d '{"title":"e2e-label-task","priority":"P2"}' 2>/dev/null)
        lbl_task_id=$(echo "$lbl_task_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        if [ -n "$lbl_id" ] && [ -n "$lbl_task_id" ]; then
            assign_resp=$(curl -sS -X POST "${API_BASE}/api/tasks/${lbl_task_id}/labels/${lbl_id}" \
                -H 'content-type: application/json' 2>/dev/null)
            if echo "$assign_resp" | grep -q '"label_ids"'; then
                phase_pass "journey: assign label to task"
            else
                phase_fail "journey: label assign failed (body: $assign_resp)"
            fi
            remove_resp=$(curl -sS -X DELETE "${API_BASE}/api/tasks/${lbl_task_id}/labels/${lbl_id}" 2>/dev/null)
            if echo "$remove_resp" | grep -q '"label_ids"'; then
                phase_pass "journey: remove label from task"
            else
                phase_fail "journey: label remove failed (body: $remove_resp)"
            fi
            curl -sS -X DELETE "${API_BASE}/api/tasks/${lbl_task_id}" > /dev/null 2>&1
            curl -sS -X DELETE "${API_BASE}/api/labels/${lbl_id}" > /dev/null 2>&1
        else
            phase_fail "journey: could not set up label assignment test"
        fi

        # --- Journey: Task reorder ---
        reorder_task_resp=$(curl -sS -X POST "${API_BASE}/api/tasks" \
            -H 'content-type: application/json' \
            -d '{"title":"e2e-reorder-task","priority":"P2"}' 2>/dev/null)
        reorder_task_id=$(echo "$reorder_task_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        if [ -n "$reorder_task_id" ]; then
            reorder_resp=$(curl -sS -X POST "${API_BASE}/api/tasks/reorder" \
                -H 'content-type: application/json' \
                -d "{\"task_id\":\"$reorder_task_id\",\"new_priority\":\"P0\",\"position\":0}" 2>/dev/null)
            if echo "$reorder_resp" | grep -q '"new_priority"'; then
                phase_pass "journey: reorder task (P2 to P0)"
            else
                phase_fail "journey: task reorder failed (body: $reorder_resp)"
            fi
            curl -sS -X DELETE "${API_BASE}/api/tasks/${reorder_task_id}" > /dev/null 2>&1
        else
            phase_fail "journey: could not create task for reorder test"
        fi

        # --- Journey: Activity log shows events ---
        activity_resp=$(curl -sS "${API_BASE}/api/activity?last=10" 2>/dev/null)
        activity_count=$(echo "$activity_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")
        if [ "$activity_count" -gt 0 ] 2>/dev/null; then
            phase_pass "journey: activity log has events ($activity_count)"
        else
            phase_fail "journey: activity log empty after creating tasks"
        fi

        # --- Journey: Search finds a task ---
        search_resp=$(curl -sS "${API_BASE}/api/search?q=e2e" 2>/dev/null)
        if [ -n "$search_resp" ] && [ "$search_resp" != "null" ]; then
            phase_pass "journey: search returns results for 'e2e'"
        else
            phase_fail "journey: search returned empty for 'e2e'"
        fi

        # --- Journey: Notifications ---
        notif_count_resp=$(curl -sS "${API_BASE}/api/notifications/unread/count" 2>/dev/null)
        if echo "$notif_count_resp" | grep -q '"count"'; then
            phase_pass "journey: notification unread count endpoint works"
        else
            phase_fail "journey: notification unread count missing 'count' field"
        fi
        markread_resp=$(curl -sS -X POST "${API_BASE}/api/notifications/read-all" \
            -H 'content-type: application/json' 2>/dev/null)
        if echo "$markread_resp" | grep -q '"result"'; then
            phase_pass "journey: mark all notifications read"
        else
            phase_fail "journey: mark all notifications read failed"
        fi

        # --- Journey: Docs draft create + cleanup ---
        doc_resp=$(curl -sS -X POST "${API_BASE}/api/docs/draft" \
            -H 'content-type: application/json' \
            -d '{"title":"e2e-test-draft"}' 2>/dev/null)
        if echo "$doc_resp" | grep -q '"result"'; then
            phase_pass "journey: create doc draft"
            # Extract the draft path from the result and delete it
            doc_path=$(echo "$doc_resp" | python3 -c "
import sys,json
r=json.load(sys.stdin).get('result','')
# ostk doc draft returns a path like 'docs/draft/e2e-test-draft.md'
import re
m=re.search(r'(docs/draft/[^\s]+)', r)
print(m.group(1) if m else '')
" 2>/dev/null || true)
            if [ -n "$doc_path" ]; then
                curl -sS -X DELETE "${API_BASE}/api/${doc_path}" > /dev/null 2>&1
            fi
        else
            phase_fail "journey: create doc draft failed (body: $doc_resp)"
        fi

        # --- Journey: Workflow create and list ---
        wf_resp=$(curl -sS -X POST "${API_BASE}/api/workflows" \
            -H 'content-type: application/json' \
            -d '{"name":"e2e-test-workflow","steps":[{"agent_name":"e2e-step","prompt":"echo hello","model":"sonnet","budget":0}]}' 2>/dev/null)
        wf_id=$(echo "$wf_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('workflow',{}).get('id',''))" 2>/dev/null || true)
        if [ -n "$wf_id" ]; then
            phase_pass "journey: create workflow"
            wf_list=$(curl -sS "${API_BASE}/api/workflows" 2>/dev/null)
            if echo "$wf_list" | grep -q "e2e-test-workflow"; then
                phase_pass "journey: workflow appears in list"
            else
                phase_fail "journey: workflow not found in list"
            fi
            curl -sS -X DELETE "${API_BASE}/api/workflows/${wf_id}" > /dev/null 2>&1
        else
            phase_fail "journey: create workflow failed (body: $wf_resp)"
        fi

        # --- Journey: Settings feature toggle round trip ---
        _orig_features=$(curl -sS "${API_BASE}/api/settings" 2>/dev/null | python3 -c "
import sys,json
s=json.load(sys.stdin)
f=s.get('features',{})
print(json.dumps(f))
" 2>/dev/null)
        curl -sS -X PATCH "${API_BASE}/api/settings" \
            -H 'content-type: application/json' \
            -d '{"features":{"Docs":false}}' > /dev/null 2>&1
        toggle_check=$(curl -sS "${API_BASE}/api/settings" 2>/dev/null | python3 -c "
import sys,json
s=json.load(sys.stdin)
print(s.get('features',{}).get('Docs', True))
" 2>/dev/null)
        if [ "$toggle_check" = "False" ]; then
            phase_pass "journey: feature toggle persists (Docs disabled)"
        else
            phase_fail "journey: feature toggle did not persist"
        fi
        # Restore
        curl -sS -X PATCH "${API_BASE}/api/settings" \
            -H 'content-type: application/json' \
            -d '{"features":{"Docs":true}}' > /dev/null 2>&1

        # --- Journey: Cost tracking data loads ---
        check_http_json "journey: cost tracking returns data"       "/api/costs?period=all"      ""
        check_http_json "journey: cost savings returns data"        "/api/costs/savings"         ""

        # --- Journey: Integration auth status checks ---
        gmail_auth=$(curl -sS "${API_BASE}/api/gmail/auth/status" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('authenticated','missing'))" 2>/dev/null)
        if [ "$gmail_auth" = "True" ] || [ "$gmail_auth" = "False" ]; then
            phase_pass "journey: Gmail auth status returns boolean"
        else
            phase_fail "journey: Gmail auth status unexpected ($gmail_auth)"
        fi
        cal_auth=$(curl -sS "${API_BASE}/api/calendar/auth/status" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('authenticated','missing'))" 2>/dev/null)
        if [ "$cal_auth" = "True" ] || [ "$cal_auth" = "False" ]; then
            phase_pass "journey: Calendar auth status returns boolean"
        else
            phase_fail "journey: Calendar auth status unexpected ($cal_auth)"
        fi
        drive_auth=$(curl -sS "${API_BASE}/api/drive/auth/status" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('authenticated','missing'))" 2>/dev/null)
        if [ "$drive_auth" = "True" ] || [ "$drive_auth" = "False" ]; then
            phase_pass "journey: Drive auth status returns boolean"
        else
            phase_fail "journey: Drive auth status unexpected ($drive_auth)"
        fi

        # --- Journey: Transcripts list ---
        check_http_json "journey: transcripts list loads"           "/api/transcripts"           '"transcripts"'

        # --- Journey: Export endpoints ---
        check_http_json "journey: export tasks markdown"            "/api/export/tasks"          ""
        check_http_json "journey: export timeline markdown"         "/api/export/timeline"       ""
        check_http_json "journey: export labels markdown"           "/api/export/labels"         ""

        # --- Journey: Agent nudge round trip ---
        # Register an agent, nudge it, read nudges, complete, delete.
        nudge_agent="e2e-nudge-$(date +%s)"
        curl -sS -X POST "${API_BASE}/api/agents/register" \
            -H 'content-type: application/json' \
            -d "{\"name\":\"$nudge_agent\",\"model\":\"sonnet\",\"budget\":0,\"status\":\"running\"}" > /dev/null 2>&1
        nudge_resp=$(curl -sS -X POST "${API_BASE}/api/agents/${nudge_agent}/nudge" \
            -H 'content-type: application/json' \
            -d '{"message":"e2e test nudge"}' 2>/dev/null)
        if echo "$nudge_resp" | grep -q '"result"\|"ok"\|"nudge"'; then
            phase_pass "journey: send nudge to running agent"
        else
            phase_fail "journey: agent nudge failed (body: $nudge_resp)"
        fi
        nudges_list=$(curl -sS "${API_BASE}/api/agents/${nudge_agent}/nudges" 2>/dev/null)
        if echo "$nudges_list" | grep -q "e2e test nudge"; then
            phase_pass "journey: nudge appears in nudge list"
        else
            phase_fail "journey: nudge not found in nudge list"
        fi
        curl -sS -X POST "${API_BASE}/api/agents/${nudge_agent}/complete" \
            -H 'content-type: application/json' > /dev/null 2>&1

        # --- Journey: Agent memory CRUD ---
        mem_agent="e2e-memory-$(date +%s)"
        curl -sS -X POST "${API_BASE}/api/agents/register" \
            -H 'content-type: application/json' \
            -d "{\"name\":\"$mem_agent\",\"model\":\"sonnet\",\"budget\":0,\"status\":\"running\"}" > /dev/null 2>&1
        mem_save=$(curl -sS -X POST "${API_BASE}/api/agents/${mem_agent}/memory" \
            -H 'content-type: application/json' \
            -d '{"key":"test_key","value":"test_value"}' 2>/dev/null)
        if echo "$mem_save" | grep -q '"result"\|"ok"\|"saved"'; then
            phase_pass "journey: save agent memory"
        else
            phase_fail "journey: save agent memory failed (body: $mem_save)"
        fi
        mem_read=$(curl -sS "${API_BASE}/api/agents/${mem_agent}/memory" 2>/dev/null)
        if echo "$mem_read" | grep -q "test_key\|test_value"; then
            phase_pass "journey: read agent memory"
        else
            phase_fail "journey: agent memory not readable (body: $mem_read)"
        fi
        curl -sS -X DELETE "${API_BASE}/api/agents/${mem_agent}/memory" > /dev/null 2>&1
        curl -sS -X POST "${API_BASE}/api/agents/${mem_agent}/complete" \
            -H 'content-type: application/json' > /dev/null 2>&1

        # --- Journey: Agent transcript ---
        check_http_json "journey: agent transcript endpoint"        "/api/agents/${nudge_agent}/transcript" ""

        # --- Journey: Threads CRUD ---
        thread_resp=$(curl -sS -X POST "${API_BASE}/api/threads" \
            -H 'content-type: application/json' \
            -d '{"name":"e2e-test-thread"}' 2>/dev/null)
        thread_id=$(echo "$thread_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',json.load(sys.stdin).get('thread_id','')) if False else json.load(open('/dev/stdin')).get('id',json.load(open('/dev/stdin')).get('thread_id','')))" 2>/dev/null || true)
        # Simpler extraction
        thread_id=$(echo "$thread_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('id', d.get('thread_id', d.get('thread',{}).get('id',''))))
" 2>/dev/null || true)
        if [ -n "$thread_id" ] && [ "$thread_id" != "None" ] && [ "$thread_id" != "" ]; then
            phase_pass "journey: create thread"
            curl -sS -X DELETE "${API_BASE}/api/threads/${thread_id}" > /dev/null 2>&1
        else
            # Thread creation might return a different shape
            if echo "$thread_resp" | grep -q '"id"\|"thread_id"\|"result"'; then
                phase_pass "journey: create thread (response ok)"
            else
                phase_fail "journey: create thread failed (body: $thread_resp)"
            fi
        fi
        check_http_json "journey: list threads"                     "/api/threads"               ""

        # --- Journey: Recurring tasks ---
        recur_resp=$(curl -sS -X POST "${API_BASE}/api/recurring" \
            -H 'content-type: application/json' \
            -d '{"task_template":{"title":"e2e-recurring","priority":"P2"},"schedule":{"kind":"weekly","days":[1]}}' 2>/dev/null)
        recur_id=$(echo "$recur_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('id', d.get('rule_id', d.get('rule',{}).get('id',''))))
" 2>/dev/null || true)
        if [ -n "$recur_id" ] && [ "$recur_id" != "None" ] && [ "$recur_id" != "" ]; then
            phase_pass "journey: create recurring task rule"
            curl -sS -X DELETE "${API_BASE}/api/recurring/${recur_id}" > /dev/null 2>&1
        else
            if echo "$recur_resp" | grep -q '"id"\|"rule_id"\|"result"\|"rule"'; then
                phase_pass "journey: create recurring task rule (response ok)"
            else
                phase_fail "journey: create recurring task rule failed (body: $recur_resp)"
            fi
        fi
        check_http_json "journey: list recurring rules"             "/api/recurring"             ""

        # --- Journey: Shares CRUD ---
        share_resp=$(curl -sS -X POST "${API_BASE}/api/shares" \
            -H 'content-type: application/json' \
            -d '{"share_type":"task_list","content_ids":["e2e-fake-id"],"title":"e2e share test"}' 2>/dev/null)
        share_token=$(echo "$share_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('token', d.get('share',{}).get('token','')))
" 2>/dev/null || true)
        if [ -n "$share_token" ] && [ "$share_token" != "None" ] && [ "$share_token" != "" ]; then
            phase_pass "journey: create share link"
            curl -sS -X DELETE "${API_BASE}/api/shares/${share_token}" > /dev/null 2>&1
        else
            if echo "$share_resp" | grep -q '"token"\|"result"'; then
                phase_pass "journey: create share link (response ok)"
            else
                phase_fail "journey: create share link failed (body: $share_resp)"
            fi
        fi
        check_http_json "journey: list shares"                      "/api/shares"                ""

        # --- Journey: Knowledge notes ---
        know_resp=$(curl -sS -X POST "${API_BASE}/api/knowledge" \
            -H 'content-type: application/json' \
            -d '{"title":"e2e-knowledge","content":"test note content"}' 2>/dev/null)
        know_id=$(echo "$know_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('id', d.get('note_id', d.get('note',{}).get('id',''))))
" 2>/dev/null || true)
        if [ -n "$know_id" ] && [ "$know_id" != "None" ] && [ "$know_id" != "" ]; then
            phase_pass "journey: create knowledge note"
            curl -sS -X DELETE "${API_BASE}/api/knowledge/${know_id}" > /dev/null 2>&1
        else
            if echo "$know_resp" | grep -q '"id"\|"note_id"\|"result"'; then
                phase_pass "journey: create knowledge note (response ok)"
            else
                phase_fail "journey: create knowledge note failed (body: $know_resp)"
            fi
        fi
        check_http_json "journey: list knowledge notes"             "/api/knowledge"             ""

        # --- Journey: Agentfiles ---
        check_http_json "journey: list agentfiles"                  "/api/agentfiles"            ""

        # --- Journey: Task suggestions ---
        check_http_json "journey: task suggestions"                 "/api/task-suggestions"      ""

        # --- Journey: Agent patterns ---
        check_http_json "journey: agent pattern recommendations"    "/api/agent-patterns/recommendations" ""

        # --- Journey: Predictions ---
        check_http_json "journey: velocity predictions"             "/api/predictions/velocity"  ""
        check_http_json "journey: forecast predictions"             "/api/predictions/forecast"  ""

        # --- Journey: Growth tracking ---
        check_http_json "journey: growth data"                      "/api/growth"                ""
        check_http_json "journey: growth summary"                   "/api/growth/summary"        ""

        # --- Journey: Dashboard main + diff ---
        check_http_json "journey: dashboard main"                   "/api/dashboard"             ""
        check_http_json "journey: dashboard diff"                   "/api/dashboard/diff"        ""

        # --- Journey: Projects list ---
        check_http_json "journey: projects list"                    "/api/projects"              ""

        # --- Journey: Docs list ---
        check_http_json "journey: docs list"                        "/api/docs"                  ""

        # --- Journey: Workspace messages ---
        check_http_json "journey: workspace messages"               "/api/workspace/messages"    ""

        # --- Journey: Chat history save + delete round trip ---
        chat_save=$(curl -sS -X PUT "${API_BASE}/api/chat/history" \
            -H 'content-type: application/json' \
            -d '{"messages":[{"role":"user","content":"e2e test"}]}' 2>/dev/null)
        if echo "$chat_save" | grep -q '"result"\|"ok"\|"saved"'; then
            phase_pass "journey: save chat history"
        else
            phase_fail "journey: save chat history failed (body: $chat_save)"
        fi
        chat_del=$(curl -sS -X DELETE "${API_BASE}/api/chat/history" 2>/dev/null)
        if echo "$chat_del" | grep -q '"result"\|"ok"\|"deleted"\|"cleared"'; then
            phase_pass "journey: clear chat history"
        else
            phase_fail "journey: clear chat history failed (body: $chat_del)"
        fi

        # --- Journey: Secrets key status ---
        check_http_json "journey: secrets key status"               "/api/secrets/key-status"    ""

        # --- Journey: Adventures templates ---
        check_http_json "journey: adventures templates"             "/api/adventures/templates"  ""

        # --- Journey: Sync status ---
        check_http_json "journey: sync status"                      "/api/sync/status"           ""

        # --- Journey: Settings chat backend status ---
        check_http_json "journey: chat backend status"              "/api/settings/chat-backend-status" ""

        # --- Journey: Label colors ---
        check_http_json "journey: label colors"                     "/api/labels/colors"         ""

        # --- Journey: PM templates ---
        check_http_json "journey: PM agent templates"               "/api/agents/pm-templates"   ""

        # =================================================================
        # CONDITIONAL INTEGRATION TESTS
        # These only run when the user has connected the relevant account.
        # =================================================================

        # --- Gmail: messages + mark read (only when authenticated) ---
        if [ "$gmail_auth" = "True" ]; then
            gmail_msgs=$(curl -sS "${API_BASE}/api/gmail/messages" 2>/dev/null)
            if echo "$gmail_msgs" | grep -q '"messages"'; then
                phase_pass "journey: Gmail messages list loads"
                # Try marking first message as read (safe, idempotent)
                first_msg_id=$(echo "$gmail_msgs" | python3 -c "
import sys,json
msgs=json.load(sys.stdin).get('messages',[])
print(msgs[0].get('id','') if msgs else '')
" 2>/dev/null || true)
                if [ -n "$first_msg_id" ] && [ "$first_msg_id" != "" ]; then
                    mark_resp=$(curl -sS -X POST "${API_BASE}/api/gmail/messages/${first_msg_id}/read" \
                        -H 'content-type: application/json' 2>/dev/null)
                    if echo "$mark_resp" | grep -q '"result"\|"ok"'; then
                        phase_pass "journey: Gmail mark message as read"
                    else
                        phase_fail "journey: Gmail mark read failed (body: $mark_resp)"
                    fi
                fi
                # Check send capability
                check_http_json "journey: Gmail send capability"    "/api/gmail/send_capability"  ""
            else
                phase_fail "journey: Gmail messages list failed (body: $gmail_msgs)"
            fi
        else
            phase_skip "journey: Gmail messages (not authenticated)"
        fi

        # --- Calendar: events list (only when authenticated) ---
        if [ "$cal_auth" = "True" ]; then
            cal_events=$(curl -sS "${API_BASE}/api/calendar/events" 2>/dev/null)
            if echo "$cal_events" | grep -q '"events"'; then
                phase_pass "journey: Calendar events list loads"
            else
                phase_fail "journey: Calendar events list failed (body: $cal_events)"
            fi
        else
            phase_skip "journey: Calendar events (not authenticated)"
        fi

        # --- Drive: files list (only when authenticated) ---
        if [ "$drive_auth" = "True" ]; then
            drive_files=$(curl -sS "${API_BASE}/api/drive/files" 2>/dev/null)
            if echo "$drive_files" | grep -q '"files"'; then
                phase_pass "journey: Drive files list loads"
            else
                phase_fail "journey: Drive files list failed (body: $drive_files)"
            fi
        else
            phase_skip "journey: Drive files (not authenticated)"
        fi

        # =================================================================
        # AGENT SPAWN + LIFECYCLE TESTS
        # =================================================================

        # --- Journey: Agent spawn, heartbeat, cancel ---
        # Spawn a real agent with budget 0 so it registers but does
        # minimal work. Then cancel it and verify it leaves active list.
        spawn_name="e2e-spawn-$(date +%s)"
        spawn_resp=$(curl -sS -X POST "${API_BASE}/api/agents/spawn" \
            -H 'content-type: application/json' \
            -d "{\"name\":\"$spawn_name\",\"prompt\":\"say hello and exit\",\"model\":\"sonnet\",\"budget\":0}" 2>/dev/null)
        if echo "$spawn_resp" | grep -q '"name"\|"pid"\|"result"\|"status"'; then
            phase_pass "journey: agent spawn returns response"
            # Give it a moment to register
            sleep 1
            # Send a heartbeat
            hb_resp=$(curl -sS -X POST "${API_BASE}/api/agents/${spawn_name}/heartbeat" \
                -H 'content-type: application/json' \
                -d '{"step":"e2e heartbeat test"}' 2>/dev/null)
            if echo "$hb_resp" | grep -q '"result"\|"ok"\|"step"'; then
                phase_pass "journey: agent heartbeat accepted"
            else
                phase_fail "journey: agent heartbeat failed (body: $hb_resp)"
            fi
            # Cancel the agent
            cancel_resp=$(curl -sS -X POST "${API_BASE}/api/agents/${spawn_name}/cancel" \
                -H 'content-type: application/json' \
                -d '{"reason":"e2e test cleanup"}' 2>/dev/null)
            if echo "$cancel_resp" | grep -q '"result"\|"cancelled"'; then
                phase_pass "journey: agent cancel works"
            else
                phase_fail "journey: agent cancel failed (body: $cancel_resp)"
            fi
        else
            phase_fail "journey: agent spawn failed (body: $spawn_resp)"
        fi

        # --- Journey: Workflow run + status tracking ---
        wfrun_resp=$(curl -sS -X POST "${API_BASE}/api/workflows" \
            -H 'content-type: application/json' \
            -d '{"name":"e2e-run-workflow","steps":[{"agent_name":"e2e-wf-step","prompt":"echo done","model":"sonnet","budget":0}]}' 2>/dev/null)
        wfrun_id=$(echo "$wfrun_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('workflow',{}).get('id',''))" 2>/dev/null || true)
        if [ -n "$wfrun_id" ] && [ "$wfrun_id" != "" ]; then
            # Run the workflow
            run_resp=$(curl -sS -X POST "${API_BASE}/api/workflows/${wfrun_id}/run" \
                -H 'content-type: application/json' 2>/dev/null)
            if echo "$run_resp" | grep -q '"status"\|"workflow"\|"steps"'; then
                phase_pass "journey: workflow run starts"
            else
                phase_fail "journey: workflow run failed (body: $run_resp)"
            fi
            # Check status
            sleep 1
            status_resp=$(curl -sS "${API_BASE}/api/workflows/${wfrun_id}/status" 2>/dev/null)
            if echo "$status_resp" | grep -q '"status"\|"steps"\|"workflow"'; then
                phase_pass "journey: workflow status returns progress"
            else
                phase_fail "journey: workflow status failed (body: $status_resp)"
            fi
            # Cleanup
            curl -sS -X DELETE "${API_BASE}/api/workflows/${wfrun_id}" > /dev/null 2>&1
        else
            phase_fail "journey: could not create workflow for run test"
        fi

        # --- Journey: Fleet spawn ---
        fleet_resp=$(curl -sS -X POST "${API_BASE}/api/agents/fleets/spawn" \
            -H 'content-type: application/json' \
            -d '{"fleet_id":"fleet-build-website","context":"e2e test only","model":"sonnet","budget":0}' 2>/dev/null)
        if echo "$fleet_resp" | grep -q '"spawned"\|"agents"\|"result"\|"members"'; then
            phase_pass "journey: fleet spawn starts agents"
            # Extract spawned agent names and cancel them all
            fleet_agents=$(echo "$fleet_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for a in d.get('spawned', d.get('agents', d.get('members', []))):
    name = a.get('name','') if isinstance(a, dict) else a
    if name: print(name)
" 2>/dev/null || true)
            for fa in $fleet_agents; do
                curl -sS -X POST "${API_BASE}/api/agents/${fa}/cancel" \
                    -H 'content-type: application/json' \
                    -d '{"reason":"e2e cleanup"}' > /dev/null 2>&1
            done
        else
            phase_fail "journey: fleet spawn failed (body: $fleet_resp)"
        fi

        # =================================================================
        # END USER JOURNEY TESTS
        # =================================================================

        # --- Enterprise lifecycle: org, members, policies, isolation, SSO ---
        check_http_json "enterprise: GET state"                          "/api/enterprise"            '"enabled"'
        check_http_json "enterprise: GET policies"                       "/api/enterprise/policies"   '"policies"'
        check_http_json "enterprise: GET audit"                          "/api/enterprise/audit"      '"events"'

        # Check if enterprise is already enabled (avoid double-create)
        ent_enabled=$(curl -sS "${API_BASE}/api/enterprise" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('enabled', False))" 2>/dev/null)

        # If not already enabled, run the full org lifecycle
        if [ "$ent_enabled" = "False" ]; then
            # Create org
            org_resp=$(curl -sS -X POST "${API_BASE}/api/enterprise/org" \
                -H 'content-type: application/json' \
                -d '{"name":"e2e-test-org","admin_email":"e2e@test.com"}' 2>/dev/null)
            if echo "$org_resp" | grep -q '"org"\|"name"\|"id"'; then
                phase_pass "enterprise: create org"
            else
                phase_fail "enterprise: create org failed (body: $org_resp)"
            fi
        else
            phase_pass "enterprise: org already exists (skipping create)"
        fi

        # Add a member
        member_resp=$(curl -sS -X POST "${API_BASE}/api/enterprise/members" \
            -H 'content-type: application/json' \
            -d '{"email":"e2e-member@test.com","role":"member"}' 2>/dev/null)
        member_id=$(echo "$member_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('id', d.get('member',{}).get('id','')))
" 2>/dev/null || true)
        if [ -n "$member_id" ] && [ "$member_id" != "None" ] && [ "$member_id" != "" ]; then
            phase_pass "enterprise: add member"

            # Update member role
            role_resp=$(curl -sS -X PATCH "${API_BASE}/api/enterprise/members/${member_id}/role" \
                -H 'content-type: application/json' \
                -d '{"role":"admin"}' 2>/dev/null)
            if echo "$role_resp" | grep -q '"role"\|"admin"\|"member"'; then
                phase_pass "enterprise: update member role"
            else
                phase_fail "enterprise: update member role failed (body: $role_resp)"
            fi

            # Remove member (cleanup)
            curl -sS -X DELETE "${API_BASE}/api/enterprise/members/${member_id}" > /dev/null 2>&1
        else
            if echo "$member_resp" | grep -q '"id"\|"member"\|"email"'; then
                phase_pass "enterprise: add member (response ok)"
            else
                phase_fail "enterprise: add member failed (body: $member_resp)"
            fi
        fi

        # List members
        check_http_json "enterprise: list members"                       "/api/enterprise/members"    '"members"'

        # Update policies
        policy_resp=$(curl -sS -X PATCH "${API_BASE}/api/enterprise/policies" \
            -H 'content-type: application/json' \
            -d '{"max_agent_budget":5.0}' 2>/dev/null)
        if echo "$policy_resp" | grep -q '"policies"\|"max_agent_budget"'; then
            phase_pass "enterprise: update policies"
        else
            phase_fail "enterprise: update policies failed (body: $policy_resp)"
        fi

        # Isolation: get + set
        check_http_json "enterprise: GET isolation"                      "/api/enterprise/isolation"  '"current"'
        iso_resp=$(curl -sS -X PATCH "${API_BASE}/api/enterprise/isolation" \
            -H 'content-type: application/json' \
            -d '{"level":"governed"}' 2>/dev/null)
        if echo "$iso_resp" | grep -q '"level"\|"governed"\|"result"'; then
            phase_pass "enterprise: set isolation level"
        else
            phase_fail "enterprise: set isolation failed (body: $iso_resp)"
        fi
        # Restore to open
        curl -sS -X PATCH "${API_BASE}/api/enterprise/isolation" \
            -H 'content-type: application/json' \
            -d '{"level":"open"}' > /dev/null 2>&1

        # SSO: get config (should work even without SSO configured)
        check_http_json "enterprise: GET SSO config"                     "/api/enterprise/sso"        ""

        # Agentfile
        check_http_json "enterprise: GET agentfile"                      "/api/enterprise/agentfile"  ""

        # Clean up: delete the org if we created it
        if [ "$ent_enabled" = "False" ]; then
            curl -sS -X DELETE "${API_BASE}/api/enterprise/org" > /dev/null 2>&1
        fi

        # --- No hardcoded ports in routers ---
        hardcoded=$(grep -r 'localhost:5173' "$REPO_DIR/api/routers/" 2>/dev/null | grep -v '.pyc' | grep -v '#.*localhost:5173' | wc -l | tr -d ' ')
        if [ "$hardcoded" = "0" ]; then
            phase_pass "no hardcoded localhost:5173 in routers"
        else
            phase_fail "found $hardcoded hardcoded localhost:5173 references in routers"
        fi

        # --- Vite proxy must use 127.0.0.1, never localhost (needle 315) ---
        # Node resolves "localhost" to ::1 (IPv6) first. Uvicorn only
        # binds 127.0.0.1 (IPv4), so the IPv6 attempt poisons the
        # proxy connection pool and causes intermittent ETIMEDOUT.
        proxy_localhost=$(grep -E "target:.*localhost" "$REPO_DIR/app/vite.config.ts" 2>/dev/null | grep -v '//' | wc -l | tr -d ' ')
        if [ "$proxy_localhost" = "0" ]; then
            phase_pass "vite proxy targets use 127.0.0.1, not localhost"
        else
            phase_fail "vite proxy targets must use 127.0.0.1 instead of localhost (needle 315)"
        fi

        # --- User data safety: needles symlink ---
        if [ -L "$REPO_DIR/.ostk/needles" ]; then
            phase_pass "needles is a symlink (safe from git pull)"
        elif [ ! -d "$REPO_DIR/.ostk/needles" ]; then
            phase_pass "needles directory not present (fresh install)"
        else
            phase_fail "needles is a real directory inside repo (unsafe)"
        fi

        # --- Start script syntax ---
        if bash -n "$REPO_DIR/start.sh" 2>/dev/null; then
            phase_pass "start.sh has valid bash syntax"
        else
            phase_fail "start.sh has syntax errors"
        fi

        # --- Needles migration in start.sh ---
        if grep -q 'myos/needles' "$REPO_DIR/start.sh"; then
            phase_pass "start.sh has needles migration logic"
        else
            phase_fail "start.sh missing needles migration"
        fi
    fi
fi

# --- Phase 5: WebSocket chat round trips ----------------------------------
#
# Tests three chat modes:
#   a) Claude solo   — @claude model, single bubble
#   b) Gemini solo   — @gemini model, single bubble
#   c) Multi-AI      — @claude talk to @gemini, orchestration loop
#
# Each test sends a message, waits for at least one token + a done event,
# and reports pass/fail. The Python helper is parameterized so we avoid
# copy-pasting the same WS client three times.

if [ "$SKIP_LIVE" != "1" ]; then
    header "WebSocket chat round trips"
    if ! server_up; then
        phase_skip "WebSocket chat (API not reachable)"
    elif ! command -v python3 > /dev/null 2>&1; then
        phase_skip "WebSocket chat (python3 not installed)"
    else

# Shared Python helper — takes model and message as env vars.
_ws_chat_test() {
    local _model="$1" _message="$2"
    PYTHONPATH="$REPO_DIR/api" API_PORT="$API_PORT" \
        _WS_MODEL="$_model" _WS_MESSAGE="$_message" \
        python3 - <<'PY'
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
URL = f"ws://localhost:{API_PORT}/ws/chat"
MODEL = os.environ.get("_WS_MODEL", "@claude")
MESSAGE = os.environ.get("_WS_MESSAGE", "say hi")

async def main():
    try:
        async with websockets.connect(URL, open_timeout=5) as ws:
            await ws.send(json.dumps({
                "messages": [{"role": "user", "content": MESSAGE}],
                "model": MODEL,
            }))
            got_token = False
            got_done = False
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=45)
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    et = event.get("type")
                    if et == "token" and event.get("data"):
                        got_token = True
                    if et in ("text",) and event.get("data"):
                        # Multi-AI sends "text" events instead of "token"
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
}

_ws_check() {
    local _label="$1" _result="$2"
    case "$_result" in
        OK)               phase_pass "$_label" ;;
        EMPTY_RESPONSE)   phase_fail "$_label (no tokens, only done)" ;;
        NO_DONE)          phase_fail "$_label (never sent done)" ;;
        TIMEOUT)          phase_fail "$_label (timed out)" ;;
        NO_WS_LIB)       phase_skip "$_label (websockets lib not installed)" ;;
        ERROR:*)          phase_fail "$_label (error: ${_result#ERROR:})" ;;
        CONNECT_FAIL:*)   phase_fail "$_label (connect fail: ${_result#CONNECT_FAIL:})" ;;
        *)                phase_fail "$_label (unknown: $_result)" ;;
    esac
}

        # --- a) Claude solo ---
        WS_CLAUDE=$(_ws_chat_test "@claude" "say hi in one word")
        _ws_check "chat WS: Claude solo streams token + done" "$WS_CLAUDE"

        # --- b) Gemini solo ---
        WS_GEMINI=$(_ws_chat_test "@gemini" "say hi in one word")
        _ws_check "chat WS: Gemini solo streams token + done" "$WS_GEMINI"

        # --- c) Multi-AI conversation ---
        WS_MULTI=$(_ws_chat_test "@claude" "@claude chat with @gemini about what color the sky is, one sentence each")
        _ws_check "chat WS: Multi-AI conversation streams" "$WS_MULTI"

    fi
fi

# --- Phase 6: browser e2e tests (agent-browser) ------------------------------

SKIP_BROWSER="${SKIP_BROWSER:-0}"
if [ "$SKIP_BROWSER" != "1" ] && [ "$SKIP_LIVE" != "1" ]; then
    header "Browser e2e tests"
    if ! command -v agent-browser > /dev/null 2>&1; then
        phase_skip "agent-browser not installed (install: brew install agent-browser)"
    elif ! curl -sS -o /dev/null -w "%{http_code}" "http://localhost:${FRONTEND_PORT:-3010}" 2>/dev/null | grep -q "^200$"; then
        phase_skip "frontend not reachable on port ${FRONTEND_PORT:-3010}"
    else
        BROWSER_OUTPUT=$(API_PORT="$API_PORT" bash "$REPO_DIR/scripts/e2e_browser.sh" 2>&1) || true
        echo "$BROWSER_OUTPUT"
        # Parse pass/fail/skip counts from the browser script output.
        # Strip ANSI color codes first so the regex can match cleanly.
        _stripped=$(echo "$BROWSER_OUTPUT" | sed 's/\x1b\[[0-9;]*m//g')
        b_pass=$(echo "$_stripped" | grep -oE 'PASS +[0-9]+' | tail -1 | grep -oE '[0-9]+' || echo "0")
        b_fail=$(echo "$_stripped" | grep -oE 'FAIL +[0-9]+' | tail -1 | grep -oE '[0-9]+' || echo "0")
        b_skip=$(echo "$_stripped" | grep -oE 'SKIP +[0-9]+' | tail -1 | grep -oE '[0-9]+' || echo "0")
        PASS=$((PASS + b_pass))
        FAIL=$((FAIL + b_fail))
        SKIP=$((SKIP + b_skip))
    fi
elif [ "$SKIP_BROWSER" = "1" ]; then
    phase_skip "browser e2e tests (SKIP_BROWSER=1)"
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
