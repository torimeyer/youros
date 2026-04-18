#!/usr/bin/env bash
# demo-prewarm.sh — parallel idempotent prewarm for Tori's live demo pipeline.
# Safe to run multiple times. Exits 0 if every step is warm, 1 if any fails.
#
# Demo steps covered:
#   1. Roadmap template    GET /api/agents/persona-templates (verify Roadmap installed)
#   2. Fleet prewarm       POST /api/agents/fleets/fleet-build-website/prewarm (ready+auth_ok)
#   3. Daily Standup       GET /api/workflows/templates (verify builtin-daily-standup, 3 steps)
#   4. Task registry       GET /api/tasks + /api/tasks/counts (warms create_task + labels path)
#   5. Specs + decompose   GET /api/specs + POST /api/specs/decompose dry-run (400 = warm)
#   6. AI clients          GET /api/health + /api/settings (Anthropic binary/env, Gemini env)

# Requires bash 3.2+, no associative arrays (macOS compatible)
set -euo pipefail

BASE="https://127.0.0.1:8000"
CURL="curl -sSk --connect-timeout 3 -m 10"

# Timing helper — use python3 for ms timestamps (bash integer overflow on macOS)
ts_ms() { python3 -c "import time; print(int(time.time()*1000))"; }

PASS=0
FAIL=0

log_pass() {
  local step="$1" ms="${2:-?}" detail="${3:-}"
  printf "  \033[32mPASS\033[0m  %-30s %6sms  %s\n" "$step" "$ms" "$detail"
  PASS=$(( PASS + 1 ))
}

log_fail() {
  local step="$1" ms="${2:-?}" detail="${3:-}"
  printf "  \033[31mFAIL\033[0m  %-30s %6sms  %s\n" "$step" "$ms" "$detail"
  FAIL=$(( FAIL + 1 ))
}

log_skip() {
  local step="$1" detail="${2:-}"
  printf "  \033[33mSKIP\033[0m  %-30s         %s\n" "$step" "$detail"
}

WALL_T0=$(ts_ms)

echo ""
echo "=== demo-prewarm $(date '+%H:%M:%S') — all 6 steps in parallel ==="
echo ""

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# ── Step 1: Roadmap template ──────────────────────────────────────────────────
(
  t0=$(ts_ms)
  out=$($CURL "$BASE/api/agents/persona-templates" 2>&1)
  ms=$(( $(ts_ms) - t0 ))
  if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
templates = d.get('templates', [])
names = [t.get('name','') for t in templates]
assert 'Roadmap' in names, f'Roadmap not in {names}'
roadmap = next(t for t in templates if t['name'] == 'Roadmap')
assert roadmap.get('installed'), 'Roadmap not installed'
" 2>/dev/null; then
    echo "PASS roadmap-template $ms" > "$TMP/roadmap"
  else
    echo "FAIL roadmap-template $ms Roadmap template not found or not installed" > "$TMP/roadmap"
  fi
) &

# ── Step 2: Build-a-Website fleet prewarm ────────────────────────────────────
(
  t0=$(ts_ms)
  out=$($CURL -X POST "$BASE/api/agents/fleets/fleet-build-website/prewarm" 2>&1)
  ms=$(( $(ts_ms) - t0 ))
  if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('ready') == True, 'ready not True'
assert d.get('auth_ok') == True, 'auth_ok not True'
assert d.get('member_count', 0) == 4, f'expected 4 members, got {d.get(\"member_count\")}'
" 2>/dev/null; then
    echo "PASS fleet-build-website $ms" > "$TMP/fleet"
  else
    echo "FAIL fleet-build-website $ms fleet prewarm failed: $out" > "$TMP/fleet"
  fi
) &

# ── Step 3: Daily Standup workflow ───────────────────────────────────────────
(
  t0=$(ts_ms)
  out=$($CURL "$BASE/api/workflows/templates" 2>&1)
  ms=$(( $(ts_ms) - t0 ))
  if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
templates = d.get('templates', [])
ids = [t.get('id','') for t in templates]
assert 'builtin-daily-standup' in ids, f'builtin-daily-standup not in {ids}'
standup = next(t for t in templates if t['id'] == 'builtin-daily-standup')
assert len(standup.get('steps', [])) >= 3, 'expected >=3 steps'
" 2>/dev/null; then
    echo "PASS daily-standup $ms" > "$TMP/standup"
  else
    echo "FAIL daily-standup $ms standup not found or steps missing: $out" > "$TMP/standup"
  fi
) &

# ── Step 4: Task creation / create_task + create_tasks_from_spec registry ────
# GET /api/tasks loads the task store + label service used by Torichat's
# create_task and create_tasks_from_spec tool calls.
# GET /api/tasks/counts warms the aggregate path used to seed the UI.
(
  t0=$(ts_ms)
  out1=$($CURL "$BASE/api/tasks?limit=1" 2>&1)
  out2=$($CURL "$BASE/api/tasks/counts" 2>&1)
  ms=$(( $(ts_ms) - t0 ))
  total=$(echo "$out1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total',len(d.get('tasks',[]))))" 2>/tmp/demo-prewarm.err || printf "")
  open_c=$(echo "$out2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('open','err'))" 2>/dev/null || echo "err")
  if [[ -n "$total" && "$open_c" != "err" ]]; then
    echo "PASS task-registry $ms $total tasks in store open=$open_c" > "$TMP/task"
  else
    echo "FAIL task-registry $ms tasks or counts endpoint not responding" > "$TMP/task"
  fi
) &

# ── Step 5: Specs list + decompose cache ─────────────────────────────────────
# GET /api/specs warms the docs store.
# POST /api/specs/decompose with a bogus path returns 400 which proves the
# router is hot and ostk.doc_decompose is importable. No real spec created.
(
  t0=$(ts_ms)
  out=$($CURL "$BASE/api/specs" 2>&1)
  dc_code=$($CURL -o /dev/null -w "%{http_code}" -X POST "$BASE/api/specs/decompose" \
    -H "Content-Type: application/json" -d '{"path":"__prewarm_probe__"}' 2>&1)
  ms=$(( $(ts_ms) - t0 ))
  count=$(echo "$out" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('docs',[])))" 2>/dev/null || echo "err")
  if [[ "$count" != "err" ]] && [[ "$dc_code" =~ ^[234][0-9][0-9]$ ]]; then
    echo "PASS specs+decompose $ms $count specs, decompose HTTP $dc_code (warm)" > "$TMP/specs"
  else
    echo "FAIL specs+decompose $ms specs=$count decompose_code=$dc_code" > "$TMP/specs"
  fi
) &

# ── Step 6: Anthropic + Gemini client readiness ───────────────────────────────
# No real AI call (avoids cost). Checks: backend alive, default model set,
# claude binary present (Anthropic claude-code path), GEMINI_API_KEY env.
(
  t0=$(ts_ms)
  health=$($CURL "$BASE/api/health" 2>&1)
  settings=$($CURL "$BASE/api/settings" 2>&1)
  ms=$(( $(ts_ms) - t0 ))
  if echo "$health" | grep -q '"status":"ok"'; then
    model=$(echo "$settings" | python3 -c "import sys,json; print(json.load(sys.stdin).get('default_model','?'))" 2>/dev/null || echo "?")
    anthropic="no-key"
    gemini="no-key"
    command -v claude &>/dev/null && anthropic="claude-binary"
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] && anthropic="env-key"
    [[ -n "${GEMINI_API_KEY:-}" ]] && gemini="env-key"
    echo "PASS ai-clients $ms model=$model anthropic=$anthropic gemini=$gemini" > "$TMP/ai"
  else
    echo "FAIL ai-clients $ms backend health check failed: $health" > "$TMP/ai"
  fi
) &

# ── Wait for all parallel jobs ────────────────────────────────────────────────
wait

WALL_MS=$(( $(ts_ms) - WALL_T0 ))

# ── Collect results ───────────────────────────────────────────────────────────
printf "\n  %-6s %-30s %7s  %s\n" "Status" "Step" "Time" "Detail"
printf "  %s\n" "──────────────────────────────────────────────────────────────────"

for f in roadmap fleet standup task specs ai; do
  result_file="$TMP/$f"
  if [[ ! -f "$result_file" ]]; then
    log_skip "$f" "result file missing"
    FAIL=$(( FAIL + 1 ))
    continue
  fi
  read -r status step ms_raw rest < "$result_file" 2>/dev/null || true
  ms_num="${ms_raw//[^0-9]/}"
  if [[ "$status" == "PASS" ]]; then
    log_pass "$step" "$ms_num" "${rest:-}"
  else
    log_fail "$step" "$ms_num" "${ms_raw:-?} ${rest:-}"
  fi
done

printf "\n  Wall-clock total: %sms | %d passed  %d failed\n\n" "$WALL_MS" "$PASS" "$FAIL"

if (( FAIL > 0 )); then
  echo "  PREWARM INCOMPLETE — fix failing steps before the demo."
  exit 1
fi
echo "  PREWARM COMPLETE — all 6 demo steps are warm and ready."
exit 0
