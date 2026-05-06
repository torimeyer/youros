#!/bin/bash
# UserPromptSubmit hook: surface any pending scaffold-commit warnings
# written by scaffold-commit-watcher.sh background processes.
#
# Reads ~/.myos/subagents/scaffold-warnings.jsonl, emits all warnings
# younger than MAX_AGE_SECONDS (default 1800 / 30 min), then removes
# consumed entries from the file. Exit 0 always (informational).
#
# The watcher runs in the background and cannot directly interrupt the
# parent session. This hook is the delivery mechanism: the warning lands
# on the next turn the user sends a message, injected into Claude's
# context via the UserPromptSubmit stdout channel.

set -u

MAX_AGE_SECONDS="${MYOS_SCAFFOLD_WARN_MAX_AGE:-1800}"
WARN_FILE="${HOME}/.myos/subagents/scaffold-warnings.jsonl"

if [ ! -f "$WARN_FILE" ]; then
    exit 0
fi

python3 <<PYEOF 2>/dev/null
import json, os, time, sys

warn_file = os.path.expanduser("~/.myos/subagents/scaffold-warnings.jsonl")
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
    # Age from spawned_at if present, else from ts field.
    age = now - (spawned_at if spawned_at else now)
    if ts_str and not spawned_at:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age = now - dt.timestamp()
        except Exception:
            age = 0
    if age > max_age:
        continue  # expired, drop silently
    emitted.append(entry)

if emitted:
    print()
    print("SCAFFOLD COMMIT ALERT (from background watcher):")
    for e in emitted:
        print(f"  {e.get('message', str(e))}")
    print()

# Rewrite file with only the unconsumed entries (keep = parse-failed lines).
try:
    with open(warn_file, "w") as f:
        for raw in keep:
            f.write(raw + "\n")
except Exception:
    pass
PYEOF

exit 0
