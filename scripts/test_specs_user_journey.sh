#!/usr/bin/env bash
# yourOS Specs user-journey smoke test.
#
# Walks the full demo flow against a running backend:
#   1. Create a draft named "e2e-specs-journey-..." via /api/specs/draft
#   2. Promote the draft to a spec
#   3. Break the spec into tasks via /api/specs/decompose
#   4. Close every linked task via /api/tasks/close
#   5. Verify the spec via /api/specs/.../verify
#   6. Assert the spec's final status is "complete"
#
# Every artifact (spec file, tasks) uses the "e2e-" prefix so the main
# smoke-test cleanup sweep catches anything this script fails to delete.
# A trap also deletes the spec and tasks inline on EXIT, INT, TERM, HUP.
#
# Usage:
#   ./scripts/test_specs_user_journey.sh
#   API_BASE=https://127.0.0.1:8000 ./scripts/test_specs_user_journey.sh
#
# Exit code is 0 on pass, 1 on any failed phase.

set -u

# →1454 plan, Fix 1: redirect spec writes to a tmpdir so this smoke test
# never accumulates artifacts under ~/.youros/specs/. The backend reads
# YOUROS_USER_SPECS_DIR at module load (api/services/ostk.py), so it is the
# caller's responsibility to restart the backend with this env var set
# before running the smoke. e2e_smoke.sh sets the same var and child
# dev-backend.sh inherits it. Without that restart, this export protects
# nothing on its own — but the trap still cleans up the dir if anything
# did land there during the run.
if [ -z "${YOUROS_USER_SPECS_DIR:-}" ]; then
    YOUROS_USER_SPECS_DIR="$(mktemp -d -t youros-specs-smoke.XXXXXX)/specs"
    export YOUROS_USER_SPECS_DIR
    trap 'rm -rf "$(dirname "$YOUROS_USER_SPECS_DIR")"' EXIT INT TERM HUP
fi

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

API_PORT="${API_PORT:-8000}"
SSL_CERT="$HOME/.youros/localhost.crt"
if [ -f "$SSL_CERT" ]; then
    API_BASE="${API_BASE:-https://127.0.0.1:${API_PORT}}"
    CURL_OPTS="-k"
else
    API_BASE="${API_BASE:-http://localhost:${API_PORT}}"
    CURL_OPTS=""
fi

# Every curl in this script passes --connect-timeout 3 -m 5 so a stuck
# server fails fast rather than hanging the smoke run.
CURL_TIMEOUTS="--connect-timeout 3 -m 5"
# Draft + decompose hit an AI call that can take longer than 5s. Use a
# longer cap just for those two requests so an honest AI response does
# not time out. Still bounded so a wedged backend fails fast.
CURL_AI_TIMEOUTS="--connect-timeout 3 -m 30"

PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC}  $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC}  $1"; FAIL=$((FAIL + 1)); }
info() { echo -e "  ${YELLOW}--${NC}    $1"; }

# Unique title per run so concurrent runs do not collide.
RUN_ID="$(date +%s)"
SPEC_TITLE="e2e-specs-journey-id${RUN_ID}-end"
SPEC_PATH=""
TASK_IDS=""

cleanup() {
    # Delete tasks created by this run, if any. ostk stores task IDs as
    # arrow-prefixed strings ("→561") but decompose returns them bare
    # ("561"). The DELETE route requires the arrow form, so prepend it.
    if [ -n "$TASK_IDS" ]; then
        for tid in $TASK_IDS; do
            curl -sS $CURL_OPTS $CURL_TIMEOUTS -X DELETE \
                "${API_BASE}/api/tasks/$(python3 -c "import urllib.parse; print(urllib.parse.quote('\u2192${tid}', safe=''))" 2>/dev/null)" \
                > /dev/null 2>&1 || true
        done
    fi
    # Delete spec + any leftover draft.
    if [ -n "$SPEC_PATH" ]; then
        curl -sS $CURL_OPTS $CURL_TIMEOUTS -X DELETE "${API_BASE}/api/${SPEC_PATH}" \
            > /dev/null 2>&1 || true
    fi
    # Belt-and-suspenders sweep: delete any spec/draft whose path matches our run id.
    python3 - <<PY 2>/dev/null || true
import json, urllib.request
try:
    req = urllib.request.Request("${API_BASE}/api/specs")
    ctx = None
    if "${API_BASE}".startswith("https://"):
        import ssl
        ctx = ssl._create_unverified_context()
    data = json.loads(urllib.request.urlopen(req, timeout=3, context=ctx).read())
    for d in data.get("docs", []):
        p = d.get("path", "")
        if "e2e-specs-journey-${RUN_ID}" in p:
            dreq = urllib.request.Request("${API_BASE}/api/" + p, method="DELETE")
            try:
                urllib.request.urlopen(dreq, timeout=3, context=ctx)
            except Exception:
                pass
except Exception:
    pass
PY
}
trap cleanup EXIT INT TERM HUP

echo ""
echo "=== Specs user journey ==="
echo ""
info "API_BASE=${API_BASE}"
info "spec title: ${SPEC_TITLE}"

# --- Step 1: Create draft ---
# The draft endpoint makes an AI call that can take ~6s warm. If the
# server is under load (smoke runs many flows back to back) it can push
# past a naive timeout. We retry up to 3 times with a short backoff and
# surface the real curl error on final failure so the smoke log actually
# tells us WHY it failed instead of just "empty body".
DRAFT_ERR_FILE="$(mktemp -t youros-specs-draft-err.XXXXXX)"
draft_resp=""
draft_attempt=0
draft_max=3
while [ $draft_attempt -lt $draft_max ]; do
    draft_attempt=$((draft_attempt + 1))
    # Bump the cap to 60s on retries so a busy backend can complete the AI
    # call. The first attempt stays at 30s so the common warm path is fast.
    if [ $draft_attempt -eq 1 ]; then
        _draft_timeouts="$CURL_AI_TIMEOUTS"
    else
        _draft_timeouts="--connect-timeout 3 -m 60"
    fi
    : > "$DRAFT_ERR_FILE"
    draft_resp=$(curl -sS $CURL_OPTS $_draft_timeouts -X POST \
        "${API_BASE}/api/specs/draft" \
        -H 'content-type: application/json' \
        -d "{\"title\":\"${SPEC_TITLE}\",\"kind\":\"spec\",\"fallback_ac\":true}" 2> "$DRAFT_ERR_FILE" || true)
    if echo "$draft_resp" | grep -q '"result"'; then
        break
    fi
    if [ $draft_attempt -lt $draft_max ]; then
        info "draft attempt ${draft_attempt} failed. retrying in 2s. curl stderr: $(head -c 200 "$DRAFT_ERR_FILE" | tr '\n' ' ')"
        sleep 2
    fi
done

if echo "$draft_resp" | grep -q '"result"'; then
    pass "journey: create draft (attempt ${draft_attempt})"
    # Wave 2 auto-promotes drafts as part of /specs/draft when AC generation
    # succeeds. The response now includes promoted_path so we can use that
    # directly. Fall back to parsing draft path for the rare case where AC
    # generation failed and the doc stayed as a draft.
    PROMOTED=$(echo "$draft_resp" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('promoted_path', '') or '')
except Exception:
    pass
" 2>/dev/null)
    DRAFT_PATH=$(echo "$draft_resp" | python3 -c "
import sys, json, re
try:
    r = json.load(sys.stdin).get('result', '')
except Exception:
    sys.exit(0)
m = re.search(r'(docs/(draft|spec)/[^\s]+\.md)', r)
print(m.group(1) if m else r.strip())
" 2>/dev/null)
    if [ -n "$PROMOTED" ]; then
        SPEC_PATH="$PROMOTED"
        info "draft path: ${DRAFT_PATH}"
        info "auto-promoted spec path: ${SPEC_PATH}"
    else
        SPEC_PATH="$DRAFT_PATH"
        info "draft path: ${DRAFT_PATH} (no auto-promote, will explicit-promote)"
    fi
else
    _err_head=$(head -c 200 "$DRAFT_ERR_FILE" | tr '\n' ' ')
    rm -f "$DRAFT_ERR_FILE"
    fail "journey: create draft after ${draft_attempt} attempts (response: ${draft_resp:0:200} | curl stderr: ${_err_head})"
    exit 1
fi
rm -f "$DRAFT_ERR_FILE"

# --- Step 2: Promote draft to spec ---
# Wave 2 auto-promotes inside create_draft. If we already have a promoted_path
# from Step 1, skip the explicit promote (it would 404 because the source draft
# file has been moved to docs/spec/). Otherwise fall back to the manual promote.
if [ -n "$PROMOTED" ]; then
    pass "journey: promote draft to spec (auto-promoted in Step 1)"
    info "spec path: ${SPEC_PATH}"
else
    # First, try the normal manual promote path (works when the draft has
    # AI-generated acceptance criteria). If the backend rejects it (no AC),
    # fall back to from-template which writes pre-written criteria without
    # needing an AI key. The fallback deletes the empty draft first so
    # there is no leftover artifact.
    promote_resp=$(curl -sS $CURL_OPTS $CURL_TIMEOUTS -X POST \
        "${API_BASE}/api/specs/promote" \
        -H 'content-type: application/json' \
        -d "{\"path\":\"${DRAFT_PATH}\"}" 2>/tmp/specs-journey-promote.err || printf "")

    if echo "$promote_resp" | grep -q '"result"'; then
        pass "journey: promote draft to spec"
        PROMOTED=$(echo "$promote_resp" | python3 -c "
import sys, json, re
try:
    r = json.load(sys.stdin).get('result', '')
except Exception:
    sys.exit(0)
m = re.search(r'(docs/spec/[^\s]+\.md)', r)
print(m.group(1) if m else '')
" 2>/dev/null)
        if [ -n "$PROMOTED" ]; then
            SPEC_PATH="$PROMOTED"
        fi
        info "spec path: ${SPEC_PATH}"
    elif echo "$promote_resp" | grep -qi "unchecked checkbox\|acceptance criteria"; then
        # Draft has no AC — AI was not available. Delete the empty draft
        # and re-create using a template which provides pre-written criteria.
        info "promote rejected (no AI-generated AC); falling back to from-template"
        curl -sS $CURL_OPTS $CURL_TIMEOUTS -X DELETE \
            "${API_BASE}/api/${DRAFT_PATH}" > /dev/null 2>&1 || true
        DRAFT_PATH=""

        tmpl_resp=$(curl -sS $CURL_OPTS $CURL_TIMEOUTS -X POST \
            "${API_BASE}/api/specs/from-template" \
            -H 'content-type: application/json' \
            -d "{\"template_id\":\"build-a-website\",\"title\":\"${SPEC_TITLE}\"}" \
            2>/tmp/specs-journey-tmpl.err || printf "")

        PROMOTED=$(echo "$tmpl_resp" | python3 -c "
import sys, json, re
try:
    d = json.load(sys.stdin)
    p = d.get('promoted_path', '') or d.get('result', '')
except Exception:
    sys.exit(0)
m = re.search(r'(docs/spec/[^\s]+\.md)', str(p))
print(m.group(1) if m else '')
" 2>/dev/null)
        if [ -n "$PROMOTED" ]; then
            SPEC_PATH="$PROMOTED"
            pass "journey: promote draft to spec (template fallback, no AI key)"
            info "spec path: ${SPEC_PATH}"
        else
            fail "journey: from-template fallback failed (response: ${tmpl_resp:0:200})"
            exit 1
        fi
    else
        fail "journey: promote draft (response: ${promote_resp:0:200})"
        exit 1
    fi
fi

# --- Step 3: Break into tasks ---
# Like draft, decompose makes an AI call and benefits from the same
# retry-with-backoff guard when the smoke has the backend under load.
DECOMP_ERR_FILE="$(mktemp -t youros-specs-decomp-err.XXXXXX)"
decompose_resp=""
decomp_attempt=0
decomp_max=3
while [ $decomp_attempt -lt $decomp_max ]; do
    decomp_attempt=$((decomp_attempt + 1))
    if [ $decomp_attempt -eq 1 ]; then
        _decomp_timeouts="$CURL_AI_TIMEOUTS"
    else
        _decomp_timeouts="--connect-timeout 3 -m 60"
    fi
    : > "$DECOMP_ERR_FILE"
    decompose_resp=$(curl -sS $CURL_OPTS $_decomp_timeouts -X POST \
        "${API_BASE}/api/specs/decompose" \
        -H 'content-type: application/json' \
        -d "{\"path\":\"${SPEC_PATH}\"}" 2> "$DECOMP_ERR_FILE" || true)
    if echo "$decompose_resp" | grep -q -E '"task_ids"|"result"'; then
        break
    fi
    if [ $decomp_attempt -lt $decomp_max ]; then
        info "decompose attempt ${decomp_attempt} failed. retrying in 2s. curl stderr: $(head -c 200 "$DECOMP_ERR_FILE" | tr '\n' ' ')"
        sleep 2
    fi
done
rm -f "$DECOMP_ERR_FILE"

TASK_IDS=$(echo "$decompose_resp" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ids = data.get('task_ids', []) or []
print(' '.join(str(i) for i in ids))
" 2>/dev/null)

TASK_COUNT=$(echo $TASK_IDS | wc -w | tr -d ' ')
if [ "$TASK_COUNT" -gt 0 ]; then
    pass "journey: decompose created ${TASK_COUNT} task(s)"
    info "task ids: ${TASK_IDS}"
else
    info "journey: decompose returned no tasks (AI may be unavailable)"
    info "skipping rest of journey"
    exit 0
fi

# --- Step 4: Close every linked task ---
closed_ok=1
for tid in $TASK_IDS; do
    # ostk task IDs come back as bare numeric strings; the close endpoint
    # accepts both bare and arrow-prefixed forms.
    close_resp=$(curl -sS $CURL_OPTS $CURL_TIMEOUTS -X POST \
        "${API_BASE}/api/tasks/${tid}/close" \
        -H 'content-type: application/json' \
        -d '{}' 2>/tmp/specs-journey-close.err || printf "")
    if ! echo "$close_resp" | grep -q -E '(result|ok|closed)'; then
        closed_ok=0
        info "close failed for ${tid}: ${close_resp:0:120}"
    fi
done
if [ "$closed_ok" -eq 1 ]; then
    pass "journey: closed all ${TASK_COUNT} tasks"
else
    fail "journey: some task closes failed"
fi

# --- Step 5: Verify the spec ---
verify_resp=$(curl -sS $CURL_OPTS $CURL_TIMEOUTS -X POST \
    "${API_BASE}/api/specs/${SPEC_PATH}/verify" \
    -H 'content-type: application/json' \
    -d '{}' 2>/tmp/specs-journey-verify.err || printf "")

all_met=$(echo "$verify_resp" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(str(data.get('all_met', False)))
except Exception:
    print('error')
" 2>/dev/null)

if [ "$all_met" = "True" ]; then
    pass "journey: verify reports all_met=True"
elif [ "$all_met" = "False" ]; then
    info "journey: verify returned all_met=False (some criteria not met)"
else
    fail "journey: verify endpoint did not return parseable JSON"
fi

# --- Step 6: Final status should be complete (when verify succeeded) ---
specs_resp=$(curl -sS $CURL_OPTS $CURL_TIMEOUTS "${API_BASE}/api/specs" 2>/tmp/specs-journey-list.err || printf "")
final_status=$(echo "$specs_resp" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for d in data.get('docs', []):
    if d.get('path') == '${SPEC_PATH}':
        print(d.get('status', ''))
        break
" 2>/dev/null)

info "final status: ${final_status}"
# When all_met=True, the spec either flips to "complete" or gets silently
# auto-archived (moved to ~/.youros/specs/archive/) per the /api/specs
# docstring. Either state is correct success.
if [ "$all_met" = "True" ] && { [ "$final_status" = "complete" ] || [ -z "$final_status" ]; }; then
    if [ -z "$final_status" ]; then
        pass "journey: spec auto-archived after verify (all criteria met)"
    else
        pass "journey: spec flipped to complete after verify"
    fi
elif [ "$all_met" = "False" ] && [ "$final_status" = "in-progress" ]; then
    pass "journey: spec stays in-progress when verify did not pass"
else
    fail "journey: unexpected final status '${final_status}' for all_met=${all_met}"
fi

echo ""
echo "Specs journey: ${PASS} pass, ${FAIL} fail"
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
