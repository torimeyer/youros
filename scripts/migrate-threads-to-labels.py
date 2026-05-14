#!/usr/bin/env python3
"""→1330: Migrate Groups (threads) to Labels with project: prefix.

For each thread: creates label `project:<name>`, copies needle_ids into
task.label_ids, clears thread_id on tasks, deletes thread.
"""
import json
import sys
import uuid
from pathlib import Path


def migrate(data_dir: Path) -> None:
    threads_file = data_dir / "threads.json"
    tasks_file = data_dir / "tasks.json"

    if not threads_file.exists():
        print("No threads.json found — nothing to migrate.")
        return

    threads = json.loads(threads_file.read_text())
    tasks = json.loads(tasks_file.read_text()) if tasks_file.exists() else []

    labels_file = data_dir / "labels.json"
    labels = json.loads(labels_file.read_text()) if labels_file.exists() else []

    existing_label_names = {lb["name"] for lb in labels}
    new_labels = []
    label_map: dict[str, str] = {}  # thread_id -> label_id

    for thread in threads:
        label_name = f"project:{thread['name']}"
        if label_name not in existing_label_names:
            label_id = str(uuid.uuid4())
            new_labels.append({
                "id": label_id,
                "name": label_name,
                "color": thread.get("color", "#6366f1"),
            })
            label_map[thread["id"]] = label_id
            existing_label_names.add(label_name)
            print(f"  Created label: {label_name} ({label_id})")

    labels.extend(new_labels)

    migrated_tasks = 0
    for task in tasks:
        thread_id = task.get("thread_id")
        if thread_id and thread_id in label_map:
            task.setdefault("label_ids", [])
            if label_map[thread_id] not in task["label_ids"]:
                task["label_ids"].append(label_map[thread_id])
            task.pop("thread_id", None)
            migrated_tasks += 1

    labels_file.write_text(json.dumps(labels, indent=2))
    tasks_file.write_text(json.dumps(tasks, indent=2))
    threads_file.rename(threads_file.with_suffix(".json.deprecated"))

    print(f"Migration complete: {len(new_labels)} labels created, {migrated_tasks} tasks updated.")
    print("threads.json renamed to threads.json.deprecated.")


if __name__ == "__main__":
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".myos"
    migrate(data_dir)
