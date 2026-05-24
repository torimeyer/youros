#!/usr/bin/env bash
# Drop-in ostk recovery-based replacement for reap-stale-fleet.sh (→1306).
# Uses `ostk recovery assess --dry-run` for visibility and
# `ostk recovery rescue` for the actual repair path.
# Does NOT touch .ostk/agents.jsonl directly.
#
# Usage:
#   scripts/reap-stale-fleet-ostk.sh            # dry-run (safe, shows discrepancies)
#   scripts/reap-stale-fleet-ostk.sh --apply    # invoke ostk recovery rescue

set -euo pipefail

DRY_RUN=true

for arg in "$@"; do
  if [[ "$arg" == "--apply" ]]; then
    DRY_RUN=false
  fi
done

if $DRY_RUN; then
  echo "=== ostk recovery assess (dry-run) ==="
  ostk recovery assess --dry-run
  echo
  echo "Dry run complete. Pass --apply to run: ostk recovery rescue"
  exit 0
fi

echo "=== ostk recovery rescue ==="
ostk recovery rescue
echo "Recovery complete."
