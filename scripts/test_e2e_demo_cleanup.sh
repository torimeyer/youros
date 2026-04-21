#!/usr/bin/env bash
# test_e2e_demo_cleanup.sh — regression test for purge_demo_residue in
# scripts/e2e_demo_path.sh.
#
# Seeds an isolated fake agent_state.json with a mix of demo-seeded rows
# and real rows, drops matching transcripts, runs the teardown in
# isolation via TEARDOWN_ONLY=1, then asserts:
#   - every demo-mode row is gone
#   - every 0-token demo transcript is deleted
#   - every real row is preserved untouched
#   - running the teardown a second time is a no-op (idempotent)
#
# Usage:
#   ./scripts/test_e2e_demo_cleanup.sh
# Exit 0 on PASS, 1 on any failure.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E_SCRIPT="${SCRIPT_DIR}/e2e_demo_path.sh"

if [ ! -x "$E2E_SCRIPT" ]; then
  echo "[fail] $E2E_SCRIPT is not executable"
  exit 1
fi

TMP_ROOT="$(mktemp -d -t e2e_demo_cleanup.XXXXXX)"
trap 'rm -rf "$TMP_ROOT"' EXIT
STATE_FILE="${TMP_ROOT}/agent_state_test.json"
TRANSCRIPTS_DIR="${TMP_ROOT}/transcripts"
mkdir -p "$TRANSCRIPTS_DIR"

PASS=0
FAIL=0
pass_check() { PASS=$(( PASS + 1 )); echo "  [pass] $*"; }
fail_check() { FAIL=$(( FAIL + 1 )); echo "  [FAIL] $*"; }

# 3 demo rows (different detection paths) + 2 real rows.
python3 - "$STATE_FILE" <<'PYEOF'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1])
state = {
  # demo #1: demo_mode: true and source: e2e-demo-path-... + spec prefix name
  "spec-e2e-demo-smoke-e2e-demo-path-12345-→999": {
    "source": "spec-build",
    "demo_mode": True,
    "status": "cancelled",
    "tokens_used": 0,
    "terminated_reason": "bulk cancel",
    "transcript_path": "transcripts/spec-e2e-demo-smoke-e2e-demo-path-12345-→999.md",
  },
  # demo #2: no demo_mode flag, but name prefix matches bulk-cancel mirror pattern
  "spec-e2e-demo-smoke-probe-initiative-→888": {
    "source": "claude-code",
    "status": "cancelled",
    "tokens_used": 0,
    "terminated_reason": "bulk cancel",
  },
  # demo #3: only source marks it as demo
  "roadmap-bot-12345": {
    "source": "e2e-demo-path-12345",
    "demo_mode": True,
    "status": "completed",
    "tokens_used": 0,
  },
  # real #1: live claude-code agent, tokens used, not demo
  "claude-code-real-session-1": {
    "source": "claude-code",
    "status": "running",
    "tokens_used": 4200,
  },
  # real #2: user-spawned agent with demo-sounding task but NOT flagged
  "fix-tooltip-overflow": {
    "source": "user-spawn",
    "status": "completed",
    "tokens_used": 1500,
  },
}
p.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
PYEOF

# Seed transcripts: one per demo agent (0-token), plus a real transcript.
: > "${TRANSCRIPTS_DIR}/spec-e2e-demo-smoke-e2e-demo-path-12345-→999.md"
: > "${TRANSCRIPTS_DIR}/spec-e2e-demo-smoke-probe-initiative-→888.md"
: > "${TRANSCRIPTS_DIR}/roadmap-bot-12345.md"
: > "${TRANSCRIPTS_DIR}/claude-code-real-session-1.md"
: > "${TRANSCRIPTS_DIR}/fix-tooltip-overflow.md"

echo "[test] seeded 5 agent rows + 5 transcripts at ${TMP_ROOT}"

# Run teardown in isolation.
AGENT_STATE_FILE="$STATE_FILE" \
TRANSCRIPTS_DIR="$TRANSCRIPTS_DIR" \
TEARDOWN_ONLY=1 \
  "$E2E_SCRIPT"
rc=$?
if [ "$rc" -ne 0 ]; then
  fail_check "teardown-only run returned non-zero (rc=${rc})"
fi

# Assert demo rows removed and real rows preserved.
python3 - "$STATE_FILE" <<'PYEOF'
import json, sys, pathlib
data = json.loads(pathlib.Path(sys.argv[1]).read_text())
removed = [
  "spec-e2e-demo-smoke-e2e-demo-path-12345-→999",
  "spec-e2e-demo-smoke-probe-initiative-→888",
  "roadmap-bot-12345",
]
preserved = ["claude-code-real-session-1", "fix-tooltip-overflow"]
fails = 0
for n in removed:
  if n in data:
    print(f"  [FAIL] demo row not removed: {n}")
    fails += 1
  else:
    print(f"  [pass] demo row removed: {n}")
for n in preserved:
  if n not in data:
    print(f"  [FAIL] real row was clobbered: {n}")
    fails += 1
  else:
    print(f"  [pass] real row preserved: {n}")
sys.exit(1 if fails else 0)
PYEOF
if [ $? -ne 0 ]; then FAIL=$(( FAIL + 1 )); else PASS=$(( PASS + 5 )); fi

# Assert transcripts: demo 0-token ones gone, real ones still there.
for f in \
  "spec-e2e-demo-smoke-e2e-demo-path-12345-→999.md" \
  "spec-e2e-demo-smoke-probe-initiative-→888.md" \
  "roadmap-bot-12345.md"; do
  if [ -f "${TRANSCRIPTS_DIR}/${f}" ]; then
    fail_check "demo transcript still present: ${f}"
  else
    pass_check "demo transcript deleted: ${f}"
  fi
done
for f in \
  "claude-code-real-session-1.md" \
  "fix-tooltip-overflow.md"; do
  if [ -f "${TRANSCRIPTS_DIR}/${f}" ]; then
    pass_check "real transcript preserved: ${f}"
  else
    fail_check "real transcript was deleted: ${f}"
  fi
done

# Idempotency: run teardown a second time, assert no error and no further change.
snapshot_before=$(python3 -c "import json,sys,pathlib;print(json.dumps(json.loads(pathlib.Path(sys.argv[1]).read_text()), sort_keys=True))" "$STATE_FILE")
AGENT_STATE_FILE="$STATE_FILE" \
TRANSCRIPTS_DIR="$TRANSCRIPTS_DIR" \
TEARDOWN_ONLY=1 \
  "$E2E_SCRIPT"
rc=$?
if [ "$rc" -ne 0 ]; then
  fail_check "second teardown run returned non-zero (rc=${rc})"
else
  pass_check "second teardown run exit 0"
fi
snapshot_after=$(python3 -c "import json,sys,pathlib;print(json.dumps(json.loads(pathlib.Path(sys.argv[1]).read_text()), sort_keys=True))" "$STATE_FILE")
if [ "$snapshot_before" = "$snapshot_after" ]; then
  pass_check "state file byte-identical across runs (idempotent)"
else
  fail_check "state file changed between runs (not idempotent)"
fi

echo ""
echo "[summary] pass=${PASS} fail=${FAIL}"
if [ "$FAIL" -gt 0 ]; then
  echo "FAIL"
  exit 1
fi
echo "PASS"
exit 0
