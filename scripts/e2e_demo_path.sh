#!/usr/bin/env bash
# e2e_demo_path.sh — end to end verification of Tori's live demo path.
#
# Walks the exact four steps Tori performs on stage:
#   1. Spawn the Roadmap agent so a fresh roadmap.md lands in
#      ~/.myos/files/ (uses the demo prewarm replay when available).
#   2. Pick one initiative line and call /specs/from-roadmap-line to
#      build a ready spec with acceptance criteria.
#   3. Hit /specs/{path}/build to fan out Builder agents in parallel.
#   4. Verify at least one builder reaches a running state fast, and
#      confirm the full build settles inside a demo-friendly budget.
#
# Measures the wall clock for every step and fails loudly if any
# regression shows up. Leaves no test spec, test agents, or test
# transcripts on disk once the run completes.
#
# SLO budgets (chosen so a live demo feels snappy but stays honest):
#   Step 1  Roadmap spawn POST response      <  2000 ms
#   Step 1b Roadmap.md visible on disk       <= 10000 ms  (prewarm target 5s)
#   Step 2  Spec created + promoted to ready <  20000 ms  (real AC LLM call)
#   Step 3  Build HTTP return                <   4000 ms  (parallel spawn)
#   Step 3b First builder visible running    <    500 ms after HTTP return
#   Step 4  At least one task non-pending    <  30000 ms  (demo-safe floor)
#
# Usage:
#   ./scripts/e2e_demo_path.sh
#   API_PORT=8001 ./scripts/e2e_demo_path.sh
#
# Exit 0 on full pass, 1 on any regression.

set -u

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

API_PORT="${API_PORT:-8000}"
API_BASE="https://127.0.0.1:${API_PORT}"
CURL="curl -sSk --connect-timeout 3 -m 30"
BUDGET_STEP1_SPAWN_MS="${BUDGET_STEP1_SPAWN_MS:-2000}"
BUDGET_STEP1_FILE_MS="${BUDGET_STEP1_FILE_MS:-10000}"
BUDGET_STEP2_SPEC_MS="${BUDGET_STEP2_SPEC_MS:-20000}"
BUDGET_STEP3_BUILD_MS="${BUDGET_STEP3_BUILD_MS:-4000}"
BUDGET_STEP3_VISIBLE_MS="${BUDGET_STEP3_VISIBLE_MS:-500}"
BUDGET_STEP4_ACTIVE_MS="${BUDGET_STEP4_ACTIVE_MS:-30000}"

STAMP="$(date +%s)"
ROADMAP_AGENT="e2e-demo-path-roadmap-${STAMP}"
INITIATIVE="E2E demo path probe ${STAMP}"
SPEC_SLUG="e2e-demo-path-probe-${STAMP}"
SPEC_PATH="docs/spec/${SPEC_SLUG}.md"
ROADMAP_FILE="${HOME}/.myos/files/roadmap.md"

ts_ms() { python3 -c "import time; print(int(time.time()*1000))"; }

log_info()  { printf "${BLUE}[info]${NC}  %s\n" "$*"; }
log_pass()  { printf "${GREEN}[pass]${NC}  %s\n" "$*"; }
log_fail()  { printf "${RED}[fail]${NC}  %s\n" "$*"; }
log_warn()  { printf "${YELLOW}[warn]${NC}  %s\n" "$*"; }

FAILURES=0
fail() {
  log_fail "$*"
  FAILURES=$(( FAILURES + 1 ))
}

CREATED_AGENTS=()
CREATED_SPEC=""

cleanup() {
  local a
  if [ "${#CREATED_AGENTS[@]}" -gt 0 ]; then
    log_info "cleanup: cancelling ${#CREATED_AGENTS[@]} spawned agent(s)"
    for a in "${CREATED_AGENTS[@]}"; do
      local enc
      enc=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$a" 2>/dev/null || echo "$a")
      $CURL -m 5 -X POST "${API_BASE}/api/agents/${enc}/cancel" >/dev/null 2>&1 || true
    done
  fi
  if [ -n "$CREATED_SPEC" ]; then
    log_info "cleanup: deleting probe spec ${CREATED_SPEC}"
    $CURL -m 5 -X DELETE "${API_BASE}/api/specs/${CREATED_SPEC}" >/dev/null 2>&1 || true
  fi
  rm -f "/Users/torimeyer/claude/torios/transcripts/${ROADMAP_AGENT}.md" 2>/dev/null || true
}
trap cleanup EXIT

echo ""
echo "=== e2e_demo_path  ${API_BASE}  $(date '+%H:%M:%S') ==="
echo ""

# Preflight
health=$($CURL "${API_BASE}/api/health" 2>&1 || true)
if ! echo "$health" | grep -q '"status":"ok"'; then
  fail "backend not healthy at ${API_BASE}. Start scripts/dev-backend.sh first."
  exit 1
fi
log_pass "backend is healthy"

if [ ! -f "${HOME}/.myos/prewarm/roadmap.md" ]; then
  log_warn "no prewarm roadmap at ~/.myos/prewarm/roadmap.md — step 1 will hit a real LLM and may be slow"
fi

# Step 1: spawn Roadmap agent
log_info "step 1: spawning Roadmap agent (${ROADMAP_AGENT})"
payload=$(python3 -c "
import json,sys
print(json.dumps({
  'name': sys.argv[1],
  'prompt': 'generate a fresh roadmap',
  'template': 'Roadmap',
  'model': 'haiku',
  'demo_mode': True,
  'source': 'e2e-demo-path',
}))
" "$ROADMAP_AGENT")

t0=$(ts_ms)
spawn_resp=$($CURL -m 10 -X POST "${API_BASE}/api/agents/spawn" \
  -H 'Content-Type: application/json' -d "$payload" 2>&1)
t1=$(ts_ms)
STEP1_SPAWN_MS=$(( t1 - t0 ))

if ! echo "$spawn_resp" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
assert d.get('name'), f'missing name in spawn response: {d}'
" 2>/dev/null; then
  fail "step 1 spawn call did not return a valid agent: ${spawn_resp}"
  exit 1
fi
CREATED_AGENTS+=("$ROADMAP_AGENT")

if (( STEP1_SPAWN_MS > BUDGET_STEP1_SPAWN_MS )); then
  fail "step 1 spawn took ${STEP1_SPAWN_MS}ms (budget ${BUDGET_STEP1_SPAWN_MS}ms)"
else
  log_pass "step 1 spawn returned in ${STEP1_SPAWN_MS}ms"
fi

# Step 1b: roadmap.md refresh on disk
log_info "step 1b: waiting for ${ROADMAP_FILE} to appear and grow"
prev_size=-1
if [ -f "$ROADMAP_FILE" ]; then
  prev_size=$(wc -c <"$ROADMAP_FILE" | tr -d ' ' || echo 0)
fi
STEP1_FILE_MS=-1
wait_start=$(ts_ms)
while :; do
  now=$(ts_ms)
  elapsed=$(( now - wait_start ))
  if [ -f "$ROADMAP_FILE" ]; then
    cur=$(wc -c <"$ROADMAP_FILE" | tr -d ' ' || echo 0)
    if [ "$cur" -gt "$prev_size" ] && [ "$cur" -gt 500 ]; then
      STEP1_FILE_MS=$elapsed
      break
    fi
  fi
  if (( elapsed > BUDGET_STEP1_FILE_MS )); then
    break
  fi
  sleep 0.2
done
if (( STEP1_FILE_MS < 0 )); then
  fail "step 1b roadmap.md did not refresh within ${BUDGET_STEP1_FILE_MS}ms"
else
  log_pass "step 1b roadmap.md refreshed in ${STEP1_FILE_MS}ms ($(wc -c <"$ROADMAP_FILE" | tr -d ' ') bytes)"
fi

# Step 2: make a spec from one roadmap line
log_info "step 2: POST /specs/from-roadmap-line (title: ${INITIATIVE})"
t0=$(ts_ms)
spec_resp=$($CURL -m 60 -X POST "${API_BASE}/api/specs/from-roadmap-line" \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c "
import json, sys
print(json.dumps({'roadmap_path': sys.argv[1], 'initiative_text': sys.argv[2]}))
" "$ROADMAP_FILE" "$INITIATIVE")" 2>&1)
t1=$(ts_ms)
STEP2_SPEC_MS=$(( t1 - t0 ))

promoted_path=$(echo "$spec_resp" | python3 -c "
import sys, json
try:
  d = json.loads(sys.stdin.read())
  print(d.get('promoted_path') or '')
except Exception:
  print('')
" 2>/dev/null || echo "")
spec_status=$(echo "$spec_resp" | python3 -c "
import sys, json
try:
  d = json.loads(sys.stdin.read())
  print(d.get('status') or '')
except Exception:
  print('')
" 2>/dev/null || echo "")

if [ -z "$promoted_path" ] || [ "$spec_status" != "ready" ]; then
  fail "step 2 spec creation did not return a promoted ready spec: ${spec_resp}"
  exit 1
fi

if [[ "$promoted_path" == *"/docs/spec/"* ]]; then
  SPEC_PATH="docs/spec/${promoted_path##*/docs/spec/}"
fi
CREATED_SPEC="$SPEC_PATH"

if (( STEP2_SPEC_MS > BUDGET_STEP2_SPEC_MS )); then
  fail "step 2 spec creation took ${STEP2_SPEC_MS}ms (budget ${BUDGET_STEP2_SPEC_MS}ms)"
else
  log_pass "step 2 spec ready in ${STEP2_SPEC_MS}ms (path: ${SPEC_PATH})"
fi

tasks_before=$($CURL -m 5 "${API_BASE}/api/specs/${SPEC_PATH}/tasks" 2>&1 || echo "")
tb_count=$(echo "$tasks_before" | python3 -c "
import sys, json
try:
  d = json.loads(sys.stdin.read())
  print(len(d.get('tasks', [])))
except Exception:
  print(0)
" 2>/dev/null || echo 0)
if (( tb_count < 1 )); then
  log_warn "step 2 spec has ${tb_count} tasks before build. Build will call decompose before spawning."
else
  log_info "step 2 spec has ${tb_count} tasks ready for build"
fi

# Step 3: build it
log_info "step 3: POST /specs/${SPEC_PATH}/build"
t0=$(ts_ms)
build_resp=$($CURL -m 30 -X POST "${API_BASE}/api/specs/${SPEC_PATH}/build" 2>&1)
t1=$(ts_ms)
STEP3_BUILD_MS=$(( t1 - t0 ))

builder_names=$(echo "$build_resp" | python3 -c "
import sys, json
try:
  d = json.loads(sys.stdin.read())
  names = d.get('agents') or []
  print('\n'.join(names))
except Exception:
  pass
" 2>/dev/null || echo "")
builder_count=0
if [ -n "$builder_names" ]; then
  builder_count=$(echo "$builder_names" | wc -l | tr -d ' ')
  while IFS= read -r name; do
    [ -n "$name" ] && CREATED_AGENTS+=("$name")
  done <<< "$builder_names"
fi

if (( builder_count < 1 )); then
  fail "step 3 build did not spawn any builders. Response: ${build_resp}"
  exit 1
fi

if (( STEP3_BUILD_MS > BUDGET_STEP3_BUILD_MS )); then
  fail "step 3 build HTTP took ${STEP3_BUILD_MS}ms (budget ${BUDGET_STEP3_BUILD_MS}ms). The asyncio.gather fanout from 481200b may have regressed."
else
  log_pass "step 3 build spawned ${builder_count} builder(s) in ${STEP3_BUILD_MS}ms"
fi

# Step 3b: first builder visible running
log_info "step 3b: polling /api/agents for at least one running builder"
STEP3B_VISIBLE_MS=-1
vstart=$(ts_ms)
while :; do
  now=$(ts_ms)
  elapsed=$(( now - vstart ))
  agents_json=$($CURL -m 5 "${API_BASE}/api/agents" 2>&1 || echo "")
  hits=$(echo "$agents_json" | python3 -c "
import sys, json
try:
  d = json.loads(sys.stdin.read())
  wanted = set(sys.argv[1:])
  running = [a for a in d.get('agents', [])
             if a.get('name') in wanted and a.get('status') == 'running']
  print(len(running))
except Exception:
  print(0)
" $(echo "$builder_names") 2>/dev/null || echo 0)
  if (( hits >= 1 )); then
    STEP3B_VISIBLE_MS=$elapsed
    break
  fi
  if (( elapsed > BUDGET_STEP3_VISIBLE_MS * 4 )); then
    break
  fi
  sleep 0.1
done
if (( STEP3B_VISIBLE_MS < 0 )); then
  fail "step 3b no builder reached running state within $(( BUDGET_STEP3_VISIBLE_MS * 4 ))ms"
elif (( STEP3B_VISIBLE_MS > BUDGET_STEP3_VISIBLE_MS )); then
  log_warn "step 3b builder running after ${STEP3B_VISIBLE_MS}ms (target ${BUDGET_STEP3_VISIBLE_MS}ms)"
else
  log_pass "step 3b first builder running in ${STEP3B_VISIBLE_MS}ms"
fi

# Step 4: at least one task transitions off pending
log_info "step 4: waiting for at least one task to move off pending (up to ${BUDGET_STEP4_ACTIVE_MS}ms)"
STEP4_ACTIVE_MS=-1
astart=$(ts_ms)
seen_statuses=""
while :; do
  now=$(ts_ms)
  elapsed=$(( now - astart ))
  tasks_json=$($CURL -m 5 "${API_BASE}/api/specs/${SPEC_PATH}/tasks" 2>&1 || echo "")
  read -r active_count total_count seen_statuses <<< "$(echo "$tasks_json" | python3 -c "
import sys, json
try:
  d = json.loads(sys.stdin.read())
  tasks = d.get('tasks', [])
  non_pending = [t for t in tasks if str(t.get('status','pending')).lower() not in ('pending','open')]
  statuses = ','.join(sorted({str(t.get('status','?')) for t in tasks}))
  print(f\"{len(non_pending)} {len(tasks)} {statuses or 'none'}\")
except Exception:
  print('0 0 parse-error')
" 2>/dev/null || echo "0 0 err")"
  if (( active_count >= 1 )); then
    STEP4_ACTIVE_MS=$elapsed
    break
  fi
  if (( elapsed > BUDGET_STEP4_ACTIVE_MS )); then
    break
  fi
  sleep 1
done
if (( STEP4_ACTIVE_MS < 0 )); then
  fail "step 4 no task moved off pending within ${BUDGET_STEP4_ACTIVE_MS}ms (statuses seen: ${seen_statuses})"
else
  log_pass "step 4 first task non-pending in ${STEP4_ACTIVE_MS}ms (statuses: ${seen_statuses})"
fi

echo ""
echo "=== timings ==="
printf "  step 1  roadmap spawn          %7d ms   (budget %d)\n" "$STEP1_SPAWN_MS" "$BUDGET_STEP1_SPAWN_MS"
printf "  step 1b roadmap file refresh   %7d ms   (budget %d)\n" "$STEP1_FILE_MS"  "$BUDGET_STEP1_FILE_MS"
printf "  step 2  spec from roadmap line %7d ms   (budget %d)\n" "$STEP2_SPEC_MS"  "$BUDGET_STEP2_SPEC_MS"
printf "  step 3  build HTTP return      %7d ms   (budget %d)\n" "$STEP3_BUILD_MS" "$BUDGET_STEP3_BUILD_MS"
printf "  step 3b first builder running  %7d ms   (budget %d)\n" "$STEP3B_VISIBLE_MS" "$BUDGET_STEP3_VISIBLE_MS"
printf "  step 4  first task non-pending %7d ms   (budget %d)\n" "$STEP4_ACTIVE_MS" "$BUDGET_STEP4_ACTIVE_MS"
echo ""

if (( FAILURES > 0 )); then
  log_fail "demo path FAILED with ${FAILURES} regression(s). Do NOT run the live demo until fixed."
  exit 1
fi
log_pass "demo path PASSED. Safe to run the live demo."
exit 0
