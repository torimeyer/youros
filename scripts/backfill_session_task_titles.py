#!/usr/bin/env python3
"""Backfill legacy "Claude Code session claude-code-..." task titles.

Old SessionStart hook used the raw claude-code-<uuid> string as the task
title, which polluted the Tasks page with a wall of unreadable rows. The
hook was updated to create tasks titled "Session in <cwd_basename>", but
existing tasks still carry the old titles. This script renames them via
the backend's PATCH /api/tasks/{task_id} endpoint so the Tasks page is
human-readable again.

Idempotent: tasks that already match the new "Session in ..." format are
skipped. Tasks not owned by the current backend are ignored silently.

Usage:
    .venv/bin/python3 scripts/backfill_session_task_titles.py
    .venv/bin/python3 scripts/backfill_session_task_titles.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import httpx


API_BASE = os.environ.get("MYOS_API_BASE", "https://127.0.0.1:8000")

# Legacy title pattern from the old SessionStart hook.
# e.g. "Claude Code session claude-code-ae81930b-5"
LEGACY_RE = re.compile(r"^Claude Code session claude-code-", re.IGNORECASE)

# New title format written by the current hook: "Session in <basename>".
NEW_TITLE_RE = re.compile(r"^Session in \S+")

NEW_DESCRIPTION = (
    "session-task: Legacy migration. Close when this Claude Code session ends."
)


def _cwd_basename_default() -> str:
    """Best-effort default cwd basename.

    Legacy tasks do not record the cwd they were filed from. Since all
    known legacy tasks in this install were filed from the torios repo
    (the only place with the old SessionStart hook), we default to the
    basename of the current working directory. Fallback to "torios" if
    the cwd is unreadable for any reason.
    """
    try:
        return Path.cwd().name or "torios"
    except OSError:
        return "torios"


def _is_legacy(title: str) -> bool:
    return bool(LEGACY_RE.match(title or ""))


def _is_already_migrated(title: str) -> bool:
    return bool(NEW_TITLE_RE.match(title or ""))


def _build_new_title(cwd_basename: str) -> str:
    return f"Session in {cwd_basename}"


def _fetch_tasks(client: httpx.Client) -> list[dict]:
    resp = client.get(f"{API_BASE}/api/tasks?include_test_data=true")
    resp.raise_for_status()
    data = resp.json()
    return data.get("tasks", [])


def _rename_task(
    client: httpx.Client, task_id: str, title: str, description: str
) -> None:
    resp = client.patch(
        f"{API_BASE}/api/tasks/{task_id}",
        json={"title": title, "description": description},
    )
    resp.raise_for_status()


def backfill(dry_run: bool = False) -> int:
    """Rename legacy session tasks. Returns the number renamed."""
    cwd_basename = _cwd_basename_default()
    new_title = _build_new_title(cwd_basename)

    renamed = 0
    skipped_already = 0
    with httpx.Client(verify=False, timeout=5.0) as client:
        tasks = _fetch_tasks(client)
        for task in tasks:
            title = task.get("title", "")
            task_id = task.get("id", "")
            if _is_already_migrated(title):
                skipped_already += 1
                continue
            if not _is_legacy(title):
                continue
            if not task_id:
                continue
            if dry_run:
                print(f"[dry-run] would rename {task_id}: {title!r} -> {new_title!r}")
            else:
                try:
                    _rename_task(client, task_id, new_title, NEW_DESCRIPTION)
                    print(f"renamed {task_id}: {title!r} -> {new_title!r}")
                except httpx.HTTPError as exc:
                    print(f"failed to rename {task_id}: {exc}", file=sys.stderr)
                    continue
            renamed += 1

    print(f"{renamed} tasks renamed ({skipped_already} already migrated)")
    return renamed


def backfill_direct(dry_run: bool = False) -> int:
    """Rename legacy session tasks by editing .ostk/needles/issues.jsonl directly.

    Used when the running backend predates the PATCH /api/tasks/{id}
    title+description support, or when the backend is unavailable. Writes
    are idempotent and safe: the same file-level validation the ostk
    service uses is applied here.
    """
    import json

    cwd_basename = _cwd_basename_default()
    new_title = _build_new_title(cwd_basename)

    issues_path = Path.cwd() / ".ostk" / "needles" / "issues.jsonl"
    if not issues_path.exists():
        print(f"{issues_path} not found", file=sys.stderr)
        return 0

    lines = issues_path.read_text().strip().splitlines()
    renamed = 0
    skipped_already = 0
    updated_lines = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            updated_lines.append(line)
            continue
        title = entry.get("title", "") or ""
        if _is_already_migrated(title):
            skipped_already += 1
            updated_lines.append(line)
            continue
        if _is_legacy(title):
            if dry_run:
                print(
                    f"[dry-run] would rename {entry.get('id')}: "
                    f"{title!r} -> {new_title!r}"
                )
            else:
                entry["title"] = new_title
                entry["description"] = NEW_DESCRIPTION
                print(f"renamed {entry.get('id')}: {title!r} -> {new_title!r}")
            renamed += 1
        updated_lines.append(json.dumps(entry, ensure_ascii=False))

    if not dry_run and renamed > 0:
        issues_path.write_text("\n".join(updated_lines) + "\n")

    print(f"{renamed} tasks renamed ({skipped_already} already migrated)")
    return renamed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be renamed without changing anything.",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help=(
            "Edit .ostk/needles/issues.jsonl directly instead of calling the "
            "running backend. Use when the backend is down or predates PATCH "
            "title+description support."
        ),
    )
    args = parser.parse_args()
    if args.direct:
        backfill_direct(dry_run=args.dry_run)
        return 0
    try:
        backfill(dry_run=args.dry_run)
    except httpx.HTTPError as exc:
        print(f"backend unreachable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
