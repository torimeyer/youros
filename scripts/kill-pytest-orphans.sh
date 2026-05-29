#!/usr/bin/env bash
# kill-pytest-orphans.sh — find and kill detached pytest processes (ppid=1)
# older than N minutes that escaped their parent.
#
# Usage:
#   ./scripts/kill-pytest-orphans.sh            # dry-run: list but do not kill
#   ./scripts/kill-pytest-orphans.sh --apply    # kill them
#   ./scripts/kill-pytest-orphans.sh --minutes 30  # override age threshold
#
# Safe to run repeatedly (idempotent). Targets only processes matching:
#   - command contains "pytest" or "-m pytest"
#   - ppid=1 (reparented to launchd/init)
#   - running for at least AGE_MINUTES minutes

set -euo pipefail

DRY_RUN=1
AGE_MINUTES=10

while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply)    DRY_RUN=0; shift ;;
        --minutes)  AGE_MINUTES="$2"; shift 2 ;;
        *)          echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

say() { printf '[orphan-reaper] %s\n' "$*"; }

if [[ "$DRY_RUN" -eq 1 ]]; then
    say "DRY RUN — pass --apply to kill. Age threshold: ${AGE_MINUTES}m"
else
    say "LIVE RUN — killing orphans older than ${AGE_MINUTES}m"
fi

# ps output: PID PPID ETIME COMMAND (BSD-style, macOS compatible)
# etime format: [[dd-]hh:]mm:ss
FOUND=0

while IFS= read -r line; do
    pid=$(echo "$line" | awk '{print $1}')
    ppid=$(echo "$line" | awk '{print $2}')
    etime=$(echo "$line" | awk '{print $3}')
    cmd=$(echo "$line" | cut -d' ' -f4-)

    # Only care about ppid=1 (reparented to init/launchd)
    [[ "$ppid" == "1" ]] || continue

    # Parse etime into total minutes: [[dd-]hh:]mm:ss
    total_mins=0
    if [[ "$etime" =~ ^([0-9]+)-([0-9]+):([0-9]+):([0-9]+)$ ]]; then
        # dd-hh:mm:ss
        total_mins=$(( ${BASH_REMATCH[1]} * 1440 + ${BASH_REMATCH[2]} * 60 + ${BASH_REMATCH[3]} ))
    elif [[ "$etime" =~ ^([0-9]+):([0-9]+):([0-9]+)$ ]]; then
        # hh:mm:ss
        total_mins=$(( ${BASH_REMATCH[1]} * 60 + ${BASH_REMATCH[2]} ))
    elif [[ "$etime" =~ ^([0-9]+):([0-9]+)$ ]]; then
        # mm:ss
        total_mins=${BASH_REMATCH[1]}
    fi

    [[ "$total_mins" -ge "$AGE_MINUTES" ]] || continue

    FOUND=1
    say "orphan pid=$pid ppid=$ppid etime=$etime age=${total_mins}m cmd=$cmd"

    if [[ "$DRY_RUN" -eq 0 ]]; then
        if kill -0 "$pid" 2>/dev/null; then
            kill -SIGKILL "$pid" 2>/dev/null && say "  killed $pid" || say "  kill failed for $pid (already gone)"
        else
            say "  $pid already gone"
        fi
    fi
done < <(ps -eo pid,ppid,etime,command | grep -E 'pytest|-m pytest' | grep -v grep | grep -v kill-pytest-orphans || true)

if [[ "$FOUND" -eq 0 ]]; then
    say "no orphaned pytest processes found (ppid=1, age>=${AGE_MINUTES}m)"
fi
