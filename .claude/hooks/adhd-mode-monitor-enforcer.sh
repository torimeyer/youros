#!/bin/bash
# PreToolUse hook: enforce Monitor pairing when ADHD mode is active.
#
# Two operating modes dispatched on tool_name:
#
#   Monitor → write a sentinel so a subsequent Agent check can see it was armed
#   Agent   → deny if ~/.myos/.adhd_mode exists and no Monitor was armed
#             within the last 120 seconds in this session
#
# Sentinel path: ~/.myos/.adhd-monitor-armed-<session_id>
#
# Fast path: no network calls, no blocking I/O. Typical: <100ms. Max: <2s.
# Per feedback_adhd_mode_auto_arm_monitor.md: when .adhd_mode exists, every
# Agent spawn MUST be paired with a Monitor in the same turn. No exceptions.

set -u
HOOK_NAME=$(basename "$0")
_HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_HOOKS_DIR/lib/deny.sh"
init_deny_traps
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL:-?} exit=$?" >> /tmp/hook-trace.log' EXIT

INPUT=$(cat)

# Single Python parse: tool_name + session_id + sentinel path + sentinel age.
# Doing everything in one invocation keeps us well under the 200ms budget.
PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, json, sys, time

US = "\x1f"
try:
    d = json.loads(os.environ.get("INPUT_JSON", "") or "{}", strict=False)
except Exception:
    sys.exit(0)

tool = (d.get("tool_name") or "").strip()
session_id = (
    (d.get("session_id") or "").strip()
    or os.environ.get("CLAUDE_SESSION_ID", "").strip()
    or "default"
)

home = os.path.expanduser("~")
sentinel = os.path.join(home, ".myos", f".adhd-monitor-armed-{session_id}")

age = -1  # -1 = sentinel absent
if os.path.exists(sentinel):
    try:
        age = int(time.time() - os.path.getmtime(sentinel))
    except Exception:
        age = 9999

sys.stdout.write(f"{tool}{US}{sentinel}{US}{age}")
PY
)

if [ -z "$PARSED" ]; then
    exit 0
fi

IFS=$'\x1f' read -r TOOL SENTINEL SENTINEL_AGE <<<"$PARSED"

case "$TOOL" in
    Monitor)
        # Record that Monitor was armed; the Agent check path reads this.
        mkdir -p "$(dirname "$SENTINEL")" 2>/dev/null || true
        touch "$SENTINEL" 2>/dev/null || true
        exit 0
        ;;
    Agent)
        # Fast out: ADHD mode not active.
        if [ ! -f "$HOME/.myos/.adhd_mode" ]; then
            exit 0
        fi

        # Sentinel present and < 120s old → Monitor was recently armed, allow.
        if [ "$SENTINEL_AGE" -ge 0 ] && [ "$SENTINEL_AGE" -lt 120 ] 2>/dev/null; then
            exit 0
        fi

        deny "ADHD mode is active. Arm a Monitor in the same turn as this Agent spawn (per feedback_adhd_mode_auto_arm_monitor.md). Add a Monitor call BEFORE this Agent call — default 60s heartbeat polling /api/agents. No Monitor detected in the last 120 seconds for this session."
        ;;
    *)
        exit 0
        ;;
esac
