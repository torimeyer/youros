"""Spec-kit YAML import and export service.

Spec-kit is a common field format for describing features as structured YAML.
This module converts between spec-kit YAML and the myOS spec shape, and back.

myOS spec shape:
  title: str
  description: str
  tasks: list of {title, description, priority, acceptance_criteria}

Spec-kit YAML shape:
  name: "Feature name"
  description: "What it does"
  tasks:
    - title: "Task 1"
      description: "..."
      priority: P1
      acceptance_criteria:
        - "Criterion 1"
"""

from __future__ import annotations

from typing import Any

import yaml


class SpeckitParseError(ValueError):
    """Raised when spec-kit YAML is malformed or missing required fields."""


def parse_speckit(yaml_str: str) -> dict[str, Any]:
    """Parse spec-kit YAML into a normalized dict.

    Raises SpeckitParseError for invalid YAML or missing 'name' field.
    Returns a dict with guaranteed keys: name, description, tasks.
    Each task has: title, description, priority, acceptance_criteria (list).
    """
    if not yaml_str or not yaml_str.strip():
        raise SpeckitParseError("YAML content is empty")
    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        raise SpeckitParseError(f"Invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SpeckitParseError("YAML must be a mapping at the top level")
    name = (data.get("name") or "").strip()
    if not name:
        raise SpeckitParseError("Spec-kit YAML must include a 'name' field")
    description = str(data.get("description") or "").strip()
    raw_tasks = data.get("tasks") or []
    if not isinstance(raw_tasks, list):
        raise SpeckitParseError("'tasks' must be a list")
    tasks: list[dict[str, Any]] = []
    for i, t in enumerate(raw_tasks):
        if not isinstance(t, dict):
            raise SpeckitParseError(f"Task at index {i} must be a mapping")
        title = str(t.get("title") or "").strip()
        if not title:
            raise SpeckitParseError(f"Task at index {i} is missing a 'title'")
        ac_raw = t.get("acceptance_criteria") or []
        if not isinstance(ac_raw, list):
            ac_raw = [str(ac_raw)]
        tasks.append({
            "title": title,
            "description": str(t.get("description") or "").strip(),
            "priority": str(t.get("priority") or "P2").strip(),
            "acceptance_criteria": [str(c).strip() for c in ac_raw if str(c).strip()],
        })
    return {"name": name, "description": description, "tasks": tasks}


def speckit_to_spec(parsed: dict[str, Any]) -> dict[str, Any]:
    """Convert a parsed spec-kit dict into the myOS spec shape.

    myOS spec shape:
      title: str
      description: str
      tasks: list of task dicts (same shape as parsed tasks)
    """
    return {
        "title": parsed.get("name", ""),
        "description": parsed.get("description", ""),
        "tasks": parsed.get("tasks", []),
    }


def spec_to_speckit(spec: dict[str, Any]) -> str:
    """Export a myOS spec dict back to spec-kit YAML.

    Accepts the myOS spec shape (title/description/tasks) and returns
    a valid spec-kit YAML string with name/description/tasks.
    """
    tasks = spec.get("tasks") or []
    output_tasks = []
    for t in tasks:
        entry: dict[str, Any] = {"title": t.get("title", "")}
        desc = (t.get("description") or "").strip()
        if desc:
            entry["description"] = desc
        priority = (t.get("priority") or "").strip()
        if priority:
            entry["priority"] = priority
        ac = t.get("acceptance_criteria") or []
        if ac:
            entry["acceptance_criteria"] = list(ac)
        output_tasks.append(entry)
    doc: dict[str, Any] = {
        "name": spec.get("title", ""),
        "description": spec.get("description", ""),
    }
    if output_tasks:
        doc["tasks"] = output_tasks
    return yaml.dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)
