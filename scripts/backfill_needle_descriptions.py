#!/usr/bin/env python3
"""Backfill bloated needle titles: split long title into short title + description.

Needles with titles over 120 chars are split at the first natural delimiter
found within the first 120 chars. Left side becomes the new title; right side
becomes the start of the description. Dry-run by default.

Usage:
    python3 scripts/backfill_needle_descriptions.py           # dry-run
    python3 scripts/backfill_needle_descriptions.py --apply   # write changes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx


API_BASE = "https://127.0.0.1:8000"
TITLE_MAX = 120
TRUNCATE_AT = 117

# Tried in priority order. We split at the first occurrence within TITLE_MAX chars.
DELIMITERS = ["\n\n", "\n", ". ", ": ", " — ", " - "]

DIFF_PATH = Path(__file__).parent / ".backfill-needle-descriptions-diff.md"


def _split_title(title: str) -> tuple[str, str]:
    """Return (new_title, description_prefix) for a title over TITLE_MAX chars.

    Tries each delimiter in priority order. If none found within TITLE_MAX chars,
    truncates at TRUNCATE_AT and puts the full original title in description.
    """
    for delim in DELIMITERS:
        idx = title.find(delim, 0, TITLE_MAX)
        if idx != -1:
            left = title[:idx].strip()
            right = title[idx + len(delim):].strip()
            return left, right
    return title[:TRUNCATE_AT] + "...", f"**Original title:** {title}"


def _build_description(split_right: str, existing_desc: str | None) -> str:
    """Merge the split right side with any existing description.

    The split right side leads because it is a direct continuation of the
    title sentence. Any prior description is appended after as additional
    context, separated by a blank line.
    """
    parts = [split_right]
    if existing_desc:
        parts.append(existing_desc)
    return "\n\n".join(parts)


def _fetch_tasks(client: httpx.Client) -> list[dict]:
    tasks: list[dict] = []
    for status in ("open", "in_progress"):
        resp = client.get(f"{API_BASE}/api/tasks?status={status}")
        resp.raise_for_status()
        tasks.extend(resp.json().get("tasks", []))
    return tasks


def _fetch_description(client: httpx.Client, task_id: str) -> str | None:
    """Best-effort fetch of existing description for a task.

    The list endpoint omits the description field. The detail endpoint also
    omits it in the current API version, so this returns None for most legacy
    needles. Included for correctness in case the API adds the field later.
    """
    try:
        resp = client.get(f"{API_BASE}/api/tasks/{task_id}", timeout=3.0)
        resp.raise_for_status()
        return resp.json().get("description") or None
    except httpx.HTTPError:
        return None


def _patch_task(client: httpx.Client, task_id: str, title: str, description: str) -> None:
    resp = client.patch(
        f"{API_BASE}/api/tasks/{task_id}",
        json={"title": title, "description": description},
    )
    resp.raise_for_status()


def run(apply: bool = False) -> int:
    with httpx.Client(verify=False, timeout=10.0) as client:
        tasks = _fetch_tasks(client)
        affected = [t for t in tasks if len(t.get("title", "")) > TITLE_MAX]

        if not affected:
            print("Found 0 needles with title >120 chars. Nothing to do.")
            return 0

        if not apply:
            sections: list[str] = []
            for task in affected:
                task_id = task["id"]
                title = task["title"]
                new_title, split_right = _split_title(title)
                existing_desc = _fetch_description(client, task_id)
                new_desc = _build_description(split_right, existing_desc)
                sections.append(
                    f"## {task_id}\n\n"
                    f"**Old title:** {title}\n\n"
                    f"**New title:** {new_title}\n\n"
                    f"**Description preview:**\n{new_desc}\n\n---"
                )
            DIFF_PATH.write_text("\n\n".join(sections) + "\n")
            try:
                rel = DIFF_PATH.relative_to(Path.cwd())
            except ValueError:
                rel = DIFF_PATH
            print(
                f"Found {len(affected)} needles with title >120 chars. "
                f"Diff written to {rel}. "
                "Run again with --apply to write."
            )
            return 0

        # --apply mode
        changed = 0
        for task in affected:
            task_id = task["id"]
            old_title = task["title"]
            new_title, split_right = _split_title(old_title)
            existing_desc = _fetch_description(client, task_id)
            new_desc = _build_description(split_right, existing_desc)
            try:
                _patch_task(client, task_id, new_title, new_desc)
                print(f"{task_id}: title trimmed from {len(old_title)}→{len(new_title)} chars")
                changed += 1
            except httpx.HTTPError as exc:
                print(f"{task_id}: FAILED — {exc}", file=sys.stderr)

    print(
        f"Applied {changed} changes. "
        "Re-run to verify idempotency (should report 0 needles)."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes via PATCH /api/tasks/{id}. Default is dry-run.",
    )
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
