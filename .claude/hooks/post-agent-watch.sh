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

# Parse runtime, tool_name, agent_name, and session_id in one pass
AGENT_PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, sys, json, re

US = "\x1f"
raw = os.environ.get("INPUT_JSON", "") or "{}"
try:
    d = json.loads(raw, strict=False)
except Exception:
    d = {}

event_name = (d.get("hook_event_name") or "").strip()
raw_model = (d.get("model") or "").strip()
if event_name in {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop"}:
    runtime = "claude"
elif event_name in {
    "BeforeTool", "AfterTool", "BeforeAgent", "AfterAgent",
    "BeforeModel", "AfterModel", "BeforeToolSelection", "PreCompress",
}:
    runtime = "gemini"
elif raw_model.lower().startswith("gemini"):
    runtime = "gemini"
elif raw_model.lower().startswith("claude"):
    runtime = "claude"
elif os.environ.get("GEMINI_PROJECT_DIR") or os.environ.get("GEMINI_SESSION_ID"):
    runtime = "gemini"
else:
    runtime = "claude"

tool = (d.get("tool_name") or "").strip()
tr = d.get("tool_response") or {}
haystack = ""
if isinstance(tr, str):
    haystack = tr
elif isinstance(tr, dict):
    for key in ("stderr", "error", "output", "content", "message", "text", "llmContent", "returnDisplay"):
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
sys.stdout.write(f"{runtime}{US}{tool}{US}{spawn_name}{US}{session_id}")
PY
)

RUNTIME="claude"
TOOL=""
AGENT_NAME=""
SESSION_ID=""
if [ -n "$AGENT_PARSED" ]; then
    IFS=$'\x1f' read -r RUNTIME TOOL AGENT_NAME SESSION_ID <<<"$AGENT_PARSED"
fi

if [ -z "${CLAUDE_PROJECT_DIR:-}" ] && [ -n "${GEMINI_PROJECT_DIR:-}" ]; then
    export CLAUDE_PROJECT_DIR="$GEMINI_PROJECT_DIR"
fi

_finish_watch() {
    if [ "${RUNTIME:-claude}" = "gemini" ]; then
        printf '{}\n'
    fi
    exit 0
}

case "${TOOL:-}" in
    Agent|Task|codebase_investigator|generalist) : ;;
    *) _finish_watch ;;
esac

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

_finish_watch
