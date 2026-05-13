#!/usr/bin/env bash
# Rule: measure_before_edit
# Replaces: measure-before-edit.sh (PreToolUse Edit|Write)
# Called from: pre-tool-guard.sh on Edit|Write
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.
# Non-blocking: always returns 0. Only emits a hint.

_measure_before_edit_check() {
  local tool="$1" msg="${2:-}"

  # Check for perf-bug keywords in last user message.
  local perf_hit=0
  case "$msg" in
    *slow*|*hang*|*blocks*|*timeout*|*blocking*|*freeze*|*latency*|*stuck*) perf_hit=1 ;;
  esac

  if [ "$perf_hit" -eq 0 ]; then
    log_rule_fire "measure_before_edit" "$tool" "allow" "no perf keywords in context"
    return 0
  fi

  # Skip if a timing script ran recently (within 15 minutes).
  local TIMING_FLAG="${MYOS_TIMING_DONE_STAMP:-${HOME}/.myos/hooks/timing-done.stamp}"
  if [ -f "$TIMING_FLAG" ]; then
    local STAMP_AGE
    STAMP_AGE=$(python3 -c "import os,time; print(int(time.time()-os.path.getmtime('${TIMING_FLAG}')))" 2>/dev/null || echo 9999)
    if [ "$STAMP_AGE" -lt 900 ]; then
      log_rule_fire "measure_before_edit" "$tool" "allow" "timing done recently (${STAMP_AGE}s ago)"
      return 0
    fi
  fi

  echo ""
  echo "PERF-BUG REMINDER (non-blocking -- rule 1 from 2026-04-27 retro):"
  echo "  Perf issue detected in context. Measure the hot path before editing:"
  echo "    python3 -c \"import time; t=time.perf_counter(); <suspect_call>; print(time.perf_counter()-t)\""
  echo "  Get a baseline number first, then edit. See feedback_measure_cold_workload_before_editing.md."

  log_rule_fire "measure_before_edit" "$tool" "allow" "reminder emitted (perf keywords present)"
  return 0
}
