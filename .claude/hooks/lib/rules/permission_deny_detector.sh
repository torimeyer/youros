#!/usr/bin/env bash
# Rule: permission_deny_detector
# Replaces: permission-deny-detector.sh (UserPromptSubmit)
# Called from: prompt-header.sh
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.
# Non-blocking: always returns 0.

_permission_deny_detector_check() {
  local lookback
  lookback=$(rule_param "permission_deny_detector.lookback_seconds" "60")

  local CANNED="user doesn't want to proceed with this tool use"
  local DENY_LOG="${DENY_LOG:-${HOME}/.claude/logs/hook-denies.log}"
  local NOW
  NOW=$(date +%s)
  local CUTOFF=$(( NOW - ${lookback:-60} ))
  local EMITTED=0

  local TOOL_RESULT_DIRS
  TOOL_RESULT_DIRS=$(find "${HOME}/.claude/projects" -name 'tool-results' -type d 2>/dev/null)

  for DIR in $TOOL_RESULT_DIRS; do
    [ -d "$DIR" ] || continue
    [ "$EMITTED" -eq 1 ] && break

    while IFS= read -r -d '' FILE; do
      local FILE_MTIME
      FILE_MTIME=$(python3 -c "import os; print(int(os.path.getmtime('$FILE')))" 2>/dev/null || echo 0)
      [ "$FILE_MTIME" -ge "$CUTOFF" ] || continue
      grep -qF "$CANNED" "$FILE" 2>/dev/null || continue

      if [ -f "$DENY_LOG" ] && [ -s "$DENY_LOG" ]; then
        local MATCH
        MATCH=$(DENY_LOG="$DENY_LOG" FILE_MTIME="$FILE_MTIME" python3 <<'PY' 2>/dev/null
import os, json
from datetime import datetime
file_mtime = int(os.environ["FILE_MTIME"])
deny_log = os.environ["DENY_LOG"]
found = False
try:
    with open(deny_log) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line, strict=False)
            except Exception:
                continue
            ts_raw = entry.get("ts", "")
            try:
                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                ts = int(dt.timestamp())
            except Exception:
                continue
            if abs(ts - file_mtime) <= 2:
                found = True
                break
except Exception:
    pass
print("yes" if found else "no")
PY
        )
        [ "$MATCH" = "yes" ] && continue
      fi

      EMITTED=1
      printf 'SETTINGS-LEVEL DENY DETECTED:\n'
      printf 'A tool was denied by a permissions rule in settings.json, not a hook.\n'
      printf 'Check ~/.claude/settings.json and .claude/settings.local.json permissions.deny.\n'
      printf 'Run: jq '"'"'.permissions.deny // []'"'"' ~/.claude/settings.json .claude/settings.json .claude/settings.local.json\n'
      break
    done < <(find "$DIR" -maxdepth 1 -name '*.txt' -print0 2>/dev/null)
  done

  log_rule_fire "permission_deny_detector" "UserPromptSubmit" "allow" "checked tool results"
  return 0
}
