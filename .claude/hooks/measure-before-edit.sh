#!/bin/bash
HOOK_NAME=$(basename "$0")
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL:-?} exit=$?" >> /tmp/hook-trace.log' EXIT
_HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/deny.sh
source "$_HOOKS_DIR/lib/deny.sh"
init_deny_traps
# PreToolUse Edit|Write: non-blocking reminder to measure before editing a perf bug.
# Rule 1 from 2026-04-27 retro. See feedback_measure_cold_workload_before_editing.md.

INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)
case "$TOOL" in
  Edit|Write) ;;
  *) exit 0 ;;
esac

# Get the last user message. MYOS_TEST_LAST_MSG overrides for tests.
if [ -n "${MYOS_TEST_LAST_MSG:-}" ]; then
  MSG="$MYOS_TEST_LAST_MSG"
else
  PROJ_SLUG=$(echo "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}" | tr '/' '-' | sed 's/^-//')
  LAST=$(ls -t "$HOME/.claude/projects/${PROJ_SLUG}/"*.jsonl 2>/dev/null | head -1)
  [ -z "$LAST" ] && exit 0
  MSG=$(grep '"type":"user"' "$LAST" | tail -1 | python3 -c "
import sys, json
line = sys.stdin.read().strip()
if not line:
    sys.exit(0)
d = json.loads(line)
c = d.get('message', {}).get('content', '')
if isinstance(c, list):
    c = ' '.join(item.get('text','') if isinstance(item,dict) else str(item) for item in c)
print(c.lower())
" 2>/dev/null || echo "")
fi

# Check for perf-bug keywords.
case "$MSG" in
  *slow*|*hang*|*blocks*|*timeout*|*blocking*|*freeze*|*latency*|*stuck*) ;;
  *) exit 0 ;;
esac

# Skip if a timing script ran recently (within 15 minutes).
# MYOS_TIMING_DONE_STAMP overrides the stamp path for tests.
TIMING_FLAG="${MYOS_TIMING_DONE_STAMP:-${HOME}/.myos/hooks/timing-done.stamp}"
if [ -f "$TIMING_FLAG" ]; then
  STAMP_AGE=$(python3 -c "import os,time; print(int(time.time()-os.path.getmtime('${TIMING_FLAG}')))" 2>/dev/null || echo 9999)
  if [ "$STAMP_AGE" -lt 900 ]; then
    exit 0
  fi
fi

echo ""
echo "PERF-BUG REMINDER (non-blocking -- rule 1 from 2026-04-27 retro):"
echo "  Perf issue detected in context. Measure the hot path before editing:"
echo "    python3 -c \"import time; t=time.perf_counter(); <suspect_call>; print(time.perf_counter()-t)\""
echo "  Get a baseline number first, then edit. See feedback_measure_cold_workload_before_editing.md."
exit 0
