#!/usr/bin/env python3
"""Reconcile agent rows stuck in status='running' with completed_at already set.

Root cause (→1182): _TERMINAL_FROM_META in the GET /agents listing path did not
include 'completed', so agents whose /complete call succeeded (status+completed_at
written) but whose asyncio proc handle was not yet reaped would be reported as
status='running' on the next poll. This script corrects already-stuck rows.

Usage:
    python3 scripts/reconcile_stuck_agents.py              # dry-run (default)
    python3 scripts/reconcile_stuck_agents.py --apply      # write fixes to disk
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = REPO_ROOT / ".ostk" / "agent_state.json"


def load_state(path: Path) -> dict:
    if not path.exists():
        print(f"State file not found: {path}", file=sys.stderr)
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"Failed to parse state file: {e}", file=sys.stderr)
        return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def reconcile(apply: bool) -> int:
    state = load_state(STATE_FILE)
    if not state:
        print("No agent state found.")
        return 0

    fixed = 0
    now = datetime.now(timezone.utc).isoformat()

    for name, meta in state.items():
        if not isinstance(meta, dict):
            continue
        status = meta.get("status")
        completed_at = meta.get("completed_at")
        if status == "running" and completed_at:
            print(
                f"{'FIX' if apply else 'DRY'}: {name!r} "
                f"status='running' + completed_at='{completed_at}' -> status='completed'"
            )
            if apply:
                meta["status"] = "completed"
                meta["reconciled_at"] = now
                meta["reconcile_reason"] = "stuck-running-with-completed_at (→1182)"
            fixed += 1

    if not fixed:
        print("No stuck rows found.")
        return 0

    if apply:
        save_state(STATE_FILE, state)
        print(f"Wrote {fixed} fix(es) to {STATE_FILE}")
    else:
        print(f"\nDry run: {fixed} row(s) would be fixed. Pass --apply to write.")

    return fixed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write fixes to disk (default: dry-run)")
    args = parser.parse_args()
    count = reconcile(apply=args.apply)
    sys.exit(0 if count >= 0 else 1)


if __name__ == "__main__":
    main()
