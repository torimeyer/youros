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

# Parse runtime + tool name + session_id + sentinel path in one pass
ADHD_PARSED=$(INPUT_JSON="$INPUT" python3 -c "
import os, json, sys, time
US = '\x1f'
try:
    d = json.loads(os.environ.get('INPUT_JSON','') or '{}', strict=False)
except Exception:
    d = {}
event_name = (d.get('hook_event_name') or '').strip()
raw_model = (d.get('model') or '').strip()
if event_name in {'PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop', 'SubagentStop'}:
    runtime = 'claude'
elif event_name in {'BeforeTool', 'AfterTool', 'BeforeAgent', 'AfterAgent', 'BeforeModel', 'AfterModel', 'BeforeToolSelection', 'PreCompress'}:
    runtime = 'gemini'
elif raw_model.lower().startswith('gemini'):
    runtime = 'gemini'
elif raw_model.lower().startswith('claude'):
    runtime = 'claude'
elif os.environ.get('GEMINI_PROJECT_DIR') or os.environ.get('GEMINI_SESSION_ID'):
    runtime = 'gemini'
else:
    runtime = 'claude'
tool = (d.get('tool_name') or '').strip()
session_id = (
    (d.get('session_id') or '').strip()
    or os.environ.get('CLAUDE_SESSION_ID','').strip()
    or os.environ.get('GEMINI_SESSION_ID','').strip()
    or 'default'
)
home = os.path.expanduser('~')
sentinel = os.path.join(home, '.youros', f'.adhd-monitor-armed-{session_id}')
age = -1
if os.path.exists(sentinel):
    try:
        age = int(time.time() - os.path.getmtime(sentinel))
    except Exception:
        age = 9999
sys.stdout.write(f'{runtime}{US}{tool}{US}{sentinel}{US}{age}')
" 2>/dev/null)

RUNTIME="claude"
TOOL=""
SENTINEL=""
SENTINEL_AGE="-1"
if [ -n "$ADHD_PARSED" ]; then
    IFS=$'\x1f' read -r RUNTIME TOOL SENTINEL SENTINEL_AGE <<<"$ADHD_PARSED"
fi

if [ -z "${CLAUDE_PROJECT_DIR:-}" ] && [ -n "${GEMINI_PROJECT_DIR:-}" ]; then
    export CLAUDE_PROJECT_DIR="$GEMINI_PROJECT_DIR"
fi

_finish_allow() {
    if [ "${RUNTIME:-claude}" = "gemini" ]; then
        printf '{}\n'
    fi
    exit 0
}

case "${TOOL:-}" in
    Agent|Task|codebase_investigator|generalist) : ;;
    *) _finish_allow ;;
esac

# ---- 1. ADHD monitor pairing check (Agent path -- deny if no sentinel) ----
. "$LIB/rules/adhd_monitor_pairing.sh"
rule_enabled adhd_monitor_pairing && _adhd_monitor_pairing_check "$TOOL" "${SENTINEL:-}" "${SENTINEL_AGE:--1}"

# ---- 2. Isolation bridge (route edit-capable tasks through REST spawn) ----
. "$LIB/rules/isolation_bridge.sh"
if rule_enabled isolation_bridge; then
    HOOK_INPUT="$INPUT" HOOK_TOOL="$TOOL" _isolation_bridge_check
fi

_finish_allow
