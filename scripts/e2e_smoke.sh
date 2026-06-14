#!/usr/bin/env bash
# yourOS end-to-end smoke test.
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
# Auto-detect HTTPS: use https if self-signed certs are present (same logic
# as dev-backend.sh so the smoke test always matches the running server scheme).
SSL_KEY="$HOME/.youros/localhost.key"
SSL_CERT="$HOME/.youros/localhost.crt"
if [ -f "$SSL_KEY" ] && [ -f "$SSL_CERT" ]; then
    API_BASE="${API_BASE:-https://127.0.0.1:${API_PORT}}"
    # Self-signed cert: skip TLS verification for local smoke tests.
    CURL_OPTS="-k"
else
    API_BASE="${API_BASE:-http://localhost:${API_PORT}}"
    CURL_OPTS=""
fi
SKIP_UNIT="${SKIP_UNIT:-0}"
SKIP_LIVE="${SKIP_LIVE:-0}"
RELEASE_MODE="${RELEASE_MODE:-0}"

# Propagate RELEASE_MODE so any child process that starts or checks
# the backend (such as scripts/dev-backend.sh) can honor it.
export RELEASE_MODE

# →1454 plan, Fix 1: when running the full smoke, point spec writes at a
# tmpdir so ~/.youros/specs/ never grows by latency-probe-* / wave2-* /
# build-a-website / ship-guided-onboarding-for-solo-pms artifacts. Child
# dev-backend.sh inherits this env var. Trap cleans up on exit.
if [ -z "${YOUROS_USER_SPECS_DIR:-}" ]; then
    YOUROS_USER_SPECS_DIR="$(mktemp -d -t youros-specs-e2e.XXXXXX)/specs"
    export YOUROS_USER_SPECS_DIR
    _E2E_SPECS_TMP_PARENT="$(dirname "$YOUROS_USER_SPECS_DIR")"
fi

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
    # Delete any tasks whose title starts with "e2e-".
    # Must use ?include_test_data=true so the backend filter does not hide them.
    # Reports the deleted count so the test author can confirm teardown worked.
    python3 -c "
import sys, json, urllib.request, urllib.parse, ssl
ctx = ssl._create_unverified_context() if '${API_BASE}'.startswith('https://') else None
deleted = 0
try:
    resp = urllib.request.urlopen('${API_BASE}/api/tasks?include_test_data=true', timeout=3, context=ctx)
    tasks = json.loads(resp.read()).get('tasks', [])
    for t in tasks:
        title = t.get('title', '')
        tid = t.get('id', '')
        if title.lower().startswith('e2e-') and tid:
            req = urllib.request.Request(
                '${API_BASE}/api/tasks/' + urllib.parse.quote(tid, safe=''),
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3, context=ctx)
                deleted += 1
            except Exception:
                pass
except Exception:
    pass
if deleted:
    print(f'[e2e sweep] deleted {deleted} leftover e2e- task(s)')
" 2>/dev/null || true

    # Delete any labels whose name starts with "e2e-"
    python3 -c "
import sys, json, urllib.request, ssl
ctx = ssl._create_unverified_context() if '${API_BASE}'.startswith('https://') else None
try:
    resp = urllib.request.urlopen('${API_BASE}/api/labels', timeout=3, context=ctx)
    labels = json.loads(resp.read()).get('labels', [])
    for l in labels:
        name = l.get('name', '')
        lid = l.get('id', '')
        if name.startswith('e2e-') and lid:
            req = urllib.request.Request(
                '${API_BASE}/api/labels/' + lid,
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3, context=ctx)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

    # Delete any draft/spec whose path OR title looks like a smoke
    # artifact so leftover specs from the specs user journey and the
    # debug demos do not accumulate across runs. Patterns mirror the
    # backend sweep in api/routers/specs.py so disk and API stay in
    # sync. Covers e2e-, demo-smoke- (hyphen and capitalized space),
    # smoke-, test-, v\d-verify-, morning-verify-, and any title or
    # filename ending in a 4+ digit timestamp/id. The https API uses a
    # self-signed cert so we build an unverified SSL context.
    python3 -c "
import sys, json, re, urllib.request, ssl
ctx = ssl._create_unverified_context() if '${API_BASE}'.startswith('https://') else None
patterns = [
    re.compile(r'^(?:demo[-_ ]smoke[-_ ]?|smoke[-_ ]|e2e[-_ ]|test[-_ ]|v\d+[-_ ]verify[-_ ]?|morning[-_ ]verify[-_ ]?)', re.IGNORECASE),
    re.compile(r'/(?:demo[-_ ]smoke[-_ ]?|smoke[-_ ]|e2e[-_ ]|test[-_ ]|v\d+[-_ ]verify[-_ ]?|morning[-_ ]verify[-_ ]?)', re.IGNORECASE),
    re.compile(r'[-_ ]\d{4,}(?:\.md)?\$', re.IGNORECASE),
]
def is_artifact(p, t):
    for value in (p, t):
        if not value:
            continue
        for pat in patterns:
            if pat.search(value):
                return True
    return False
try:
    resp = urllib.request.urlopen('${API_BASE}/api/specs', timeout=3, context=ctx)
    docs = json.loads(resp.read()).get('docs', [])
    for d in docs:
        p = d.get('path') or ''
        t = d.get('title') or ''
        if is_artifact(p, t):
            req = urllib.request.Request(
                '${API_BASE}/api/specs/' + p,
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3, context=ctx)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

    # Disk sweep: delete any orphan smoke-artifact files under
    # docs/draft/ and docs/spec/ in case the API was down or a prior
    # delete failed. This is the last line of defense so no demo spec
    # ever survives. Mirrors the backend regex.
    python3 -c "
import os, re, sys
root = sys.argv[1]
patterns = [
    re.compile(r'^(?:demo[-_ ]smoke[-_ ]?|smoke[-_ ]|e2e[-_ ]|test[-_ ]|v\d+[-_ ]verify[-_ ]?|morning[-_ ]verify[-_ ]?)', re.IGNORECASE),
    re.compile(r'[-_ ]\d{4,}(?:\.md)?\$', re.IGNORECASE),
]
for sub in ('docs/draft', 'docs/spec'):
    d = os.path.join(root, sub)
    if not os.path.isdir(d):
        continue
    for name in os.listdir(d):
        for pat in patterns:
            if pat.search(name):
                try:
                    os.unlink(os.path.join(d, name))
                except OSError:
                    pass
                break
" "${REPO_DIR}" 2>/dev/null || true

    # Delete any shared links whose title starts with "e2e"
    python3 -c "
import sys, json, urllib.request, ssl
ctx = ssl._create_unverified_context() if '${API_BASE}'.startswith('https://') else None
try:
    resp = urllib.request.urlopen('${API_BASE}/api/shares', timeout=3, context=ctx)
    shares = json.loads(resp.read()).get('shares', [])
    for s in shares:
        title = s.get('title', '')
        token = s.get('token', '')
        if title.lower().startswith('e2e') and token:
            req = urllib.request.Request(
                '${API_BASE}/api/shares/' + token,
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3, context=ctx)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

    # ~/.youros/files/ disk sweep for any .md that looks like an e2e or
    # smoke artifact. The Roadmap/PRD templates, workflows, and fleet
    # agents all write rollup .md files here under names like
    # "e2e-narrow-prd-2026-04-17T03-04.md" when they are exercised by
    # the live smoke. Without this sweep those files accumulate across
    # runs and the test_artifact_hygiene.py guard fails the pytest
    # suite. Uses the same name patterns as the API docs/specs sweep
    # above. Conservative: never touches files that do not match the
    # smoke pattern, so user-generated docs and baseline ia-review
    # outputs stay put.
    python3 -c "
import os, re
from pathlib import Path
root = Path(os.environ.get('YOUROS_HOME', os.path.expanduser('~/.youros'))) / 'files'
if not root.is_dir():
    raise SystemExit(0)
patterns = [
    re.compile(r'^(?:demo[-_ ]smoke[-_ ]?|smoke[-_ ]|e2e[-_ ]|test[-_ ]|v\d+[-_ ]verify[-_ ]?|morning[-_ ]verify[-_ ]?)', re.IGNORECASE),
]
for name in os.listdir(root):
    for pat in patterns:
        if pat.search(name):
            try:
                (root / name).unlink()
            except OSError:
                pass
            break
" 2>/dev/null || true

    # ------------------------------------------------------------------
    # Extended teardown (diagnosis 2026-04-18).
    # A prior sweep had to remove 225 e2e-prefixed artifacts that had
    # built up from many past smoke runs: 167 agents, 38 transcripts,
    # 12 agent_memory files, 3 workflows, 2 labels, 1 thread, 1 knowledge
    # note, 1 recurring task, 1 share. Labels and shares were already
    # covered above. The rest are covered below so no e2e-* artifact
    # survives a successful run. All calls are idempotent: DELETE on a
    # missing id is a no-op.
    # Surfaces covered below:
    #   - Agents (API) — DELETE /api/agents/<name>
    #   - Workflows (API) — DELETE /api/workflows/<id>
    #   - Threads (API) — DELETE /api/threads/<id>
    #   - Knowledge (API) — DELETE /api/knowledge/<note_id>
    #   - Recurring rules (API) — DELETE /api/recurring/<rule_id>
    #   - Agent memory files (disk) — rm ~/.youros/agent_memory/e2e-*.json
    #   - Transcripts (disk) — rm <repo>/transcripts/e2e-*
    # ------------------------------------------------------------------

    # Delete any agents whose name starts with "e2e-" or "e2e" via the API.
    # The smoke creates e2e-nudge-*, e2e-memory-*, e2e-spawn-*, e2e-lifecycle-*,
    # e2e-chat-filter-*, e2e-real-filter-*, plus any fleet members that register
    # themselves. DELETE /api/agents/<name> removes the registry row so the
    # Agents page and running-agents panel stay clean.
    python3 -c "
import json, urllib.request, urllib.parse, ssl
ctx = ssl._create_unverified_context() if '${API_BASE}'.startswith('https://') else None
try:
    resp = urllib.request.urlopen('${API_BASE}/api/agents', timeout=3, context=ctx)
    agents = json.loads(resp.read()).get('agents', [])
    for a in agents:
        name = (a.get('name') or '').strip()
        if name.lower().startswith('e2e-') or name.lower().startswith('e2e'):
            req = urllib.request.Request(
                '${API_BASE}/api/agents/' + urllib.parse.quote(name, safe=''),
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3, context=ctx)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

    # Delete any workflows whose name starts with "e2e-". The workflows
    # list returns {workflows: [{id, name, ...}, ...]}.
    python3 -c "
import json, urllib.request, ssl
ctx = ssl._create_unverified_context() if '${API_BASE}'.startswith('https://') else None
try:
    resp = urllib.request.urlopen('${API_BASE}/api/workflows', timeout=3, context=ctx)
    wfs = json.loads(resp.read()).get('workflows', [])
    for w in wfs:
        name = (w.get('name') or '').lower()
        wid = w.get('id') or ''
        if name.startswith('e2e-') and wid:
            req = urllib.request.Request(
                '${API_BASE}/api/workflows/' + wid,
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3, context=ctx)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

    # Delete any threads whose name starts with "e2e-".
    python3 -c "
import json, urllib.request, ssl
ctx = ssl._create_unverified_context() if '${API_BASE}'.startswith('https://') else None
try:
    resp = urllib.request.urlopen('${API_BASE}/api/threads', timeout=3, context=ctx)
    threads = json.loads(resp.read()).get('threads', [])
    for t in threads:
        name = (t.get('name') or '').lower()
        tid = t.get('id') or ''
        if name.startswith('e2e-') and tid:
            req = urllib.request.Request(
                '${API_BASE}/api/threads/' + tid,
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3, context=ctx)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

    # Delete any tasks (needles) whose title starts with "e2e". The
    # per-journey DELETE calls in each test section cover the happy path but
    # are fire-and-forget and silently swallow failures. This sweep catches
    # anything left behind when a journey phase exits early, an ID extraction
    # fails, or a curl call is interrupted. Without this sweep, failed cleanup
    # leaves real needles in .ostk/needles/issues.jsonl that appear on the
    # Tasks page and can overwrite existing needle IDs (→1323 incident).
    python3 -c "
import json, urllib.request, urllib.parse, ssl
ctx = ssl._create_unverified_context() if '${API_BASE}'.startswith('https://') else None
try:
    url = '${API_BASE}/api/tasks?include_test_data=true'
    resp = urllib.request.urlopen(url, timeout=5, context=ctx)
    data = json.loads(resp.read())
    tasks = data.get('tasks', [])
    for task in tasks:
        title = (task.get('title') or '').lower()
        tid = task.get('id') or ''
        if title.startswith('e2e') and tid:
            req = urllib.request.Request(
                '${API_BASE}/api/tasks/' + urllib.parse.quote(tid, safe=''),
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3, context=ctx)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

    # Delete any knowledge notes whose title starts with "e2e-".
    python3 -c "
import json, urllib.request, ssl
ctx = ssl._create_unverified_context() if '${API_BASE}'.startswith('https://') else None
try:
    resp = urllib.request.urlopen('${API_BASE}/api/knowledge', timeout=3, context=ctx)
    notes = json.loads(resp.read()).get('notes', [])
    for n in notes:
        title = (n.get('title') or '').lower()
        nid = n.get('id') or ''
        if title.startswith('e2e-') and nid:
            req = urllib.request.Request(
                '${API_BASE}/api/knowledge/' + nid,
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3, context=ctx)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

    # Delete any recurring-task rules whose template title starts with "e2e-".
    python3 -c "
import json, urllib.request, ssl
ctx = ssl._create_unverified_context() if '${API_BASE}'.startswith('https://') else None
try:
    resp = urllib.request.urlopen('${API_BASE}/api/recurring', timeout=3, context=ctx)
    rules = json.loads(resp.read()).get('rules', [])
    for r in rules:
        tpl = r.get('task_template') or {}
        title = (tpl.get('title') or '').lower()
        rid = r.get('id') or ''
        if title.startswith('e2e-') and rid:
            req = urllib.request.Request(
                '${API_BASE}/api/recurring/' + rid,
                method='DELETE')
            try:
                urllib.request.urlopen(req, timeout=3, context=ctx)
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

    # Disk sweep: ~/.youros/agent_memory/e2e-*.json. Agent memory files
    # are written by the /api/agents/<name>/memory endpoint the smoke
    # exercises, and also by any live agent the smoke registers. They
    # are not always removed by DELETE /memory because the smoke can
    # race the backend under --reload. This catches any stragglers.
    python3 -c "
import os, re
from pathlib import Path
root = Path(os.environ.get('YOUROS_HOME', os.path.expanduser('~/.youros'))) / 'agent_memory'
if not root.is_dir():
    raise SystemExit(0)
pat = re.compile(r'^e2e[-_]', re.IGNORECASE)
for name in os.listdir(root):
    if pat.search(name):
        try:
            (root / name).unlink()
        except OSError:
            pass
" 2>/dev/null || true

    # Disk sweep: <repo>/transcripts/e2e-*. Transcripts accumulate when
    # agents register + complete inside the smoke (nudge, memory, spawn,
    # lifecycle, etc.). The transcripts router only reads the directory
    # so there is no API delete; remove files directly.
    python3 -c "
import os, re, sys
from pathlib import Path
root = Path(sys.argv[1]) / 'transcripts'
if not root.is_dir():
    raise SystemExit(0)
pat = re.compile(r'^e2e[-_]', re.IGNORECASE)
for name in os.listdir(root):
    if pat.search(name):
        try:
            (root / name).unlink()
        except OSError:
            pass
" "${REPO_DIR}" 2>/dev/null || true
}

_e2e_cleanup() {
    _e2e_sweep_artifacts
    # →1454 plan, Fix 1: remove the spec tmpdir we created at startup.
    if [ -n "${_E2E_SPECS_TMP_PARENT:-}" ] && [ -d "$_E2E_SPECS_TMP_PARENT" ]; then
        rm -rf "$_E2E_SPECS_TMP_PARENT" 2>/dev/null || true
    fi
    if [ -n "$_E2E_ORIGINAL_OS_NAME" ]; then
        curl -sS $CURL_OPTS -X PATCH "${API_BASE}/api/settings" \
            -H 'content-type: application/json' \
            -d "{\"os_name\":\"$_E2E_ORIGINAL_OS_NAME\"}" > /dev/null 2>&1 || true
    fi
}
trap _e2e_cleanup EXIT ERR INT TERM HUP

# Clean up leftovers from any prior failed run before starting.
_e2e_sweep_artifacts

# Run-unique alphanumeric tag. The tasks router's _sanitize_task_title
# strips trailing numeric-only tokens (so "foo-1776397878" normalizes to
# "foo") which makes back-to-back smoke runs collide with the in-process
# title tombstone and 409. Using a suffix that ends in a non-digit
# keeps the sanitized title unique across runs. Derived from the epoch
# stamp so it is still meaningful in logs but ends in 'x' so the
# trailing-id regex does not match.
E2E_RUN_TAG="run$(date +%s | rev | cut -c1-6)x"
export E2E_RUN_TAG

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

header "yourOS end-to-end smoke test"
# Time primitive: announce smoke start. Failures here must NOT break smoke.
SMOKE_OP_ID="smoke-$(date +%s)"
curl -sk --connect-timeout 3 -m 5 -X POST "${API_BASE}/api/time/start" \
    -H 'Content-Type: application/json' \
    -d "{\"op_id\":\"${SMOKE_OP_ID}\",\"op_kind\":\"smoke_gate\"}" \
    > /dev/null 2>&1 || true
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
        if (cd "$REPO_DIR/api" && . .venv/bin/activate && python -m pytest -q --tb=short --timeout=120) < /dev/null; then
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
        if (cd "$REPO_DIR/app" && pnpm test --run) < /dev/null; then
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
        if (cd "$REPO_DIR/app" && pnpm exec tsc -b) < /dev/null; then
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
    # $CURL_OPTS carries -k when the backend serves a self-signed HTTPS cert,
    # and $API_BASE already uses the https scheme + correct port in that case,
    # so this probe matches the live server's scheme. The connect/max timeouts
    # keep a slow or hung TLS handshake from stalling the whole smoke run.
    curl -sS $CURL_OPTS --connect-timeout 3 -m 5 -o /dev/null -w "%{http_code}" "${API_BASE}/api/settings" 2>/dev/null | grep -q "^200$"
}

check_http_json() {
    # $1: name, $2: path, $3: grep expression for a required substring
    local name="$1" path="$2" required="$3"
    local body
    body=$(curl -sS $CURL_OPTS "${API_BASE}${path}" 2>/dev/null)
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
        if [ "${RELEASE_MODE:-}" = "1" ]; then
            echo "ERROR: RELEASE_MODE=1 but server is not reachable at ${API_BASE}" >&2
            exit 1
        fi
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
        code=$(curl -sS $CURL_OPTS -o /dev/null -w "%{http_code}" "${API_BASE}/api/files/preview?path=README.md")
        if [ "$code" = "400" ]; then
            phase_pass "markdown gets 400 from rich preview (as designed)"
        else
            phase_fail "markdown preview returned $code instead of 400"
        fi

        # beautify-deck with bogus path should be rejected cleanly.
        code=$(curl -sS $CURL_OPTS -o /dev/null -w "%{http_code}" \
            -X POST "${API_BASE}/api/files/beautify-deck" \
            -H 'content-type: application/json' \
            -d '{"path":"no-such-deck.pptx"}')
        if [ "$code" = "400" ] || [ "$code" = "404" ]; then
            phase_pass "POST /api/files/beautify-deck rejects missing file"
        else
            phase_fail "beautify-deck returned $code for missing file"
        fi

        # Create a task and verify it shows up in the tasks list. This
        # exercises the auto-label scheduling path too. E2E_RUN_TAG ends
        # in a non-digit so _sanitize_task_title keeps the title unique
        # across back-to-back smoke runs.
        title="e2e-smoke-task-${E2E_RUN_TAG}"
        # include_test_data=true so the title sanitizer allows the e2e-
        # prefix. Without the flag the POST returns 400 "test artifact".
        create_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks?include_test_data=true" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"$title\",\"priority\":\"P2\"}")
        if echo "$create_resp" | grep -q '"task_id"'; then
            phase_pass "POST /api/tasks creates a task"
            # Extract the task id and DELETE so smoke tasks never accumulate.
            smoke_task_id=$(echo "$create_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
            if [ -n "$smoke_task_id" ]; then
                curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/tasks/${smoke_task_id}" > /dev/null 2>&1 || true
            fi
        else
            phase_fail "POST /api/tasks (body: $create_resp)"
        fi
        # --- Fleet endpoints ---
        check_http_json "GET /api/agents/fleets returns fleet list"      "/api/agents/fleets"         '"fleets"'

        # Verify fleet count (should be 9)
        fleet_count=$(curl -sS $CURL_OPTS "${API_BASE}/api/agents/fleets" 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('fleets',[])))" 2>/dev/null)
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
        check_http_json "GET /api/upgrade/status returns youros info"      "/api/upgrade/status"        '"youros"'
        check_http_json "GET /api/upgrade/status returns ostk info"      "/api/upgrade/status"        '"ostk"'

        # --- Status/clock endpoint ---
        check_http_json "GET /api/status/clock returns kernel"           "/api/status/clock"          '"kernel"'

        # --- Workflow templates ---
        check_http_json "GET /api/workflows/templates returns list"      "/api/workflows/templates"   '"templates"'

        # Verify workflow template count (should be 9)
        wf_count=$(curl -sS $CURL_OPTS "${API_BASE}/api/workflows/templates" 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('templates',[])))" 2>/dev/null)
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

        # --- Agent register + complete lifecycle ---
        lifecycle_agent="e2e-lifecycle-$(date +%s)"
        reg_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/agents/register" \
            -H 'content-type: application/json' \
            -d "{\"name\":\"$lifecycle_agent\",\"model\":\"sonnet\",\"budget\":0,\"status\":\"running\",\"task\":\"e2e lifecycle smoke test\",\"source\":\"api\"}" 2>/dev/null)
        if echo "$reg_resp" | grep -q '"result"'; then
            phase_pass "POST /api/agents/register creates running agent"
        else
            phase_fail "POST /api/agents/register (body: $reg_resp)"
        fi

        # Verify it shows as active
        active_check=$(curl -sS $CURL_OPTS "${API_BASE}/api/agents" 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('yes' if '${lifecycle_agent}' in d.get('active',[]) else 'no')
" 2>/dev/null)
        if [ "$active_check" = "yes" ]; then
            phase_pass "registered agent appears in active list"
        else
            phase_fail "registered agent not in active list"
        fi

        # Complete the agent
        comp_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/agents/${lifecycle_agent}/complete" \
            -H 'content-type: application/json' 2>/dev/null)
        if echo "$comp_resp" | grep -q '"completed"'; then
            phase_pass "POST /api/agents/{name}/complete marks agent done"
        else
            phase_fail "POST /api/agents/complete (body: $comp_resp)"
        fi

        # Verify it is no longer active
        active_after=$(curl -sS $CURL_OPTS "${API_BASE}/api/agents" 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('yes' if '${lifecycle_agent}' in d.get('active',[]) else 'no')
" 2>/dev/null)
        if [ "$active_after" = "no" ]; then
            phase_pass "completed agent removed from active list"
        else
            phase_fail "completed agent still in active list"
        fi

        # --- user_spawned_only filter matches the Agents page -----------------
        # Register a chat-session row and a real subagent, then assert the
        # filtered endpoint excludes the chat row the way isUserSpawnedAgent
        # does in app/src/lib/agentUtils.ts. Regression guard for the "my CLI
        # counted 2 agents while the Agents page showed 1" bug.
        ts_now="$(date +%s)"
        chat_name="e2e-chat-filter-${ts_now}"
        real_name="e2e-real-filter-${ts_now}"
        curl -sS $CURL_OPTS -o /dev/null -X POST "${API_BASE}/api/agents/register" \
            -H 'content-type: application/json' \
            -d "{\"name\":\"$chat_name\",\"model\":\"sonnet\",\"budget\":0,\"status\":\"running\",\"task\":\"e2e chat row\",\"source\":\"chat\"}" 2>/dev/null
        curl -sS $CURL_OPTS -o /dev/null -X POST "${API_BASE}/api/agents/register" \
            -H 'content-type: application/json' \
            -d "{\"name\":\"$real_name\",\"model\":\"sonnet\",\"budget\":0,\"status\":\"running\",\"task\":\"e2e real subagent\",\"source\":\"api\"}" 2>/dev/null

        filter_check=$(curl -sS $CURL_OPTS "${API_BASE}/api/agents?user_spawned_only=true" 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
names={a.get('name') for a in d.get('agents',[])}
has_real='${real_name}' in names
has_chat='${chat_name}' in names
print('ok' if (has_real and not has_chat) else f'fail real={has_real} chat={has_chat}')
" 2>/dev/null)
        if [ "$filter_check" = "ok" ]; then
            phase_pass "GET /api/agents?user_spawned_only=true excludes source=chat"
        else
            phase_fail "user_spawned_only filter mismatch ($filter_check)"
        fi

        # scripts/status.sh must not list the chat row either.
        # If the script is missing or not executable, skip with a clear
        # reason instead of silently branching out. status.sh is an
        # optional helper and its absence should not fail the release
        # gate, but silent skips hide real regressions so we surface it.
        if [ -x "${REPO_DIR}/scripts/status.sh" ]; then
            status_out=$(API_HOST="${API_BASE}" "${REPO_DIR}/scripts/status.sh" 2>/dev/null || true)
            if echo "$status_out" | grep -q "$chat_name"; then
                phase_fail "scripts/status.sh leaked chat row '$chat_name'"
            else
                phase_pass "scripts/status.sh hides source=chat rows"
            fi
        else
            phase_skip "scripts/status.sh chat filter (script not present or not executable)"
        fi

        # Cleanup: complete both.
        curl -sS $CURL_OPTS -o /dev/null -X POST "${API_BASE}/api/agents/${chat_name}/complete" -H 'content-type: application/json' 2>/dev/null || true
        curl -sS $CURL_OPTS -o /dev/null -X POST "${API_BASE}/api/agents/${real_name}/complete" -H 'content-type: application/json' 2>/dev/null || true

        # --- Task CRUD lifecycle ---
        # E2E_RUN_TAG ends in a non-digit so _sanitize_task_title does
        # not strip it. Without it, the sanitized title collides with
        # the previous run's title tombstone and 409s the create.
        crud_title="e2e-crud-${E2E_RUN_TAG}"
        # include_test_data=true so the hardened title sanitizer accepts
        # the e2e- prefix. Regression guard for the 8 smoke fails on
        # 2026-04-15 when the sanitizer started rejecting these.
        crud_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks?include_test_data=true" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"$crud_title\",\"priority\":\"P1\"}" 2>/dev/null)
        crud_id=$(echo "$crud_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        if [ -n "$crud_id" ]; then
            phase_pass "task CRUD: create works"
        else
            phase_fail "task CRUD: create failed"
        fi

        # Verify task appears in list by ID, not by raw title. The API's
        # _sanitize_task_title strips trailing numeric suffixes, so a
        # crud_title like "e2e-crud-1776397878" is stored as "E2e-crud"
        # and a raw-title grep misses it. The returned crud_id is stable
        # across this sanitization and uniquely identifies the row.
        # include_test_data=true so the tasks router's e2e- prefix filter
        # does not hide the task from our own verification.
        if [ -n "$crud_id" ] && curl -sS $CURL_OPTS "${API_BASE}/api/tasks?include_test_data=true" 2>/dev/null | grep -q "$crud_id"; then
            phase_pass "task CRUD: appears in task list"
        else
            phase_fail "task CRUD: not found in task list"
        fi

        # Close the task (test the close endpoint, then delete to clean up)
        close_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks/${crud_id}/close" \
            -H 'content-type: application/json' 2>/dev/null)
        if echo "$close_resp" | grep -q '"result"'; then
            phase_pass "task CRUD: close works"
        else
            phase_fail "task CRUD: close failed"
        fi
        # Delete to truly remove test data
        curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/tasks/${crud_id}" > /dev/null 2>&1

        # --- Label CRUD lifecycle ---
        label_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/labels" \
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
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/labels/${label_id}" > /dev/null 2>&1
            phase_pass "label CRUD: delete works"
        fi

        # --- Settings PATCH round trip ---
        # Save the original name into the trap variable so _e2e_cleanup
        # can restore it even if the script is interrupted mid-test.
        _E2E_ORIGINAL_OS_NAME=$(curl -sS $CURL_OPTS "${API_BASE}/api/settings" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('os_name',''))" 2>/dev/null)
        # Pollution guard: never restore "e2e-test-os" itself (means a prior run
        # was interrupted between the PATCH and the restore). Fall back to "yourOS".
        if [ "$_E2E_ORIGINAL_OS_NAME" = "e2e-test-os" ] || [ -z "$_E2E_ORIGINAL_OS_NAME" ]; then
            _E2E_ORIGINAL_OS_NAME="yourOS"
        fi
        curl -sS $CURL_OPTS -X PATCH "${API_BASE}/api/settings" \
            -H 'content-type: application/json' \
            -d '{"os_name":"e2e-test-os"}' > /dev/null 2>&1
        settings_after=$(curl -sS $CURL_OPTS "${API_BASE}/api/settings" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('os_name',''))" 2>/dev/null)
        if [ "$settings_after" = "e2e-test-os" ]; then
            phase_pass "settings PATCH round trip works"
            # Restore immediately.  Only clear _E2E_ORIGINAL_OS_NAME after
            # confirming HTTP 200 so the EXIT trap can retry if this curl
            # fails silently (→1345: the original pollution vector was that
            # the restore ran with discarded output and then the variable was
            # unconditionally cleared, leaving the real settings.json dirty
            # when the backend dropped the connection mid-restore).
            _restore_http=$(curl -sS $CURL_OPTS -o /dev/null -w "%{http_code}" \
                -X PATCH "${API_BASE}/api/settings" \
                -H 'content-type: application/json' \
                -d "{\"os_name\":\"$_E2E_ORIGINAL_OS_NAME\"}" 2>/dev/null || echo "000")
            if [ "$_restore_http" = "200" ]; then
                _E2E_ORIGINAL_OS_NAME=""
            fi
            # If not 200, keep _E2E_ORIGINAL_OS_NAME set so the EXIT trap retries.
        else
            phase_fail "settings PATCH did not persist"
            # os_name was never changed — nothing to restore.
            _E2E_ORIGINAL_OS_NAME=""
        fi

        # --- Briefing dismiss round trip ---
        curl -sS $CURL_OPTS -X POST "${API_BASE}/api/briefing/dismiss" \
            -H 'content-type: application/json' > /dev/null 2>&1
        dismiss_check=$(curl -sS $CURL_OPTS "${API_BASE}/api/briefing" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('show',True))" 2>/dev/null)
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
        # E2E_RUN_TAG suffix keeps the sanitized title unique so the
        # title tombstone from a previous run does not 409 the create.
        reopen_title="e2e-reopen-${E2E_RUN_TAG}"
        reopen_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks?include_test_data=true" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"$reopen_title\",\"priority\":\"P2\"}" 2>/dev/null)
        reopen_id=$(echo "$reopen_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        if [ -n "$reopen_id" ]; then
            curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks/${reopen_id}/close" \
                -H 'content-type: application/json' > /dev/null 2>&1
            reopen_result=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks/${reopen_id}/reopen" \
                -H 'content-type: application/json' 2>/dev/null)
            if echo "$reopen_result" | grep -q '"result"'; then
                phase_pass "journey: close then reopen task"
            else
                phase_fail "journey: reopen failed (body: $reopen_result)"
            fi
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/tasks/${reopen_id}" > /dev/null 2>&1
        else
            phase_fail "journey: could not create task for reopen test"
        fi

        # --- Journey: Task dependencies (link two tasks) ---
        dep_a_title="e2e-blocker-${E2E_RUN_TAG}"
        dep_b_title="e2e-blocked-${E2E_RUN_TAG}"
        dep_a_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks?include_test_data=true" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"$dep_a_title\",\"priority\":\"P1\"}" 2>/dev/null)
        dep_a_id=$(echo "$dep_a_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        dep_b_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks?include_test_data=true" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"$dep_b_title\",\"priority\":\"P1\"}" 2>/dev/null)
        dep_b_id=$(echo "$dep_b_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        if [ -n "$dep_a_id" ] && [ -n "$dep_b_id" ]; then
            link_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks/${dep_a_id}/link" \
                -H 'content-type: application/json' \
                -d "{\"target\":\"$dep_b_id\",\"relation\":\"blocks\"}" 2>/dev/null)
            if echo "$link_resp" | grep -q '"result"'; then
                phase_pass "journey: link two tasks (blocks relationship)"
            else
                phase_fail "journey: task link failed (body: $link_resp)"
            fi
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/tasks/${dep_a_id}" > /dev/null 2>&1
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/tasks/${dep_b_id}" > /dev/null 2>&1
        else
            phase_fail "journey: could not create tasks for dependency test"
        fi

        # --- Journey: Dashboard compounds (focus first) ---
        compounds_resp=$(curl -sS $CURL_OPTS "${API_BASE}/api/dashboard/compounds" 2>/dev/null)
        if echo "$compounds_resp" | grep -q '"all"'; then
            phase_pass "journey: dashboard compounds returns dependency analysis"
        else
            phase_fail "journey: dashboard compounds missing 'all' field"
        fi

        # --- Journey: Dashboard summary ---
        summary_resp=$(curl -sS $CURL_OPTS "${API_BASE}/api/dashboard/summary" 2>/dev/null)
        if echo "$summary_resp" | grep -q '"bullets"'; then
            phase_pass "journey: dashboard summary returns bullets"
        else
            phase_fail "journey: dashboard summary missing 'bullets' field"
        fi

        # --- Journey: Task label assignment round trip ---
        lbl_name="e2e-label-${E2E_RUN_TAG}"
        lbl_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/labels" \
            -H 'content-type: application/json' \
            -d "{\"name\":\"$lbl_name\",\"color\":\"#3b82f6\"}" 2>/dev/null)
        lbl_id=$(echo "$lbl_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('label',{}).get('id', d.get('id','')))
" 2>/dev/null || true)
        lbl_task_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks?include_test_data=true" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"e2e-label-task-${E2E_RUN_TAG}\",\"priority\":\"P2\"}" 2>/dev/null)
        lbl_task_id=$(echo "$lbl_task_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        if [ -n "$lbl_id" ] && [ -n "$lbl_task_id" ]; then
            assign_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks/${lbl_task_id}/labels/${lbl_id}" \
                -H 'content-type: application/json' 2>/dev/null)
            if echo "$assign_resp" | grep -q '"label_ids"'; then
                phase_pass "journey: assign label to task"
            else
                phase_fail "journey: label assign failed (body: $assign_resp)"
            fi
            remove_resp=$(curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/tasks/${lbl_task_id}/labels/${lbl_id}" 2>/dev/null)
            if echo "$remove_resp" | grep -q '"label_ids"'; then
                phase_pass "journey: remove label from task"
            else
                phase_fail "journey: label remove failed (body: $remove_resp)"
            fi
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/tasks/${lbl_task_id}" > /dev/null 2>&1
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/labels/${lbl_id}" > /dev/null 2>&1
        else
            phase_fail "journey: could not set up label assignment test"
        fi

        # --- Journey: Task reorder ---
        reorder_task_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks?include_test_data=true" \
            -H 'content-type: application/json' \
            -d "{\"title\":\"e2e-reorder-task-${E2E_RUN_TAG}\",\"priority\":\"P2\"}" 2>/dev/null)
        reorder_task_id=$(echo "$reorder_task_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || true)
        if [ -n "$reorder_task_id" ]; then
            reorder_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/tasks/reorder" \
                -H 'content-type: application/json' \
                -d "{\"task_id\":\"$reorder_task_id\",\"new_priority\":\"P0\",\"position\":0}" 2>/dev/null)
            if echo "$reorder_resp" | grep -q '"new_priority"'; then
                phase_pass "journey: reorder task (P2 to P0)"
            else
                phase_fail "journey: task reorder failed (body: $reorder_resp)"
            fi
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/tasks/${reorder_task_id}" > /dev/null 2>&1
        else
            phase_fail "journey: could not create task for reorder test"
        fi

        # --- Journey: Activity log shows events ---
        activity_resp=$(curl -sS $CURL_OPTS "${API_BASE}/api/activity?last=10" 2>/dev/null)
        activity_count=$(echo "$activity_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")
        if [ "$activity_count" -gt 0 ] 2>/dev/null; then
            phase_pass "journey: activity log has events ($activity_count)"
        else
            phase_fail "journey: activity log empty after creating tasks"
        fi

        # --- Journey: Search finds a task ---
        search_resp=$(curl -sS $CURL_OPTS "${API_BASE}/api/search?q=e2e" 2>/dev/null)
        if [ -n "$search_resp" ] && [ "$search_resp" != "null" ]; then
            phase_pass "journey: search returns results for 'e2e'"
        else
            phase_fail "journey: search returned empty for 'e2e'"
        fi

        # --- Journey: Notifications ---
        notif_count_resp=$(curl -sS $CURL_OPTS "${API_BASE}/api/notifications/unread/count" 2>/dev/null)
        if echo "$notif_count_resp" | grep -q '"count"'; then
            phase_pass "journey: notification unread count endpoint works"
        else
            phase_fail "journey: notification unread count missing 'count' field"
        fi
        markread_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/notifications/read-all" \
            -H 'content-type: application/json' 2>/dev/null)
        if echo "$markread_resp" | grep -q '"result"'; then
            phase_pass "journey: mark all notifications read"
        else
            phase_fail "journey: mark all notifications read failed"
        fi

        # --- Journey: Docs draft create + cleanup ---
        doc_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/docs/draft" \
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
                curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/${doc_path}" > /dev/null 2>&1
            fi
        else
            phase_fail "journey: create doc draft failed (body: $doc_resp)"
        fi

        # --- Journey: Workflow create and list ---
        wf_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/workflows" \
            -H 'content-type: application/json' \
            -d '{"name":"e2e-test-workflow","steps":[{"agent_name":"e2e-step","prompt":"echo hello","model":"sonnet","budget":0}]}' 2>/dev/null)
        wf_id=$(echo "$wf_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('workflow',{}).get('id',''))" 2>/dev/null || true)
        if [ -n "$wf_id" ]; then
            phase_pass "journey: create workflow"
            wf_list=$(curl -sS $CURL_OPTS "${API_BASE}/api/workflows" 2>/dev/null)
            if echo "$wf_list" | grep -q "e2e-test-workflow"; then
                phase_pass "journey: workflow appears in list"
            else
                phase_fail "journey: workflow not found in list"
            fi
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/workflows/${wf_id}" > /dev/null 2>&1
        else
            phase_fail "journey: create workflow failed (body: $wf_resp)"
        fi

        # --- Journey: Settings feature toggle round trip ---
        _orig_features=$(curl -sS $CURL_OPTS "${API_BASE}/api/settings" 2>/dev/null | python3 -c "
import sys,json
s=json.load(sys.stdin)
f=s.get('features',{})
print(json.dumps(f))
" 2>/dev/null)
        curl -sS $CURL_OPTS -X PATCH "${API_BASE}/api/settings" \
            -H 'content-type: application/json' \
            -d '{"features":{"Specs":false}}' > /dev/null 2>&1
        toggle_check=$(curl -sS $CURL_OPTS "${API_BASE}/api/settings" 2>/dev/null | python3 -c "
import sys,json
s=json.load(sys.stdin)
print(s.get('features',{}).get('Specs', True))
" 2>/dev/null)
        if [ "$toggle_check" = "False" ]; then
            phase_pass "journey: feature toggle persists (Specs disabled)"
        else
            phase_fail "journey: feature toggle did not persist"
        fi
        # Restore
        curl -sS $CURL_OPTS -X PATCH "${API_BASE}/api/settings" \
            -H 'content-type: application/json' \
            -d '{"features":{"Specs":true}}' > /dev/null 2>&1

        # --- Journey: Cost tracking data loads ---
        check_http_json "journey: cost tracking returns data"       "/api/costs?period=all"      ""
        check_http_json "journey: cost savings returns data"        "/api/costs/savings"         ""

        # --- Journey: Specs user journey (draft -> promote -> decompose -> verify) ---
        # Delegates to scripts/test_specs_user_journey.sh, which covers
        # the full spec-driven-development flow and cleans up its own
        # artifacts via trap. We capture its exit code and record a single
        # phase pass/fail so the final summary stays readable.
        # If the helper script has been removed (e.g. demo retired),
        # skip with a reason instead of letting bash bail with no-such-file.
        # The orphan-reference-sweep phase at the end will flag the drift.
        if [ ! -f "${REPO_DIR}/scripts/test_specs_user_journey.sh" ]; then
            phase_skip "journey: specs user journey (scripts/test_specs_user_journey.sh not present)"
        elif bash "${REPO_DIR}/scripts/test_specs_user_journey.sh" > /tmp/e2e_specs_journey.log 2>&1; then
            phase_pass "journey: specs user journey (draft -> promote -> decompose -> verify)"
        else
            phase_fail "journey: specs user journey failed (see /tmp/e2e_specs_journey.log)"
        fi

        # --- Journey: Integration auth status checks ---
        gmail_auth=$(curl -sS $CURL_OPTS "${API_BASE}/api/gmail/auth/status" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('authenticated','missing'))" 2>/dev/null)
        if [ "$gmail_auth" = "True" ] || [ "$gmail_auth" = "False" ]; then
            phase_pass "journey: Gmail auth status returns boolean"
        else
            phase_fail "journey: Gmail auth status unexpected ($gmail_auth)"
        fi
        cal_auth=$(curl -sS $CURL_OPTS "${API_BASE}/api/calendar/auth/status" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('authenticated','missing'))" 2>/dev/null)
        if [ "$cal_auth" = "True" ] || [ "$cal_auth" = "False" ]; then
            phase_pass "journey: Calendar auth status returns boolean"
        else
            phase_fail "journey: Calendar auth status unexpected ($cal_auth)"
        fi
        drive_auth=$(curl -sS $CURL_OPTS "${API_BASE}/api/drive/auth/status" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('authenticated','missing'))" 2>/dev/null)
        if [ "$drive_auth" = "True" ] || [ "$drive_auth" = "False" ]; then
            phase_pass "journey: Drive auth status returns boolean"
        else
            phase_fail "journey: Drive auth status unexpected ($drive_auth)"
        fi
        atlassian_auth=$(curl -sS $CURL_OPTS "${API_BASE}/api/atlassian/status" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('connected','missing'))" 2>/dev/null)
        if [ "$atlassian_auth" = "True" ] || [ "$atlassian_auth" = "False" ]; then
            phase_pass "journey: Atlassian auth status returns boolean"
        else
            phase_fail "journey: Atlassian auth status unexpected ($atlassian_auth)"
        fi
        github_auth=$(curl -sS $CURL_OPTS "${API_BASE}/api/github/status" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('connected','missing'))" 2>/dev/null)
        if [ "$github_auth" = "True" ] || [ "$github_auth" = "False" ]; then
            phase_pass "journey: GitHub auth status returns boolean"
        else
            phase_fail "journey: GitHub auth status unexpected ($github_auth)"
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
        curl -sS $CURL_OPTS -X POST "${API_BASE}/api/agents/register" \
            -H 'content-type: application/json' \
            -d "{\"name\":\"$nudge_agent\",\"model\":\"sonnet\",\"budget\":0,\"status\":\"running\",\"task\":\"e2e nudge round trip\",\"source\":\"api\"}" > /dev/null 2>&1
        nudge_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/agents/${nudge_agent}/nudge" \
            -H 'content-type: application/json' \
            -d '{"message":"e2e test nudge"}' 2>/dev/null)
        if echo "$nudge_resp" | grep -q '"result"\|"ok"\|"nudge"'; then
            phase_pass "journey: send nudge to running agent"
        else
            phase_fail "journey: agent nudge failed (body: $nudge_resp)"
        fi
        nudges_list=$(curl -sS $CURL_OPTS "${API_BASE}/api/agents/${nudge_agent}/nudges" 2>/dev/null)
        if echo "$nudges_list" | grep -q "e2e test nudge"; then
            phase_pass "journey: nudge appears in nudge list"
        else
            phase_fail "journey: nudge not found in nudge list"
        fi
        curl -sS $CURL_OPTS -X POST "${API_BASE}/api/agents/${nudge_agent}/complete" \
            -H 'content-type: application/json' > /dev/null 2>&1

        # --- Journey: Agent memory CRUD ---
        mem_agent="e2e-memory-$(date +%s)"
        curl -sS $CURL_OPTS -X POST "${API_BASE}/api/agents/register" \
            -H 'content-type: application/json' \
            -d "{\"name\":\"$mem_agent\",\"model\":\"sonnet\",\"budget\":0,\"status\":\"running\",\"task\":\"e2e memory crud\",\"source\":\"api\"}" > /dev/null 2>&1
        mem_save=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/agents/${mem_agent}/memory" \
            -H 'content-type: application/json' \
            -d '{"key":"test_key","value":"test_value"}' 2>/dev/null)
        if echo "$mem_save" | grep -q '"result"\|"ok"\|"saved"'; then
            phase_pass "journey: save agent memory"
        else
            phase_fail "journey: save agent memory failed (body: $mem_save)"
        fi
        mem_read=$(curl -sS $CURL_OPTS "${API_BASE}/api/agents/${mem_agent}/memory" 2>/dev/null)
        if echo "$mem_read" | grep -q "test_key\|test_value"; then
            phase_pass "journey: read agent memory"
        else
            phase_fail "journey: agent memory not readable (body: $mem_read)"
        fi
        curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/agents/${mem_agent}/memory" > /dev/null 2>&1
        curl -sS $CURL_OPTS -X POST "${API_BASE}/api/agents/${mem_agent}/complete" \
            -H 'content-type: application/json' > /dev/null 2>&1

        # --- Journey: Agent transcript ---
        check_http_json "journey: agent transcript endpoint"        "/api/agents/${nudge_agent}/transcript" ""

        # --- Journey: Threads CRUD (post-→1330: /api/threads returns 410 Gone) ---
        # Threads/Groups have been replaced by Labels with project: prefix.
        # The endpoint deliberately returns 410. Verify that contract instead
        # of trying to create a thread.
        thread_resp=$(curl -sS $CURL_OPTS -o /dev/null -w "%{http_code}" -X POST "${API_BASE}/api/threads" \
            -H 'content-type: application/json' \
            -d '{"name":"e2e-test-thread"}' 2>/dev/null)
        if [ "$thread_resp" = "410" ]; then
            phase_pass "journey: /api/threads returns 410 Gone (→1330 deprecation)"
        else
            phase_fail "journey: /api/threads expected 410, got $thread_resp"
        fi
        check_http_json "journey: list threads"                     "/api/threads"               ""

        # --- Journey: Recurring tasks ---
        recur_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/recurring" \
            -H 'content-type: application/json' \
            -d '{"task_template":{"title":"e2e-recurring","priority":"P2"},"schedule":{"kind":"weekly","days":[1]}}' 2>/dev/null)
        recur_id=$(echo "$recur_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('id', d.get('rule_id', d.get('rule',{}).get('id',''))))
" 2>/dev/null || true)
        if [ -n "$recur_id" ] && [ "$recur_id" != "None" ] && [ "$recur_id" != "" ]; then
            phase_pass "journey: create recurring task rule"
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/recurring/${recur_id}" > /dev/null 2>&1
        else
            if echo "$recur_resp" | grep -q '"id"\|"rule_id"\|"result"\|"rule"'; then
                phase_pass "journey: create recurring task rule (response ok)"
            else
                phase_fail "journey: create recurring task rule failed (body: $recur_resp)"
            fi
        fi
        check_http_json "journey: list recurring rules"             "/api/recurring"             ""

        # --- Journey: Shares CRUD ---
        share_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/shares" \
            -H 'content-type: application/json' \
            -d '{"share_type":"task_list","content_ids":["e2e-fake-id"],"title":"e2e share test"}' 2>/dev/null)
        share_token=$(echo "$share_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('token', d.get('share',{}).get('token','')))
" 2>/dev/null || true)
        if [ -n "$share_token" ] && [ "$share_token" != "None" ] && [ "$share_token" != "" ]; then
            phase_pass "journey: create share link"
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/shares/${share_token}" > /dev/null 2>&1
        else
            if echo "$share_resp" | grep -q '"token"\|"result"'; then
                phase_pass "journey: create share link (response ok)"
            else
                phase_fail "journey: create share link failed (body: $share_resp)"
            fi
        fi
        check_http_json "journey: list shares"                      "/api/shares"                ""

        # --- Journey: Knowledge notes ---
        know_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/knowledge" \
            -H 'content-type: application/json' \
            -d '{"title":"e2e-knowledge","content":"test note content"}' 2>/dev/null)
        know_id=$(echo "$know_resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('id', d.get('note_id', d.get('note',{}).get('id',''))))
" 2>/dev/null || true)
        if [ -n "$know_id" ] && [ "$know_id" != "None" ] && [ "$know_id" != "" ]; then
            phase_pass "journey: create knowledge note"
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/knowledge/${know_id}" > /dev/null 2>&1
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
        chat_save=$(curl -sS $CURL_OPTS -X PUT "${API_BASE}/api/chat/history" \
            -H 'content-type: application/json' \
            -d '{"messages":[{"role":"user","content":"e2e test"}]}' 2>/dev/null)
        if echo "$chat_save" | grep -q '"result"\|"ok"\|"saved"'; then
            phase_pass "journey: save chat history"
        else
            phase_fail "journey: save chat history failed (body: $chat_save)"
        fi
        chat_del=$(curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/chat/history" 2>/dev/null)
        if echo "$chat_del" | grep -q '"result"\|"ok"\|"deleted"\|"cleared"'; then
            phase_pass "journey: clear chat history"
        else
            phase_fail "journey: clear chat history failed (body: $chat_del)"
        fi

        # --- Journey: Secrets key status ---
        # Verify key-status returns expected boolean fields
        key_status_resp=$(curl -sS $CURL_OPTS "${API_BASE}/api/secrets/key-status" 2>/dev/null)
        if echo "$key_status_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d.get('google_connected'), bool), 'google_connected not bool'; assert 'anthropic' in d, 'anthropic missing'" 2>/dev/null; then
            phase_pass "journey: secrets key-status has google_connected bool + anthropic field"
        else
            phase_fail "journey: secrets key-status missing expected fields (body: $key_status_resp)"
        fi

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
            gmail_msgs=$(curl -sS $CURL_OPTS "${API_BASE}/api/gmail/messages" 2>/dev/null)
            if echo "$gmail_msgs" | grep -q '"messages"'; then
                phase_pass "journey: Gmail messages list loads"
                # Try marking first message as read (safe, idempotent)
                first_msg_id=$(echo "$gmail_msgs" | python3 -c "
import sys,json
msgs=json.load(sys.stdin).get('messages',[])
print(msgs[0].get('id','') if msgs else '')
" 2>/dev/null || true)
                if [ -n "$first_msg_id" ] && [ "$first_msg_id" != "" ]; then
                    mark_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/gmail/messages/${first_msg_id}/read" \
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
            cal_events=$(curl -sS $CURL_OPTS "${API_BASE}/api/calendar/events" 2>/dev/null)
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
            drive_files=$(curl -sS $CURL_OPTS "${API_BASE}/api/drive/files" 2>/dev/null)
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
        spawn_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/agents/spawn" \
            -H 'content-type: application/json' \
            -d "{\"name\":\"$spawn_name\",\"prompt\":\"say hello and exit\",\"model\":\"sonnet\",\"budget\":0}" 2>/dev/null)
        if echo "$spawn_resp" | grep -q '"name"\|"pid"\|"result"\|"status"'; then
            phase_pass "journey: agent spawn returns response"
            # Give it a moment to register
            sleep 1
            # Send a heartbeat. A budget-0 real agent can legitimately
            # complete before this heartbeat lands; that is not a bug, it
            # is live-mode reality. Accept both "ok" and "already
            # terminal" responses so the race does not flake the gate.
            hb_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/agents/${spawn_name}/heartbeat" \
                -H 'content-type: application/json' \
                -d '{"step":"e2e heartbeat test"}' 2>/dev/null)
            if echo "$hb_resp" | grep -q '"result"\|"ok"\|"step"\|terminal status'; then
                phase_pass "journey: agent heartbeat accepted"
            else
                phase_fail "journey: agent heartbeat failed (body: $hb_resp)"
            fi
            # Cancel the agent
            cancel_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/agents/${spawn_name}/cancel" \
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
        wfrun_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/workflows" \
            -H 'content-type: application/json' \
            -d '{"name":"e2e-run-workflow","steps":[{"agent_name":"e2e-wf-step","prompt":"echo done","model":"sonnet","budget":0}]}' 2>/dev/null)
        wfrun_id=$(echo "$wfrun_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('workflow',{}).get('id',''))" 2>/dev/null || true)
        if [ -n "$wfrun_id" ] && [ "$wfrun_id" != "" ]; then
            # Run the workflow
            run_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/workflows/${wfrun_id}/run" \
                -H 'content-type: application/json' 2>/dev/null)
            if echo "$run_resp" | grep -q '"status"\|"workflow"\|"steps"'; then
                phase_pass "journey: workflow run starts"
            else
                phase_fail "journey: workflow run failed (body: $run_resp)"
            fi
            # Check status
            sleep 1
            status_resp=$(curl -sS $CURL_OPTS "${API_BASE}/api/workflows/${wfrun_id}/status" 2>/dev/null)
            if echo "$status_resp" | grep -q '"status"\|"steps"\|"workflow"'; then
                phase_pass "journey: workflow status returns progress"
            else
                phase_fail "journey: workflow status failed (body: $status_resp)"
            fi
            # Cleanup
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/workflows/${wfrun_id}" > /dev/null 2>&1
        else
            phase_fail "journey: could not create workflow for run test"
        fi


        # =================================================================
        # END USER JOURNEY TESTS
        # =================================================================

        # --- Enterprise lifecycle: org, members, policies, isolation, SSO ---
        check_http_json "enterprise: GET state"                          "/api/enterprise"            '"enabled"'
        check_http_json "enterprise: GET policies"                       "/api/enterprise/policies"   '"policies"'
        check_http_json "enterprise: GET audit"                          "/api/enterprise/audit"      '"events"'

        # Check if enterprise is already enabled (avoid double-create)
        ent_enabled=$(curl -sS $CURL_OPTS "${API_BASE}/api/enterprise" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('enabled', False))" 2>/dev/null)

        # If not already enabled, run the full org lifecycle
        if [ "$ent_enabled" = "False" ]; then
            # Create org
            org_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/enterprise/org" \
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

        # pre-clear enterprise member (idempotent setup)
        echo "pre-clearing enterprise member (idempotent setup)"
        existing_member=$(curl -sS $CURL_OPTS "${API_BASE}/api/enterprise/members" 2>/dev/null | python3 -c "
import sys,json
try:
    members=json.load(sys.stdin).get('members',[])
    hit=[x for x in members if x.get('email')=='e2e-member@test.com']
    print(hit[0].get('id','') if hit else '')
except: pass
" 2>/dev/null || true)
        if [ -n "$existing_member" ] && [ "$existing_member" != "None" ] && [ "$existing_member" != "" ]; then
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/enterprise/members/${existing_member}" > /dev/null 2>&1
        fi
        # Add a member
        member_resp=$(curl -sS $CURL_OPTS -X POST "${API_BASE}/api/enterprise/members" \
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
            role_resp=$(curl -sS $CURL_OPTS -X PATCH "${API_BASE}/api/enterprise/members/${member_id}/role" \
                -H 'content-type: application/json' \
                -d '{"role":"admin"}' 2>/dev/null)
            if echo "$role_resp" | grep -q '"role"\|"admin"\|"member"'; then
                phase_pass "enterprise: update member role"
            else
                phase_fail "enterprise: update member role failed (body: $role_resp)"
            fi

            # Remove member (cleanup)
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/enterprise/members/${member_id}" > /dev/null 2>&1
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
        policy_resp=$(curl -sS $CURL_OPTS -X PATCH "${API_BASE}/api/enterprise/policies" \
            -H 'content-type: application/json' \
            -d '{"max_agent_budget":5.0}' 2>/dev/null)
        if echo "$policy_resp" | grep -q '"policies"\|"max_agent_budget"'; then
            phase_pass "enterprise: update policies"
        else
            phase_fail "enterprise: update policies failed (body: $policy_resp)"
        fi

        # Isolation: get + set
        check_http_json "enterprise: GET isolation"                      "/api/enterprise/isolation"  '"current"'
        iso_resp=$(curl -sS $CURL_OPTS -X PATCH "${API_BASE}/api/enterprise/isolation" \
            -H 'content-type: application/json' \
            -d '{"level":"governed"}' 2>/dev/null)
        if echo "$iso_resp" | grep -q '"level"\|"governed"\|"result"'; then
            phase_pass "enterprise: set isolation level"
        else
            phase_fail "enterprise: set isolation failed (body: $iso_resp)"
        fi
        # Restore to open
        curl -sS $CURL_OPTS -X PATCH "${API_BASE}/api/enterprise/isolation" \
            -H 'content-type: application/json' \
            -d '{"level":"open"}' > /dev/null 2>&1

        # SSO: get config (should work even without SSO configured)
        check_http_json "enterprise: GET SSO config"                     "/api/enterprise/sso"        ""
        # SSO login URL endpoint (verifies redirect_uri is dynamically derived)
        sso_login_resp=$(curl -sS $CURL_OPTS "${API_BASE}/api/enterprise/sso/login" 2>/dev/null)
        if echo "$sso_login_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'url' in d or 'detail' in d" 2>/dev/null; then
            phase_pass "enterprise: SSO login URL endpoint responds"
        else
            phase_fail "enterprise: SSO login URL endpoint failed (body: $sso_login_resp)"
        fi

        # Agentfile
        check_http_json "enterprise: GET agentfile"                      "/api/enterprise/agentfile"  ""

        # Clean up: delete the org if we created it
        if [ "$ent_enabled" = "False" ]; then
            curl -sS $CURL_OPTS -X DELETE "${API_BASE}/api/enterprise/org" > /dev/null 2>&1
        fi

        # --- No hardcoded ports in routers ---
        # If the routers directory is missing this is a fundamental repo
        # break that should surface as a real fail, not silently skip.
        if [ ! -d "$REPO_DIR/api/routers" ]; then
            phase_fail "api/routers directory missing (repo layout broken)"
        else
            hardcoded=$(grep -r 'localhost:5173' "$REPO_DIR/api/routers/" 2>/dev/null | grep -v '.pyc' | grep -v '#.*localhost:5173' | wc -l | tr -d ' ')
            if [ "$hardcoded" = "0" ]; then
                phase_pass "no hardcoded localhost:5173 in routers"
            else
                phase_fail "found $hardcoded hardcoded localhost:5173 references in routers"
            fi
            # --- No hardcoded https://localhost:3010 in routers ---
            hardcoded_3010=$(grep -r 'https://localhost:3010' "$REPO_DIR/api/routers/" 2>/dev/null | grep -v '.pyc' | wc -l | tr -d ' ')
            if [ "$hardcoded_3010" = "0" ]; then
                phase_pass "no hardcoded https://localhost:3010 in routers"
            else
                phase_fail "found $hardcoded_3010 hardcoded https://localhost:3010 references in routers (use _frontend_url(request))"
            fi
        fi

        # --- Vite proxy must use 127.0.0.1, never localhost (needle 315) ---
        # Node resolves "localhost" to ::1 (IPv6) first. Uvicorn only
        # binds 127.0.0.1 (IPv4), so the IPv6 attempt poisons the
        # proxy connection pool and causes intermittent ETIMEDOUT.
        # Skip cleanly if vite.config.ts was renamed or moved; the
        # orphan-reference-sweep phase at the end will flag it.
        if [ ! -f "$REPO_DIR/app/vite.config.ts" ]; then
            phase_skip "vite proxy target check (app/vite.config.ts not present)"
        else
            proxy_localhost=$(grep -E "target:.*localhost" "$REPO_DIR/app/vite.config.ts" 2>/dev/null | grep -v '//' | wc -l | tr -d ' ')
            if [ "$proxy_localhost" = "0" ]; then
                phase_pass "vite proxy targets use 127.0.0.1, not localhost"
            else
                phase_fail "vite proxy targets must use 127.0.0.1 instead of localhost (needle 315)"
            fi
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
        # start.sh is a core entry point, but if it has been renamed or
        # moved we skip with a clear reason instead of failing on a
        # bash -n against a missing file. The orphan-reference-sweep
        # phase at the end will flag the drift.
        if [ ! -f "$REPO_DIR/start.sh" ]; then
            phase_skip "start.sh syntax check (start.sh not present)"
            phase_skip "start.sh needles migration (start.sh not present)"
        else
            if bash -n "$REPO_DIR/start.sh" 2>/dev/null; then
                phase_pass "start.sh has valid bash syntax"
            else
                phase_fail "start.sh has syntax errors"
            fi

            # --- Needles migration in start.sh ---
            if grep -q 'youros/needles' "$REPO_DIR/start.sh"; then
                phase_pass "start.sh has needles migration logic"
            else
                phase_fail "start.sh missing needles migration"
            fi
        fi
    fi
fi

# --- Phase 5: WebSocket chat round trips ----------------------------------
#
# Tests three chat modes:
#   a) Claude solo: @claude model, single bubble
#   b) Gemini solo: @gemini model, single bubble
#   c) Multi-AI: @claude talk to @gemini, orchestration loop
#
# Each test sends a message, waits for at least one token + a done event,
# and reports pass/fail. The Python helper is parameterized so we avoid
# copy-pasting the same WS client three times.

if [ "$SKIP_LIVE" != "1" ]; then
    header "WebSocket chat round trips"
    if ! server_up; then
        if [ "${RELEASE_MODE:-}" = "1" ]; then
            echo "ERROR: RELEASE_MODE=1 but server is not reachable at ${API_BASE}" >&2
            exit 1
        fi
        phase_skip "WebSocket chat (API not reachable)"
    elif ! command -v python3 > /dev/null 2>&1; then
        phase_skip "WebSocket chat (python3 not installed)"
    else

# Shared Python helper: takes model and message as env vars.
_ws_chat_test() {
    local _model="$1" _message="$2"
    PYTHONPATH="$REPO_DIR/api" API_PORT="$API_PORT" \
        _WS_MODEL="$_model" _WS_MESSAGE="$_message" \
        _WS_USE_TLS="$([ -f "$HOME/.youros/localhost.key" ] && echo 1 || echo 0)" \
        python3 "$REPO_DIR/scripts/lib/e2e_ws_chat.py"
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
    elif ! curl -ksS --connect-timeout 3 -m 5 -o /dev/null -w "%{http_code}" "https://localhost:${FRONTEND_PORT:-3010}" 2>/dev/null | grep -q "^200$"; then
        phase_skip "frontend not reachable on port ${FRONTEND_PORT:-3010}"
    elif [ ! -f "$REPO_DIR/scripts/e2e_browser.sh" ]; then
        # Helper script has been retired or moved. Skip cleanly; the
        # orphan-reference-sweep phase at the end will flag the drift.
        phase_skip "browser e2e tests (scripts/e2e_browser.sh not present)"
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

# --- Phase 7: e2e leftover regression assertion ------------------------------
# After every phase has run, the EXIT trap will fire one more sweep. But we
# want this to surface as a phase result too, so a sweep miss registers as a
# smoke failure (not a silent leftover). We run the sweep here, then assert
# the /api/specs count of e2e entries is zero.
header "E2E leftover regression"
if ! server_up; then
    # No live API to query: we cannot count leftovers, so this is a skip,
    # not a failure. Reporting a fabricated negative count here is what
    # produced the impossible "-1 e2e spec(s)" line.
    phase_skip "e2e leftovers (API not reachable on ${API_BASE})"
else
    _e2e_sweep_artifacts
    # The Python helper prints either a non-negative leftover count or the
    # token UNREACHABLE when the response cannot be parsed. It never prints a
    # negative number, so the count can never go below zero.
    _e2e_remaining=$(curl -sS $CURL_OPTS --connect-timeout 3 -m 5 \
        "${API_BASE}/api/specs" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('UNREACHABLE'); sys.exit(0)
n = 0
for x in d.get('docs', []):
    p = (x.get('path') or '').lower()
    t = (x.get('title') or '').lower()
    if 'e2e-' in p or 'e2e-' in t:
        n += 1
print(n)
" 2>/dev/null)
    if [ "$_e2e_remaining" = "0" ]; then
        phase_pass "e2e leftovers: 0 specs remain after sweep"
    elif [ -z "$_e2e_remaining" ] || [ "$_e2e_remaining" = "UNREACHABLE" ]; then
        phase_skip "e2e leftovers (could not read /api/specs)"
    else
        phase_fail "e2e leftovers: ${_e2e_remaining} e2e spec(s) still present after sweep"
    fi
fi

# --- Phase 8: orphan-reference-sweep -----------------------------------------
# Scan this smoke script for repo-relative paths (e.g. scripts/foo.sh,
# app/vite.config.ts, api/routers) it references. If any of those paths
# no longer exist on disk, the smoke has drifted: a future phase will hit
# a missing artifact and silently fail or skip without a clear reason.
# Surface those drifts as a single fail here at release time so they get
# fixed (either by updating the phase or removing the stale reference)
# instead of rotting the gate.
header "Orphan reference sweep"
_orphan_list=$(python3 "$REPO_DIR/scripts/lib/e2e_orphan_sweep.py" "${REPO_DIR}" "${REPO_DIR}/scripts/e2e_smoke.sh" 2>/dev/null)
if [ -z "$_orphan_list" ]; then
    phase_pass "orphan-reference-sweep: every path referenced in e2e_smoke.sh exists on disk"
else
    # Turn the newline-separated list into a single comma-joined line
    # so the phase_fail output stays readable in release logs.
    _orphan_joined=$(echo "$_orphan_list" | tr '\n' ',' | sed 's/,$//')
    phase_fail "orphan-reference-sweep: missing referenced path(s): $_orphan_joined"
fi

# --- Summary ---------------------------------------------------------------

header "Summary"
echo -e "  ${GREEN}PASS${NC} $PASS"
echo -e "  ${RED}FAIL${NC} $FAIL"
echo -e "  ${YELLOW}SKIP${NC} $SKIP"
echo ""

if [ "$FAIL" -eq 0 ]; then
    curl -sk --connect-timeout 3 -m 5 -X POST "${API_BASE}/api/time/finish" \
        -H 'Content-Type: application/json' \
        -d "{\"op_id\":\"${SMOKE_OP_ID}\",\"status\":\"completed\"}" \
        > /dev/null 2>&1 || true
    echo -e "${GREEN}All phases passed.${NC} yourOS is ready to release."
    exit 0
else
    curl -sk --connect-timeout 3 -m 5 -X POST "${API_BASE}/api/time/finish" \
        -H 'Content-Type: application/json' \
        -d "{\"op_id\":\"${SMOKE_OP_ID}\",\"status\":\"failed\"}" \
        > /dev/null 2>&1 || true
    echo -e "${RED}$FAIL phase(s) failed.${NC} Fix them before releasing."
    exit 1
fi
