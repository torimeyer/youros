#!/usr/bin/env python3
"""Reconcile open needles whose work has already shipped.

Background (→2200): the rotated-archive reconciler now correctly surfaces open
needles that live only in issues.jsonl.1. That exposed a backlog of needles
where the work was committed but `ostk work close "→NNNN"` was never called.
This script finds them and (optionally) closes them.

Default mode is DRY RUN — it lists candidates and exits without changing state.
Pass --apply to actually close. Pass --branch refs/heads/main to scope the
commit search to a specific ref (default: HEAD).

Heuristics on purpose: this is a one-shot reconcile tool, not a hook. Some
"→NNNN" commit references show up in `wip`, `scaffold`, or `Revert` subjects
where the work was NOT actually completed. The script surfaces those subjects
so you can spot-check before applying.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

NEEDLE_REF = re.compile(r"→(\d{1,5})")
COMPLETED_PREFIXES = ("feat", "fix", "perf", "refactor")
SUSPECT_PREFIXES = ("wip", "scaffold", "revert", "Revert")


def load_open_needles(store: Path) -> dict[str, dict]:
    """Return {id: last-entry} for any non-closed, non-shelved needle across
    issues.jsonl + issues.jsonl.1."""
    out: dict[str, dict] = {}
    for path in (store / "issues.jsonl.1", store / "issues.jsonl"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            nid = entry.get("id")
            if nid:
                out[nid] = entry
    return {nid: e for nid, e in out.items()
            if (e.get("status") or "").lower() not in {"closed", "shelved"}}


def commits_mentioning(repo: Path, branch: str) -> dict[str, list[tuple[str, str]]]:
    """Return {needle_id: [(short_sha, subject), ...]} across the branch."""
    cmd = ["git", "-C", str(repo), "log", "--oneline", "--no-decorate", branch]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    refs: dict[str, list[tuple[str, str]]] = {}
    for line in proc.stdout.splitlines():
        sha, _, subject = line.partition(" ")
        for m in NEEDLE_REF.finditer(subject):
            nid = "→" + m.group(1)
            refs.setdefault(nid, []).append((sha, subject))
    return refs


def categorize(subjects: list[tuple[str, str]]) -> str:
    """'completed' if any commit looks like a real ship; 'suspect' if every
    commit is a wip/scaffold/revert; 'mixed' otherwise."""
    has_completed = False
    has_suspect = False
    for _, subj in subjects:
        prefix = subj.split("(", 1)[0].split(":", 1)[0].strip()
        if prefix in COMPLETED_PREFIXES:
            has_completed = True
        elif prefix in SUSPECT_PREFIXES:
            has_suspect = True
    if has_completed and not has_suspect:
        return "completed"
    if has_completed and has_suspect:
        return "mixed"
    return "suspect"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually close the needles (default: dry run)")
    parser.add_argument("--branch", default="HEAD",
                        help="git ref to scan for commit subjects (default: HEAD)")
    parser.add_argument("--store", default=os.path.join(os.environ.get("YOUROS_HOME", os.path.expanduser("~/.youros")), "needles"),
                        help="needle store dir (default: ~/.youros/needles)")
    parser.add_argument("--repo", default=os.getcwd(),
                        help="git repo to scan (default: cwd)")
    parser.add_argument("--only", choices=["completed", "mixed", "suspect", "all"],
                        default="completed",
                        help="which category to apply to (default: completed)")
    args = parser.parse_args()

    store = Path(args.store)
    repo = Path(args.repo)
    open_needles = load_open_needles(store)
    refs = commits_mentioning(repo, args.branch)

    print(f"open needles in store: {len(open_needles)}")
    print(f"needle refs in git log: {len(refs)}")
    print()

    candidates: list[tuple[str, str, list[tuple[str, str]]]] = []
    for nid, entry in sorted(open_needles.items(), key=lambda kv: int(kv[0][1:])):
        if nid not in refs:
            continue
        cat = categorize(refs[nid])
        candidates.append((nid, cat, refs[nid]))

    if not candidates:
        print("nothing to reconcile.")
        return 0

    for nid, cat, subjects in candidates:
        title = (open_needles[nid].get("title") or "")[:80]
        print(f"{nid}  [{cat}]  {title}")
        for sha, subj in subjects[:4]:
            print(f"    {sha}  {subj[:100]}")
    print()

    if not args.apply:
        print("DRY RUN. Re-run with --apply --only completed to close the safe ones.")
        return 0

    closed = 0
    for nid, cat, _ in candidates:
        if args.only != "all" and cat != args.only:
            continue
        result = subprocess.run(
            ["ostk", "work", "close", nid, "--reason", "completed"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"closed {nid}")
            closed += 1
        else:
            print(f"FAILED {nid}: {result.stderr.strip()}", file=sys.stderr)
    print(f"\nclosed {closed} needles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
