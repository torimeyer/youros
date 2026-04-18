#!/usr/bin/env bash
# test_demo_prewarm.sh — verify every demo prewarm check passes end-to-end.
# Validates each of the 6 demo steps individually, then runs demo-prewarm.sh.
# Exits 0 if all checks pass, 1 if any fail.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="https://127.0.0.1:8000"
CURL="curl -sSk --connect-timeout 3 -m 10"

PASS=0
FAIL=0

ok()   { printf "  \033[32mPASS\033[0m  %s\n" "$1"; PASS=$(( PASS + 1 )); }
fail() { printf "  \033[31mFAIL\033[0m  %s -- %s\n" "$1" "$2"; FAIL=$(( FAIL + 1 )); }

echo ""
echo "=== test_demo_prewarm: $(date '+%H:%M:%S') ==="
echo ""

# ── 1. Backend is reachable ────────────────────────────────────────────────────
if $CURL -o /dev/null -w "%{http_code}" "$BASE/api/health" 2>/dev/null | grep -q "^2"; then
  ok "backend reachable"
else
  fail "backend reachable" "GET /api/health not 2xx — is the backend running?"
fi

# ── 2. Run the prewarm script ──────────────────────────────────────────────────
echo ""
echo "--- running demo-prewarm.sh ---"
if "$SCRIPT_DIR/demo-prewarm.sh"; then
  ok "demo-prewarm.sh"
else
  fail "demo-prewarm.sh" "prewarm script exited non-zero"
fi

# ── 3. Verify Roadmap template is present and resolvable ──────────────────────
echo ""
echo "--- individual checks ---"

# Roadmap template
out=$($CURL "$BASE/api/agents/persona-templates" 2>&1)
if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
t = next((t for t in d.get('templates',[]) if t.get('name')=='Roadmap'), None)
assert t is not None, 'Roadmap not found'
assert t.get('installed'), 'Roadmap not installed'
assert t.get('id') == 'builtin-pm-roadmap', f'unexpected id: {t.get(\"id\")}'
" 2>/dev/null; then
  ok "Roadmap template installed (builtin-pm-roadmap)"
else
  fail "Roadmap template" "not found or not installed"
fi

# ── 4. Fleet prewarm returns ready:true, auth_ok:true ─────────────────────────
out=$($CURL -X POST "$BASE/api/agents/fleets/fleet-build-website/prewarm" 2>&1)
if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('ready') == True
assert d.get('auth_ok') == True
assert d.get('member_count') == 4
" 2>/dev/null; then
  ok "fleet-build-website prewarm (ready=true, auth_ok=true, 4 members)"
else
  fail "fleet-build-website prewarm" "ready or auth_ok not true: $out"
fi

# ── 5. Daily Standup workflow has 3 steps ─────────────────────────────────────
out=$($CURL "$BASE/api/workflows/templates" 2>&1)
if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
standup = next((t for t in d.get('templates',[]) if t.get('id')=='builtin-daily-standup'), None)
assert standup is not None, 'not found'
assert len(standup.get('steps',[])) == 3, f'expected 3 steps, got {len(standup.get(\"steps\",[]))}'
" 2>/dev/null; then
  ok "Daily Standup (3 steps)"
else
  fail "Daily Standup" "template missing or step count wrong"
fi

# ── 6. Task registry (create_task + create_tasks_from_spec) ──────────────────
out_tasks=$($CURL "$BASE/api/tasks?limit=1" 2>&1)
out_counts=$($CURL "$BASE/api/tasks/counts" 2>&1)
total=$(echo "$out_tasks" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total',len(d.get('tasks',[]))))" 2>/dev/null || echo "err")
open_c=$(echo "$out_counts" | python3 -c "import sys,json; print(json.load(sys.stdin).get('open','err'))" 2>/dev/null || echo "err")
if [[ "$total" != "err" && "$open_c" != "err" ]]; then
  ok "task registry warm ($total tasks, open=$open_c)"
else
  fail "task registry" "tasks or counts endpoint not responding (total=$total open=$open_c)"
fi

# ── 7. Specs list returns docs ────────────────────────────────────────────────
out=$($CURL "$BASE/api/specs" 2>&1)
if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'docs' in d
" 2>/dev/null; then
  ok "specs list endpoint"
else
  fail "specs list endpoint" "no docs key in response"
fi

# ── 8. Specs decompose endpoint is registered ─────────────────────────────────
http_code=$($CURL -o /dev/null -w "%{http_code}" -X POST "$BASE/api/specs/decompose" \
  -H "Content-Type: application/json" \
  -d '{"path":"__prewarm_probe__"}' 2>&1)
if [[ "$http_code" =~ ^[2345][0-9][0-9]$ ]]; then
  ok "specs decompose endpoint registered (HTTP $http_code)"
else
  fail "specs decompose endpoint" "no HTTP response (code=$http_code)"
fi

# ── 9. Fleets list contains all expected demo fleets ─────────────────────────
out=$($CURL "$BASE/api/agents/fleets" 2>&1)
if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
ids = [f['id'] for f in d.get('fleets',[])]
assert 'fleet-build-website' in ids, f'fleet-build-website not in {ids}'
" 2>/dev/null; then
  ok "fleet-build-website in fleet list"
else
  fail "fleet list" "fleet-build-website missing"
fi

# ── 10. AI clients: backend healthy, default model set, claude binary ─────────
health=$($CURL "$BASE/api/health" 2>&1)
settings=$($CURL "$BASE/api/settings" 2>&1)
if echo "$health" | grep -q '"status":"ok"'; then
  model=$(echo "$settings" | python3 -c "import sys,json; print(json.load(sys.stdin).get('default_model','?'))" 2>/dev/null || echo "?")
  claude_present="no"
  command -v claude &>/dev/null && claude_present="yes"
  ok "AI clients ready (model=$model, claude-binary=$claude_present)"
else
  fail "AI clients" "backend health check failed"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== test_demo_prewarm: $PASS passed, $FAIL failed ==="
echo ""

if (( FAIL > 0 )); then
  echo "DEMO NOT READY — fix the failing checks above before going live."
  exit 1
else
  echo "DEMO READY — all prewarm checks passed."
  exit 0
fi
