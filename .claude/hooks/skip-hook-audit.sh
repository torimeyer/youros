#!/bin/bash
# PreToolUse Bash hook: block MYOS_SKIP_HOOK=1 git commit unless a recent
# ostk decide entry tagged skip-hook exists (within 600 seconds).

INPUT=$(cat)

CMD=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null)

# Only act if MYOS_SKIP_HOOK=1 AND git commit are both present.
case "$CMD" in
  *MYOS_SKIP_HOOK=1*) ;;
  *) exit 0 ;;
esac

case "$CMD" in
  *git\ commit*) ;;
  *) exit 0 ;;
esac

# Locate decisions file (DECISIONS_PATH env override for tests).
DECISIONS="${DECISIONS_PATH:-.ostk/decisions.jsonl}"

if [ ! -f "$DECISIONS" ]; then
  echo "MYOS_SKIP_HOOK=1 requires a recent \`ostk decide\` entry tagged skip-hook explaining why." >&2
  echo "No decisions file found at: $DECISIONS" >&2
  echo "Run: ostk decide key=skip-hook-<short-reason> value=skip reason=\"...why...\"" >&2
  echo "Then retry." >&2
  exit 2
fi

NOW=$(date +%s)
CUTOFF=$((NOW - 600))

FOUND=$(DECISIONS="$DECISIONS" CUTOFF="$CUTOFF" python3 -c '
import sys, json, os
from datetime import datetime, timezone

decisions_path = os.environ["DECISIONS"]
cutoff = int(os.environ["CUTOFF"])

try:
    with open(decisions_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            key = str(entry.get("key", ""))
            value = str(entry.get("value", ""))
            ts_raw = (entry.get("timestamp") or entry.get("ts")
                      or entry.get("created_at") or "")
            ts = 0
            if ts_raw:
                try:
                    dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    ts = int(dt.timestamp())
                except Exception:
                    try:
                        ts = int(float(str(ts_raw)))
                    except Exception:
                        pass
            if ts < cutoff:
                continue
            if key.startswith("skip-hook") or value in ("skip-hook", "skip"):
                print("yes")
                sys.exit(0)
except Exception:
    pass
print("no")
' 2>/dev/null)

if [ "$FOUND" = "yes" ]; then
  exit 0
fi

echo "MYOS_SKIP_HOOK=1 requires a recent \`ostk decide\` entry tagged skip-hook explaining why." >&2
echo "Run: ostk decide key=skip-hook-<short-reason> value=skip reason=\"...why...\"" >&2
echo "Then retry within 10 minutes." >&2
exit 2
