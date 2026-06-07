#!/usr/bin/env bash
# ostk_daemon_watchdog.sh
#
# Detects and recovers from a saturated ostk daemon (→1527).
#
# Two saturation signals:
#   1. PTY fd leak: each shell.run command via PTY opens /dev/ptmx on the
#      master side. If the master FD is not closed after the subprocess exits,
#      macOS kqueue keeps signalling EVFILT_READ (EOF is re-armed). Tokio's
#      runtime polls endlessly → 80%+ CPU after days of accumulation.
#      Confirmed in daemon-6.0.5 binary (src/serve/tools/sh_spawn.rs).
#   2. Binary inode mismatch: ostk was upgraded while the daemon was live.
#      The running process holds the old inode open; the new binary at the
#      same path has a different inode. Run `ostk daemon restart` to swap in
#      the new binary.
#
# Recovery: `ostk daemon restart` run from the daemon's CWD — stops that
# daemon and clears its cached binary so the next RPC spawns a fresh one.
#
# Usage:
#   scripts/ostk_daemon_watchdog.sh             # scan all daemon pids
#   scripts/ostk_daemon_watchdog.sh --dry-run   # scan only, no restart
#
# Tunables (env vars):
#   OSTK_DAEMON_PTY_WARN_THRESHOLD      warn level (default 20)
#   OSTK_DAEMON_PTY_CRITICAL_THRESHOLD  restart level (default 60)
#   OSTK_DAEMON_WATCHDOG_LOGFILE        log path (default /tmp/ostk-daemon-watchdog.log)
#   LSOF_CMD                            override lsof binary for tests

set -euo pipefail

LOGFILE="${OSTK_DAEMON_WATCHDOG_LOGFILE:-/tmp/ostk-daemon-watchdog.log}"
DRY_RUN=false
PTY_WARN="${OSTK_DAEMON_PTY_WARN_THRESHOLD:-20}"
PTY_CRITICAL="${OSTK_DAEMON_PTY_CRITICAL_THRESHOLD:-60}"
LSOF="${LSOF_CMD:-lsof}"

# Tick: record each run start so launchd scheduling can be verified
echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] watchdog tick pid=$$" >> ~/.youros/logs/watchdog.log

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true ; shift ;;
    *) echo "Unknown argument: $1" >&2 ; exit 1 ;;
  esac
done

log() {
  local msg="[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] ostk-daemon-watchdog: $*"
  echo "$msg" | tee -a "$LOGFILE"
}

# Count /dev/ptmx master FDs held by a daemon PID.
count_ptmx_fds() {
  local pid="$1"
  "$LSOF" -p "$pid" 2>/dev/null | grep -c '/dev/ptmx' || true
}

# Inode of the binary a PID is currently executing (from lsof NODE column).
running_binary_inode() {
  local pid="$1"
  "$LSOF" -p "$pid" 2>/dev/null \
    | awk '$4 == "txt" && $NF ~ /daemon-/ {print $8; exit}'
}

# Inode of the binary at the path the PID was launched from.
installed_binary_inode() {
  local pid="$1"
  local path
  path=$("$LSOF" -p "$pid" 2>/dev/null \
    | awk '$4 == "txt" && $NF ~ /daemon-/ {print $NF; exit}')
  [[ -n "$path" ]] && stat -f "%i" "$path" 2>/dev/null || echo "0"
}

# CWD of a running PID.
pid_cwd() {
  "$LSOF" -p "$1" 2>/dev/null | awk '$4 == "cwd" {print $NF; exit}'
}

# All running ostk daemon PIDs.
# pgrep on macOS uses BRE (not ERE) — '+' is literal. Use ps aux instead.
find_daemon_pids() {
  ps aux | awk '/\/daemon-[0-9]/ && !/awk/' | awk '{print $2}'
}

# Inspect one PID. Returns 0 if a restart was triggered/would be triggered.
check_daemon() {
  local pid="$1"
  local restart_reason=""

  local pty_count
  pty_count=$(count_ptmx_fds "$pid")

  if [[ "$pty_count" -ge "$PTY_CRITICAL" ]]; then
    log "CRITICAL: pid $pid has $pty_count PTY fds (threshold=$PTY_CRITICAL)"
    restart_reason="pty_leak:${pty_count}"
  elif [[ "$pty_count" -ge "$PTY_WARN" ]]; then
    log "WARN: pid $pid has $pty_count PTY fds (warn=${PTY_WARN}, critical=${PTY_CRITICAL})"
  fi

  local running_inode installed_inode
  running_inode=$(running_binary_inode "$pid")
  installed_inode=$(installed_binary_inode "$pid")
  if [[ -n "$running_inode" && -n "$installed_inode" \
        && "$running_inode" != "0" && "$installed_inode" != "0" \
        && "$running_inode" != "$installed_inode" ]]; then
    log "STALE: pid $pid binary inode $running_inode != installed $installed_inode"
    [[ -z "$restart_reason" ]] && restart_reason="stale_binary"
  fi

  if [[ -z "$restart_reason" ]]; then
    log "OK: pid $pid pty_fds=${pty_count}"
    return 1
  fi

  local cwd
  cwd=$(pid_cwd "$pid")
  if [[ -z "$cwd" ]]; then
    log "WARN: cannot determine CWD for pid $pid — skipping restart"
    return 0
  fi

  if $DRY_RUN; then
    log "DRY-RUN: would run 'ostk daemon restart' in $cwd (reason=$restart_reason)"
  else
    log "Restarting daemon (pid=$pid, reason=$restart_reason, cwd=$cwd)"
    (cd "$cwd" && ostk daemon restart 2>&1) \
      | while IFS= read -r line; do log "  [restart] $line"; done \
      || log "WARN: ostk daemon restart exited non-zero"
  fi
  return 0
}

main() {
  local pids
  pids=$(find_daemon_pids)

  if [[ -z "$pids" ]]; then
    log "No ostk daemon processes found."
    return 0
  fi

  local restarted=0
  for pid in $pids; do
    if check_daemon "$pid"; then
      restarted=$((restarted + 1))
    fi
  done

  log "Scan complete. pids_checked=$(echo "$pids" | wc -w | tr -d ' ') restarted=${restarted}"
}

main
