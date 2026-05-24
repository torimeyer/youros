#!/usr/bin/env bash
# Rule: auto_monitor_spawn
# Replaces: auto-monitor-spawn.sh (PostToolUse:Agent)
# Called from: post-agent-watch.sh
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.
# Non-blocking: always returns 0.
# Args: $1=tool (Agent) — reads HOOK_INPUT env var for full payload

_auto_monitor_spawn_check() {
  local tool="${1:-Agent}"

  local SPAWN_NAME
  SPAWN_NAME=$(INPUT_JSON="${HOOK_INPUT:-}" python3 <<'PY' 2>/dev/null
import os, sys, json, re
raw = os.environ.get("INPUT_JSON", "") or "{}"
try:
    d = json.loads(raw)
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

m = re.search(r"Spawned REST agent name:\s*(\S+)", haystack)
if m:
    print(m.group(1).strip())
PY
  )

  if [ -n "$SPAWN_NAME" ]; then
    local PENDING_FILE="${CLAUDE_PROJECT_DIR:-.}/.ostk/pending-monitor-spawns.jsonl"
    mkdir -p "$(dirname "$PENDING_FILE")" 2>/dev/null || true
    local LINE
    LINE=$(SPAWN_NAME="$SPAWN_NAME" python3 -c '
import os, json
from datetime import datetime, timezone
print(json.dumps({
    "name": os.environ["SPAWN_NAME"],
    "ts": datetime.now(timezone.utc).isoformat(),
}))
' 2>/dev/null)
    if [ -n "$LINE" ]; then
      printf '%s\n' "$LINE" >> "$PENDING_FILE" 2>/dev/null || true
      echo "P1-b: Recorded bridge spawn '${SPAWN_NAME}' in .ostk/pending-monitor-spawns.jsonl. Arm a Monitor for this agent." >&2
    fi
  else
    echo "P1-b: Remember to arm a Monitor for any bridge-spawned agent, or use scripts/spawn-monitor.sh." >&2
  fi

  log_rule_fire "auto_monitor_spawn" "$tool" "allow" "checked for spawn name"
  return 0
}
