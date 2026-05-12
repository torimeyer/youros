#!/usr/bin/env bash
# Trim .ostk/agents.jsonl rows where last_seen is older than 90s.
# Run this when the fleet-active gate blocks git mutations.
set -euo pipefail

AGENTS_FILE="${TRIM_TARGET:-.ostk/agents.jsonl}"

if [[ ! -f "$AGENTS_FILE" ]]; then
  echo "trim-stale-agents: $AGENTS_FILE not found, nothing to do"
  exit 0
fi

BACKUP="${AGENTS_FILE}.bak-trim-$(date +%s)"
cp "$AGENTS_FILE" "$BACKUP"

python3 - "$AGENTS_FILE" <<'EOF'
import sys, json
from datetime import datetime, timezone

path = sys.argv[1]
now = datetime.now(timezone.utc)
kept = []
total = 0

for line in open(path):
    line = line.rstrip()
    if not line:
        continue
    total += 1
    try:
        d = json.loads(line)
        ls = d.get('last_seen')
        if ls and (now - datetime.fromisoformat(ls.replace('Z', '+00:00'))).total_seconds() < 90:
            kept.append(line)
    except (json.JSONDecodeError, ValueError):
        pass

with open(path, 'w') as f:
    if kept:
        f.write('\n'.join(kept) + '\n')
    else:
        f.write('')

print(f"trim-stale-agents: kept {len(kept)} of {total} rows")
EOF
