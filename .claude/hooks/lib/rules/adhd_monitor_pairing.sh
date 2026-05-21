#!/usr/bin/env bash
# Rule: adhd_monitor_pairing
# Replaces: adhd-mode-monitor-enforcer.sh
# Two functions: arm (Monitor path) and check (Agent path).
# Called from: pre-tool-guard.sh (Monitor arm) and pre-agent-guard.sh (Agent check).
# Assumes: load-rule.sh, log-fire.sh, deny.sh already sourced by caller.

# _adhd_monitor_pairing_arm: called when Monitor tool fires.
# Writes the sentinel and spawns a keepalive process that re-touches it every
# floor(ttl/2) seconds (≥30s). The keepalive lets _check detect that a Monitor
# is still running even when the sentinel age exceeds the TTL.
# Self-limits to 8 hours (480 × refresh interval) to avoid permanent orphans.
# Args: $1=tool $2=sentinel_path
_adhd_monitor_pairing_arm() {
  local tool="${1:-Monitor}" sentinel="${2:-}"

  if [ -z "$sentinel" ]; then
    log_rule_fire "adhd_monitor_pairing" "$tool" "allow" "no sentinel path (arm skipped)"
    return 0
  fi

  mkdir -p "$(dirname "$sentinel")" 2>/dev/null || true
  touch "$sentinel" 2>/dev/null || true

  # Kill any existing keepalive for this sentinel before spawning a new one.
  local pid_file="${sentinel}.keepalive.pid"
  if [ -f "$pid_file" ]; then
    local old_pid
    old_pid=$(cat "$pid_file" 2>/dev/null || true)
    [ -n "$old_pid" ] && kill "$old_pid" 2>/dev/null || true
    rm -f "$pid_file" 2>/dev/null || true
  fi

  local ttl
  ttl=$(rule_param "adhd_monitor_pairing.sentinel_ttl_seconds" "120")
  local refresh=$(( (ttl / 2) < 30 ? 30 : (ttl / 2) ))
  local max_iters=480  # 480 × refresh ≈ 8 hours at default 60s

  (
    i=0
    while [ "$i" -lt "$max_iters" ]; do
      sleep "$refresh"
      [ -f "$sentinel" ] || exit 0
      touch "$sentinel" 2>/dev/null || exit 0
      i=$(( i + 1 ))
    done
  ) &
  disown
  echo $! > "$pid_file" 2>/dev/null || true

  log_rule_fire "adhd_monitor_pairing" "$tool" "allow" "sentinel armed at $sentinel (keepalive pid=$(cat "$pid_file" 2>/dev/null || echo ?))"
  return 0
}

# _adhd_monitor_pairing_disarm: called from PostToolUse when Monitor returns an
# internal error. Kills the keepalive and removes the sentinel so the next Agent
# spawn is blocked, forcing pclaude to arm a working Monitor or ScheduleWakeup.
# Args: $1=tool $2=sentinel_path $3=reason
_adhd_monitor_pairing_disarm() {
  local tool="${1:-Monitor}" sentinel="${2:-}" reason="${3:-monitor failed}"

  if [ -z "$sentinel" ]; then
    log_rule_fire "adhd_monitor_pairing" "$tool" "allow" "disarm: no sentinel path"
    return 0
  fi

  local pid_file="${sentinel}.keepalive.pid"
  if [ -f "$pid_file" ]; then
    local ka_pid
    ka_pid=$(cat "$pid_file" 2>/dev/null || true)
    [ -n "$ka_pid" ] && kill "$ka_pid" 2>/dev/null || true
    rm -f "$pid_file" 2>/dev/null || true
  fi

  rm -f "$sentinel" 2>/dev/null || true
  log_rule_fire "adhd_monitor_pairing" "$tool" "warn" "sentinel disarmed (${reason})"
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

  # Sentinel is stale (or absent). Check if the keepalive process is still
  # alive — a live keepalive means a Monitor was armed this session and is
  # still running. If so, refresh the sentinel and allow.
  if [ -n "$sentinel" ] && [ -f "$sentinel" ]; then
    local pid_file="${sentinel}.keepalive.pid"
    if [ -f "$pid_file" ]; then
      local ka_pid
      ka_pid=$(cat "$pid_file" 2>/dev/null || true)
      if [ -n "$ka_pid" ] && kill -0 "$ka_pid" 2>/dev/null; then
        touch "$sentinel" 2>/dev/null || true
        log_rule_fire "adhd_monitor_pairing" "$tool" "allow" "monitor keepalive alive (pid=${ka_pid}, sentinel was ${sentinel_age}s old) — refreshed"
        return 0
      fi
    fi
  fi

  local reason="ADHD mode is active. Arm a Monitor in the same turn as this Agent spawn (per feedback_adhd_mode_auto_arm_monitor.md). Add a Monitor call BEFORE this Agent call — default 60s heartbeat polling /api/agents. No Monitor detected in the last ${ttl:-120} seconds for this session."
  log_rule_fire "adhd_monitor_pairing" "$tool" "block" "ADHD mode active, no fresh Monitor sentinel"
  deny "$reason"
}
