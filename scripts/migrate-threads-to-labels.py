#!/usr/bin/env python3
"""→1330: Migrate Groups (threads) to Labels with project: prefix.

Reads ~/.youros/threads.json, creates a project:<name> label for each
thread, assigns that label to all tasks in the thread, then archives
the old threads file.

Safe to re-run: skips labels that already exist, skips assignments
already present.
"""

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

MYOS = Path(os.environ.get("YOUROS_HOME") or (Path.home() / ".youros"))
THREADS_PATH = MYOS / "threads.json"
LABELS_PATH = MYOS / "labels.json"
TASK_LABELS_PATH = MYOS / "task_labels.json"

# Colors to cycle through for generated labels
COLORS = [
    "#3b82f6",  # blue
    "#8b5cf6",  # purple
    "#22c55e",  # green
    "#f97316",  # orange
    "#ec4899",  # pink
    "#06b6d4",  # cyan
    "#eab308",  # yellow
    "#14b8a6",  # teal
]


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2))


def main():
    if not THREADS_PATH.exists():
        print("No threads.json found — nothing to migrate.")
        return

    threads = load_json(THREADS_PATH, [])
    if not isinstance(threads, list) or not threads:
        print("threads.json is empty — nothing to migrate.")
        return

    labels = load_json(LABELS_PATH, [])
    if not isinstance(labels, list):
        labels = []

    task_labels_raw = load_json(TASK_LABELS_PATH, {})
    if not isinstance(task_labels_raw, dict):
        task_labels_raw = {}
    # Normalise to structured format
    if "assignments" not in task_labels_raw:
        task_labels_raw = {"assignments": task_labels_raw, "auto_applied": {}, "rejected": {}}
    assignments: dict = task_labels_raw.setdefault("assignments", {})
    task_labels_raw.setdefault("auto_applied", {})
    task_labels_raw.setdefault("rejected", {})

    existing_label_names = {l.get("name", "").lower() for l in labels}
    migrated = 0
    skipped = 0

    for i, thread in enumerate(threads):
        name = thread.get("name", "").strip()
        if not name:
            continue
        label_name = f"project:{name}"
        if label_name.lower() in existing_label_names:
            print(f"  SKIP  label '{label_name}' already exists")
            skipped += 1
        else:
            label = {
                "id": str(uuid.uuid4())[:8],
                "name": label_name,
                "color": COLORS[i % len(COLORS)],
            }
            labels.append(label)
            existing_label_names.add(label_name.lower())
            print(f"  CREATE label '{label_name}' (id={label['id']})")
            migrated += 1

        # Find the label id (whether just created or pre-existing)
        label_id = next(
            l["id"] for l in labels if l.get("name", "").lower() == label_name.lower()
        )

        for task_id in thread.get("needle_ids", []):
            task_assignments = assignments.setdefault(task_id, [])
            if label_id not in task_assignments:
                task_assignments.append(label_id)
                print(f"    ASSIGN task {task_id} → {label_name}")

    # Persist changes
    LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_json(LABELS_PATH, labels)
    save_json(TASK_LABELS_PATH, task_labels_raw)

    # Archive threads file
    backup = THREADS_PATH.with_suffix(".json.bak")
    shutil.copy2(THREADS_PATH, backup)
    save_json(THREADS_PATH, [])
    print(f"\nDone. {migrated} labels created, {skipped} already existed.")
    print(f"Original threads archived to {backup}")


if __name__ == "__main__":
    main()
