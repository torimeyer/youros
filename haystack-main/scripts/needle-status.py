#!/usr/bin/env python3
"""
needle-status.py — sprint needle status query
Usage: python3 scripts/needle-status.py [→NNN ...]
       python3 scripts/needle-status.py --thread v1.3
       python3 scripts/needle-status.py --priority P0

Without args: shows v1.3 sprint needles (hardcoded IDs updated each sprint).
"""
import sys, json, os
from pathlib import Path

ROOT = Path(__file__).parent.parent
ISSUES = ROOT / ".haystack/needles/issues.jsonl"

# Sprint IDs — update each sprint (or pass on command line)
V1_3_IDS = {"→609", "→495", "→610", "→502", "→619", "→620", "→617", "→618", "→615", "→616"}

STATUS_ICON = {"open": "○", "closed": "✓", "active": "●"}
PRIORITY_COLOR = {"P0": "\033[31m", "P1": "\033[33m", "P2": "\033[36m", "": ""}
RESET = "\033[0m"

def load_needles():
    if not ISSUES.exists():
        return []
    needles = []
    with open(ISSUES) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    needles.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return needles

def format_needle(n, title_width=80):
    icon = STATUS_ICON.get(n.get("status", ""), "?")
    pri = n.get("priority", "")
    color = PRIORITY_COLOR.get(pri, "")
    title = n.get("title", "")[:title_width]
    return f"  {icon} {color}{n['id']:>6}{RESET} [{pri}] {title}"

def main():
    args = sys.argv[1:]
    needles = load_needles()

    if "--priority" in args:
        idx = args.index("--priority")
        pri = args[idx + 1] if idx + 1 < len(args) else "P0"
        selected = [n for n in needles if n.get("priority") == pri and n.get("status") == "open"]
        print(f"\n{pri} open needles ({len(selected)}):")
        for n in selected:
            print(format_needle(n))

    elif "--thread" in args:
        idx = args.index("--thread")
        thread = args[idx + 1] if idx + 1 < len(args) else "v1.3"
        # Filter by title keywords for now (until haystack thread show exists →615)
        keywords = thread.lower().split("-")
        selected = [n for n in needles
                    if any(k in n.get("title", "").lower() for k in keywords)
                    and n.get("status") == "open"]
        print(f"\nThread {thread} open needles ({len(selected)}):")
        for n in selected:
            print(format_needle(n))

    elif args:
        # Specific IDs passed on command line
        ids = set(args)
        needle_map = {n["id"]: n for n in needles}
        print(f"\nNeedle status:")
        for id_ in args:
            n = needle_map.get(id_)
            if n:
                print(format_needle(n))
            else:
                print(f"  ? {id_:>6}  [not found]")

    else:
        # Default: v1.3 sprint
        needle_map = {n["id"]: n for n in needles}
        open_count = sum(1 for id_ in V1_3_IDS if needle_map.get(id_, {}).get("status") == "open")
        closed_count = sum(1 for id_ in V1_3_IDS if needle_map.get(id_, {}).get("status") == "closed")
        print(f"\nv1.3 sprint — {open_count} open, {closed_count} closed:")
        for id_ in sorted(V1_3_IDS):
            n = needle_map.get(id_)
            if n:
                print(format_needle(n))
            else:
                print(f"  ? {id_:>6}  [not tracked]")

    print()

if __name__ == "__main__":
    main()
