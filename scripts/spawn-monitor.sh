#!/usr/bin/env bash
# spawn-monitor.sh
#
# One-shot agent completion monitor. Arm this in a Monitor tool call
# immediately after spawning an agent so the parent session is notified
# the moment the agent finishes.
#
# Usage (explicit name):
#   bash scripts/spawn-monitor.sh <agent-name> [git-sentinel] [interval-secs]
#
# Usage (auto — reads latest entry from .ostk/pending-monitor-spawns.jsonl):
#   bash scripts/spawn-monitor.sh
#
# The script delegates all polling logic to monitor-agent.sh and exits
# with the same exit code (0 = DONE, 1 = error/circuit-breaker).
#
# Intended use pattern (paste both lines right after an Agent spawn):
#
#   # 1. Spawn the agent (background Task or Agent tool)
#   # 2. Immediately pass to Monitor tool:
#   bash scripts/spawn-monitor.sh <agent-name> <agent-name> 60

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MONITOR="${SCRIPT_DIR}/monitor-agent.sh"

if [[ ! -x "${MONITOR}" ]]; then
    echo "ERROR: monitor-agent.sh not found or not executable: ${MONITOR}" >&2
    exit 2
fi

AGENT_NAME="${1:-}"
GIT_SENTINEL="${2:-}"
INTERVAL="${3:-60}"

# Auto-mode: if no agent name given, pull the latest entry from
# pending-monitor-spawns.jsonl (written by task-isolation-bridge.sh or
# auto-monitor-spawn.sh at spawn time).
if [[ -z "${AGENT_NAME}" ]]; then
    PENDING_FILE="${CLAUDE_PROJECT_DIR:-.}/.ostk/pending-monitor-spawns.jsonl"
    if [[ ! -f "${PENDING_FILE}" ]]; then
        echo "ERROR: no agent name given and no pending spawns file found." >&2
        echo "Usage: bash scripts/spawn-monitor.sh <agent-name> [git-sentinel] [interval-secs]" >&2
        exit 2
    fi
    AGENT_NAME=$(python3 - "${PENDING_FILE}" <<'PY' 2>/dev/null
import sys, json
path = sys.argv[1]
last = None
with open(path) as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            last = json.loads(line).get("name", "")
        except Exception:
            pass
print(last or "")
PY
)
    if [[ -z "${AGENT_NAME}" ]]; then
        echo "ERROR: could not parse agent name from ${PENDING_FILE}" >&2
        exit 2
    fi
    echo "spawn-monitor: auto-detected agent '${AGENT_NAME}' from pending-monitor-spawns.jsonl" >&2
fi

if [[ -z "${GIT_SENTINEL}" ]]; then
    GIT_SENTINEL="${AGENT_NAME}"
fi

exec bash "${MONITOR}" "${AGENT_NAME}" "${GIT_SENTINEL}" "${INTERVAL}"
