#!/usr/bin/env bash
# Reaps stale entries from .ostk/agents.jsonl that are falsely holding the
# fleet-active gate open (→1522). Entries are stale when last_seen is older
# than STALE_THRESHOLD_MINUTES (default 5) and the alias is not currently
# registered as running in .ostk/agent_state.json.
#
# Usage:
#   scripts/reap-stale-fleet.sh            # dry-run (safe, shows what would change)
#   scripts/reap-stale-fleet.sh --apply    # rewrite .ostk/agents.jsonl in place

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS_JSONL="$REPO_ROOT/.ostk/agents.jsonl"
AGENT_STATE="$REPO_ROOT/.ostk/agent_state.json"
STALE_THRESHOLD_MINUTES="${OSTK_STALE_THRESHOLD_MINUTES:-5}"
DRY_RUN=true

for arg in "$@"; do
  if [[ "$arg" == "--apply" ]]; then
    DRY_RUN=false
  fi
done

if [[ ! -f "$AGENTS_JSONL" ]]; then
  echo "agents.jsonl not found at $AGENTS_JSONL — nothing to do"
  exit 0
fi

python3 - "$AGENTS_JSONL" "$AGENT_STATE" "$STALE_THRESHOLD_MINUTES" "$DRY_RUN" <<'PYEOF'
import sys, json, os
from datetime import datetime, timezone

agents_file, state_file, threshold_str, dry_run_str = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
threshold_mins = int(threshold_str)
dry_run = dry_run_str.lower() == "true"

now = datetime.now(timezone.utc)

# Load running aliases from agent_state.json (authoritative kernel state)
running_aliases = set()
try:
    with open(state_file) as f:
        state = json.load(f)
    running_aliases = {
        alias for alias, v in state.items()
        if isinstance(v, dict) and v.get("status") == "running"
    }
except Exception as e:
    print(f"warn: could not read agent_state.json: {e}", file=sys.stderr)

# Read and classify agents.jsonl entries
entries = []
with open(agents_file) as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # skip malformed lines

total_before = len(entries)
kept = []
reaped = []

for entry in entries:
    alias = entry.get("alias", "")
    status = entry.get("status", "")
    last_seen_str = entry.get("last_seen", "")

    # Keep anything the kernel says is running right now
    if alias in running_aliases:
        kept.append(entry)
        continue

    # Keep entries with recent heartbeats
    if last_seen_str:
        try:
            last_seen = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
            age_mins = (now - last_seen).total_seconds() / 60
            if age_mins <= threshold_mins:
                kept.append(entry)
                continue
        except ValueError:
            pass

    # Everything else is stale
    reaped.append(entry)

print(f"Before: {total_before} entries ({total_before} active)")
print(f"Kept:   {len(kept)} entries")
print(f"Reaped: {len(reaped)} entries (stale, threshold={threshold_mins}m)")

if dry_run:
    print()
    print("Dry run — no changes written. Pass --apply to rewrite agents.jsonl.")
    if reaped:
        print(f"Sample reaped aliases: {[e['alias'] for e in reaped[:5]]}")
    sys.exit(0)

# Back up, then rewrite
import shutil, tempfile, time
backup_path = agents_file + f".bak-reaper-{int(time.time())}"
shutil.copy2(agents_file, backup_path)
print(f"Backup: {backup_path}")

tmp = agents_file + ".tmp"
with open(tmp, "w") as f:
    for entry in kept:
        f.write(json.dumps(entry) + "\n")
os.replace(tmp, agents_file)

print(f"After:  {len(kept)} entries in {agents_file}")
print("Done. Re-run: ostk bash cmd='git stash list' to verify gate passes.")
PYEOF
