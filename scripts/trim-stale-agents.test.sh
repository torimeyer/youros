#!/usr/bin/env bash
# Test: trim-stale-agents.sh keeps fresh rows and drops stale ones.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT

AGENTS_FILE="$TMPDIR_TEST/agents.jsonl"
NOW="$(python3 -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))')"
OLD="2020-01-01T00:00:00Z"

# 1 fresh row + 2 stale rows
printf '{"name":"fresh","last_seen":"%s"}\n' "$NOW"  > "$AGENTS_FILE"
printf '{"name":"stale1","last_seen":"%s"}\n' "$OLD" >> "$AGENTS_FILE"
printf '{"name":"stale2","last_seen":"%s"}\n' "$OLD" >> "$AGENTS_FILE"

TRIM_TARGET="$AGENTS_FILE" bash "$SCRIPT_DIR/trim-stale-agents.sh"

COUNT="$(wc -l < "$AGENTS_FILE" | tr -d ' ')"
if [[ "$COUNT" -ne 1 ]]; then
  echo "FAIL: expected 1 row, got $COUNT"
  exit 1
fi

REMAINING="$(python3 -c "import json; d=json.loads(open('$AGENTS_FILE').read()); print(d['name'])")"
if [[ "$REMAINING" != "fresh" ]]; then
  echo "FAIL: expected 'fresh' row to survive, got: $REMAINING"
  exit 1
fi

echo "PASS: trim-stale-agents.sh kept 1 of 3 rows (fresh only)"
