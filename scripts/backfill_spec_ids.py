#!/usr/bin/env python3
"""One-shot backfill: assign S001, S002, … to any spec that lacks spec_id.

Idempotent — specs that already have spec_id: are left unchanged.
Ordering: alphabetical by filename within each directory.
Scan order: docs/spec/ first (project-local), then ~/.youros/specs/ (user-local).

Usage:
    python scripts/backfill_spec_ids.py          # dry-run (prints what it would do)
    python scripts/backfill_spec_ids.py --apply  # write the frontmatter additions
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_SPECS_DIR = Path.home() / ".youros" / "specs"

_ID_RE = re.compile(r"^spec_id:\s*S(\d+)", re.MULTILINE)
_FM_RE = re.compile(r"^---\n(.*?\n)---\n", re.DOTALL)


def _existing_ids(dirs: list[Path]) -> set[int]:
    nums: set[int] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for md in d.glob("*.md"):
            try:
                text = md.read_text(errors="replace")
                for m in _ID_RE.finditer(text):
                    nums.add(int(m.group(1)))
            except OSError:
                pass
    return nums


def _needs_id(path: Path) -> bool:
    try:
        text = path.read_text(errors="replace")
        return not _ID_RE.search(text)
    except OSError:
        return False


def _inject_spec_id(path: Path, spec_id: str, apply: bool) -> None:
    text = path.read_text(errors="replace")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        # No frontmatter: prepend one.
        new_text = f"---\nspec_id: {spec_id}\n---\n{text}"
    else:
        # Find end of frontmatter and insert before the closing ---
        end_idx = None
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                end_idx = i
                break
        if end_idx is None:
            print(f"  SKIP {path.name}: malformed frontmatter")
            return
        new_lines = lines[:end_idx] + [f"spec_id: {spec_id}"] + lines[end_idx:]
        new_text = "\n".join(new_lines)

    action = "WRITE" if apply else "DRY-RUN"
    print(f"  {action} {path.name} → {spec_id}")
    if apply:
        path.write_text(new_text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually write files")
    args = parser.parse_args()

    scan_dirs = [
        PROJECT_ROOT / "docs" / "spec",
        USER_SPECS_DIR,
    ]

    # Collect files that need IDs, preserving alphabetical-within-dir order.
    needs_id: list[Path] = []
    for d in scan_dirs:
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            if _needs_id(md):
                needs_id.append(md)

    if not needs_id:
        print("All specs already have spec_id — nothing to do.")
        return

    # Find the highest existing ID number so we don't collide.
    used = _existing_ids(scan_dirs)
    next_num = max(used, default=0) + 1

    print(f"{'DRY-RUN' if not args.apply else 'APPLYING'}: {len(needs_id)} spec(s) need IDs")
    for path in needs_id:
        spec_id = f"S{next_num:03d}"
        _inject_spec_id(path, spec_id, args.apply)
        next_num += 1

    if not args.apply:
        print("\nRe-run with --apply to write the changes.")


if __name__ == "__main__":
    main()
