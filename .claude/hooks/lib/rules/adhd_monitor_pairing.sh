#!/usr/bin/env bash
# Rule: adhd_monitor_pairing
# Replaces: adhd-mode-monitor-enforcer.sh
# Two functions: arm (Monitor path) and check (Agent path).
# Called from: pre-tool-guard.sh (Monitor arm) and pre-agent-guard.sh (Agent check).
# Assumes: load-rule.sh, log-fire.sh, deny.sh already sourced by caller.

# _adhd_monitor_pairing_arm: called when Monitor tool fires.
# Writes the sentinel so a subsequent Agent check can see it was armed.
# Args: $1=tool $2=sentinel_path
_adhd_monitor_pairing_arm() {
  local tool="${1:-Monitor}" sentinel="${2:-}"

  if [ -z "$sentinel" ]; then
    log_rule_fire "adhd_monitor_pairing" "$tool" "allow" "no sentinel path (arm skipped)"
    return 0
  fi

  mkdir -p "$(dirname "$sentinel")" 2>/dev/null || true
  touch "$sentinel" 2>/dev/null || true

  log_rule_fire "adhd_monitor_pairing" "$tool" "allow" "sentinel armed at $sentinel"
  return 0
}

# _adhd_monitor_pairing_check: called when Agent tool fires.
# Denies if ADHD mode active and no Monitor was armed recently.
# Args: $1=tool $2=sentinel_path $3=sentinel_age_seconds (-1 if absent)
_adhd_monitor_pairing_check() {
  local tool="${1:-Agent}" sentinel="${2:-}" sentinel_age="${3:--1}"
  local ttl
  ttl=$(rule_param "adhd_monitor_pairing.sentinel_ttl_seconds" "120")

  # Fast out: ADHD mode not active.
  if [ ! -f "$HOME/.myos/.adhd_mode" ]; then
    log_rule_fire "adhd_monitor_pairing" "$tool" "allow" "adhd_mode not active"
    return 0
  fi

  # Sentinel present and within TTL → Monitor was recently armed, allow.
  if [ "$sentinel_age" -ge 0 ] 2>/dev/null && [ "$sentinel_age" -lt "${ttl:-120}" ] 2>/dev/null; then
    log_rule_fire "adhd_monitor_pairing" "$tool" "allow" "monitor sentinel ${sentinel_age}s old (within ${ttl}s TTL)"
    return 0
  fi

  local reason="ADHD mode is active. Arm a Monitor in the same turn as this Agent spawn (per feedback_adhd_mode_auto_arm_monitor.md). Add a Monitor call BEFORE this Agent call — default 60s heartbeat polling /api/agents. No Monitor detected in the last ${ttl:-120} seconds for this session."
  log_rule_fire "adhd_monitor_pairing" "$tool" "block" "ADHD mode active, no fresh Monitor sentinel"
  deny "$reason"
}
