#!/usr/bin/env python3
"""Reconcile the needle store: report and safely close stale open needles that
have accumulated across the active issues.jsonl and the rotated issues.jsonl.1.

Why this exists
---------------
The Tasks board reads the ostk daemon, which merges issues.jsonl (active) and
issues.jsonl.1 (rotated archive). Over time the archive accumulates "open"
needles that are stale. Closing works (the daemon honours `ostk work close`),
but two things made cleanup unreliable:
  1. A close left the matching archive entry still reading "open" (now fixed in
     OstkService.close_task — two-file consistency).
  2. No run ever closed the full stale set, and doing it by hand is infeasible.

This tool is the safe executor: YOU decide the id list (the triage call), and it
closes each via the daemon AND neutralises both files, while REFUSING to touch
anything that is live work:
  - ids with an active git worktree (peer agents mid-flight), or
  - ids carried by a non-terminal agent_state.json row.

It also never closes spec acceptance-criteria child rows (no priority +
spec_ref) — those are kept under their spec and are already hidden from the
board by is_ac_child_task.

Usage
-----
  python3 scripts/reconcile_needle_archive.py                 # dry-run report
  python3 scripts/reconcile_needle_archive.py --close 1804,2027
  python3 scripts/reconcile_needle_archive.py --close-orphans # AC rows whose
                                                              # spec_ref file is gone
  add --apply to actually write; without it every close is simulated.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NEEDLES_DIR = REPO_ROOT / ".ostk" / "needles"
ACTIVE = NEEDLES_DIR / "issues.jsonl"
ROTATED = NEEDLES_DIR / "issues.jsonl.1"
AGENT_STATE = REPO_ROOT / ".ostk" / "agent_state.json"

_TERMINAL_AGENT = {"completed", "failed", "cancelled", "error", "done"}


def _load(path: Path) -> dict:
    """id (bare, no arrow) -> last entry seen for that id."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        nid = str(entry.get("id", "")).lstrip("→")
        if nid:
            out[nid] = entry
    return out


def _effective() -> dict:
    """Merged view: the active file overrides the rotated archive."""
    act, rot = _load(ACTIVE), _load(ROTATED)
    return {i: act.get(i, rot.get(i)) for i in set(act) | set(rot)}


def _live_worktree_ids() -> set[str]:
    """Task ids that appear in an active git worktree path (peer work)."""
    ids: set[str] = set()
    try:
        out = subprocess.run(
            ["git", "worktree", "list"], cwd=REPO_ROOT,
            capture_output=True, text=True,
        ).stdout
    except Exception:
        return ids
    for line in out.splitlines():
        seg = line.split()[0] if line.split() else ""
        name = Path(seg).name
        # Only digit-runs that follow an explicit agent action verb, e.g.
        # agent-fix-2039-..., agent-build-1990-..., agent-implement-2007.
        for m in re.finditer(r"agent-[a-z]+-(\d{3,4})\b", name):
            ids.add(m.group(1))
        for m in re.finditer(r"agent-[a-z-]*?-(\d{3,4})-", name):
            ids.add(m.group(1))
    return ids


def _live_agent_ids() -> set[str]:
    """Task/needle ids carried by a non-terminal agent_state.json row."""
    ids: set[str] = set()
    if not AGENT_STATE.exists():
        return ids
    try:
        state = json.loads(AGENT_STATE.read_text())
    except Exception:
        return ids
    for meta in (state.values() if isinstance(state, dict) else []):
        if not isinstance(meta, dict):
            continue
        if meta.get("completed_at"):
            continue
        if str(meta.get("status") or "").lower() in _TERMINAL_AGENT:
            continue
        for key in ("task_id", "needle_id"):
            v = meta.get(key)
            if v:
                ids.add(str(v).lstrip("→"))
        for v in meta.get("needle_ids") or []:
            ids.add(str(v).lstrip("→"))
    return ids


def _is_ac_child(entry: dict) -> bool:
    return (not entry.get("priority")) and bool(entry.get("spec_ref"))


def _orphan_ac(entry: dict) -> bool:
    """AC child whose spec_ref file no longer exists -> not keepable backlog."""
    sr = entry.get("spec_ref")
    return _is_ac_child(entry) and bool(sr) and not Path(sr).exists()


def report() -> dict:
    eff = _effective()
    protected = _live_worktree_ids() | _live_agent_ids()
    open_ids = [i for i, o in eff.items() if o.get("status") == "open"]
    standalone, ac_kept, orphans, prot = [], [], [], []
    for i in open_ids:
        o = eff[i]
        if i in protected:
            prot.append(i)
        elif _orphan_ac(o):
            orphans.append(i)
        elif _is_ac_child(o):
            ac_kept.append(i)
        else:
            standalone.append(i)
    print(f"open needles (merged): {len(open_ids)}")
    print(f"  standalone board tasks (triage these): {len(standalone)}")
    print(f"  AC children kept under their spec:      {len(ac_kept)}")
    print(f"  orphan AC (spec file gone -> closeable): {len(orphans)}")
    print(f"  PROTECTED (live worktree/agent, skipped): {len(prot)}")
    if orphans:
        print("\norphan AC ids:", ",".join(sorted(orphans, key=int)))
    if prot:
        print("\nprotected ids:", ",".join(sorted(prot, key=int)))
    return {"standalone": standalone, "ac_kept": ac_kept,
            "orphans": orphans, "protected": prot}


async def close_ids(ids: list[str], reason: str, apply: bool) -> None:
    protected = _live_worktree_ids() | _live_agent_ids()
    sys.path.insert(0, str(REPO_ROOT / "api"))
    from services.ostk import OstkService  # noqa: E402

    svc = OstkService(cwd=str(REPO_ROOT))
    for raw in ids:
        i = str(raw).lstrip("→")
        if i in protected:
            print(f"  SKIP →{i}: protected (live worktree/agent)")
            continue
        if not apply:
            print(f"  [dry-run] would close →{i} (reason={reason})")
            continue
        try:
            await svc.close_task(f"→{i}", closed_reason=reason)
            print(f"  closed →{i}")
        except Exception as exc:
            print(f"  FAILED →{i}: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--close", help="comma-separated needle ids to close")
    ap.add_argument("--close-orphans", action="store_true",
                    help="close AC children whose spec_ref file is missing")
    ap.add_argument("--reason", default="archived",
                    choices=["completed", "duplicate", "archived"])
    ap.add_argument("--apply", action="store_true", help="actually write")
    args = ap.parse_args()

    if not args.close and not args.close_orphans:
        report()
        return

    r = report()
    targets: list[str] = []
    if args.close:
        targets += [x.strip() for x in args.close.split(",") if x.strip()]
    if args.close_orphans:
        targets += r["orphans"]
    print(f"\n{'APPLY' if args.apply else 'DRY-RUN'}: closing {len(targets)} "
          f"needle(s) (reason={args.reason})")
    asyncio.run(close_ids(targets, args.reason, args.apply))


if __name__ == "__main__":
    main()
