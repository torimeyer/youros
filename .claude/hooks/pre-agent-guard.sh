#!/usr/bin/env bash
# Consolidated PreToolUse:Agent guard.
# Replaces: adhd-mode-monitor-enforcer.sh (Agent path) + task-isolation-bridge.sh

HOOK_NAME=$(basename "$0")
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
. "$LIB/deny.sh"
init_deny_traps
. "$LIB/load-rule.sh"
. "$LIB/log-fire.sh"
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL:-?} exit=$?" >> /tmp/hook-trace.log' EXIT

set -u
INPUT=$(cat)

# Parse tool name
TOOL=$(INPUT_JSON="$INPUT" python3 -c "
import os, json, sys
try:
    d = json.loads(os.environ.get('INPUT_JSON','') or '{}')
    print((d.get('tool_name') or '').strip())
except Exception:
    pass
" 2>/dev/null)

case "${TOOL:-}" in
    Agent|Task) : ;;
    *) exit 0 ;;
esac

# Parse session_id + sentinel path for ADHD check
ADHD_PARSED=$(INPUT_JSON="$INPUT" python3 -c "
import os, json, sys, time
US = '\x1f'
try:
    d = json.loads(os.environ.get('INPUT_JSON','') or '{}', strict=False)
except Exception:
    sys.exit(0)
session_id = (d.get('session_id') or '').strip() or os.environ.get('CLAUDE_SESSION_ID','').strip() or 'default'
home = os.path.expanduser('~')
sentinel = os.path.join(home, '.myos', f'.adhd-monitor-armed-{session_id}')
age = -1
if os.path.exists(sentinel):
    try:
        age = int(time.time() - os.path.getmtime(sentinel))
    except Exception:
        age = 9999
sys.stdout.write(f'{sentinel}{US}{age}')
" 2>/dev/null)

IFS=$'\x1f' read -r SENTINEL SENTINEL_AGE <<<"${ADHD_PARSED:-}"

# ---- 1. ADHD monitor pairing check (Agent path — deny if no sentinel) ----
. "$LIB/rules/adhd_monitor_pairing.sh"
rule_enabled adhd_monitor_pairing && _adhd_monitor_pairing_check "$TOOL" "${SENTINEL:-}" "${SENTINEL_AGE:--1}"

# ---- 2. Isolation bridge (route edit-capable tasks through REST spawn) ----
. "$LIB/rules/isolation_bridge.sh"
if rule_enabled isolation_bridge; then
    HOOK_INPUT="$INPUT" HOOK_TOOL="$TOOL" _isolation_bridge_check
fi

exit 0
