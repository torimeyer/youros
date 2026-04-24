#!/usr/bin/env bash
# Print a table of worktree gate statuses.
#
# Reads every .ostk/worktree_status/*.json and prints one row per
# branch so the user can see at a glance which worktrees are ready to
# merge and which are parked.
#
# Read-only. Writes nothing, never merges, never touches git.

set -u

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${REPO_DIR}/.ostk/worktree_status"

if [ ! -d "${DIR}" ]; then
  echo "no worktree status directory yet: ${DIR}"
  echo "run scripts/worktree-gate.sh <worktree-path> to populate it."
  exit 0
fi

FILES=( "${DIR}"/*.json )
if [ ! -e "${FILES[0]}" ]; then
  echo "no worktree status files yet in ${DIR}"
  exit 0
fi

PY="${REPO_DIR}/api/.venv/bin/python"
if [ ! -x "${PY}" ]; then
  PY="python3"
fi

"${PY}" - "${DIR}" <<'PYEOF'
import json, sys, os, glob

d = sys.argv[1]
rows = []
for f in sorted(glob.glob(os.path.join(d, "*.json"))):
    try:
        data = json.loads(open(f).read())
    except Exception as e:
        rows.append(("?", os.path.basename(f), "unreadable", "", str(e)))
        continue
    rows.append((
        data.get("status", "?"),
        data.get("branch", os.path.basename(f)),
        data.get("suite", "?"),
        data.get("ran_at", "?"),
        ",".join(data.get("failing_tests", []))[:60],
    ))

print(f"{'STATUS':<8} {'BRANCH':<40} {'SUITE':<10} {'RAN_AT':<22} FAILING")
print("-" * 100)
for r in rows:
    print(f"{r[0]:<8} {r[1]:<40} {r[2]:<10} {r[3]:<22} {r[4]}")

ready = sum(1 for r in rows if r[0] == "ready")
parked = sum(1 for r in rows if r[0] == "parked")
print("-" * 100)
print(f"total: {len(rows)}   ready: {ready}   parked: {parked}")
PYEOF
