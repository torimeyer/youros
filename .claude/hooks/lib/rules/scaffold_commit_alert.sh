#!/usr/bin/env bash
# Rule: scaffold_commit_alert
# Replaces: scaffold-commit-alert.sh
# Called from: prompt-header.sh (UserPromptSubmit)
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.

_scaffold_commit_alert_check() {
  local max_age
  max_age=$(rule_param "scaffold_commit_alert.max_age_seconds" "1800")

  local WARN_FILE="${HOME}/.youros/subagents/scaffold-warnings.jsonl"
  if [ ! -f "$WARN_FILE" ]; then
    log_rule_fire "scaffold_commit_alert" "UserPromptSubmit" "allow" "no warnings file"
    return 0
  fi

  MAX_AGE_SECONDS="${max_age}" python3 <<PYEOF 2>/dev/null
import json, os, time, sys

warn_file = os.path.expanduser("~/.youros/subagents/scaffold-warnings.jsonl")
max_age = int(os.environ.get("MAX_AGE_SECONDS", "1800"))
now = time.time()

try:
    lines = open(warn_file).readlines()
except Exception:
    sys.exit(0)

keep = []
emitted = []
for raw in lines:
    raw = raw.strip()
    if not raw:
        continue
    try:
        entry = json.loads(raw)
    except Exception:
        keep.append(raw)
        continue
    ts_str = entry.get("ts") or ""
    spawned_at = entry.get("spawned_at") or 0
    age = now - (spawned_at if spawned_at else now)
    if ts_str and not spawned_at:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age = now - dt.timestamp()
        except Exception:
            age = 0
    if age > max_age:
        continue
    emitted.append(entry)

if emitted:
    print()
    print("SCAFFOLD COMMIT ALERT (from background watcher):")
    for e in emitted:
        print(f"  {e.get('message', str(e))}")
    print()

try:
    with open(warn_file, "w") as f:
        for raw in keep:
            f.write(raw + "\n")
except Exception:
    pass
PYEOF

  log_rule_fire "scaffold_commit_alert" "UserPromptSubmit" "allow" "checked warnings"
  return 0
}
