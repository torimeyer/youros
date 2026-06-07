#!/usr/bin/env bash
# Consolidated PostToolUse:Agent watch.
# Replaces: auto-monitor-spawn.sh + scaffold-commit-watcher.sh
# Note: complete-agent.sh stays as a separate infra hook.

HOOK_NAME=$(basename "$0")
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
. "$LIB/load-rule.sh"
. "$LIB/log-fire.sh"
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL:-?} exit=$?" >> /tmp/hook-trace.log' EXIT

set -u
INPUT=$(cat)

# Verify tool is Agent
TOOL=$(INPUT_JSON="$INPUT" python3 -c "
import os, json, sys
try:
    d = json.loads(os.environ.get('INPUT_JSON','') or '{}')
    print((d.get('tool_name') or '').strip())
except Exception:
    pass
" 2>/dev/null)

case "${TOOL:-}" in
    Agent) : ;;
    *) exit 0 ;;
esac

# Parse agent name and session_id from tool response
AGENT_PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, sys, json, re

US = "\x1f"
raw = os.environ.get("INPUT_JSON", "") or "{}"
try:
    d = json.loads(raw, strict=False)
except Exception:
    sys.exit(0)

tr = d.get("tool_response") or {}
haystack = ""
if isinstance(tr, str):
    haystack = tr
elif isinstance(tr, dict):
    for key in ("stderr", "error", "output", "content", "message", "text"):
        v = tr.get(key)
        if isinstance(v, str):
            haystack += v + "\n"
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    haystack += (item.get("text") or "") + "\n"
                elif isinstance(item, str):
                    haystack += item + "\n"

spawn_name = ""
m = re.search(r"Spawned REST agent name:\s*(\S+)", haystack)
if m:
    spawn_name = m.group(1).strip()

if not spawn_name:
    last_file = os.path.expanduser("~/.youros/subagents/last.name")
    try:
        spawn_name = open(last_file).read().strip()
    except Exception:
        pass

session_id = (d.get("session_id") or "").strip()
sys.stdout.write(f"{spawn_name}{US}{session_id}")
PY
)

IFS=$'\x1f' read -r AGENT_NAME SESSION_ID <<<"${AGENT_PARSED:-}"

# Export HOOK_INPUT for functions that need full payload
export HOOK_INPUT="$INPUT"

# ---- 1. auto_monitor_spawn (always run for infra, gated by rule) ----
. "$LIB/rules/auto_monitor_spawn.sh"
rule_enabled auto_monitor_spawn && _auto_monitor_spawn_check "$TOOL"

# ---- 2. scaffold_commit_watcher (gated by rule) ----
. "$LIB/rules/scaffold_commit_watcher.sh"
if rule_enabled scaffold_commit_watcher && [ -n "$AGENT_NAME" ]; then
    _scaffold_commit_watcher_check "$TOOL" "${AGENT_NAME:-}" "${SESSION_ID:-}"
fi

exit 0
