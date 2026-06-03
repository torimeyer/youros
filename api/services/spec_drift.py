"""Informational drift check: plan-vs-shipped, phase 5 (informational only, never blocks)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from services.spec_audit import (
    _FRONTMATTER_RE,
    _REPO_ROOT,
    _parse_frontmatter,
    compute_husk_status,
    compute_shipped,
)

_DONE_STATUSES = frozenset({"done", "complete", "completed"})
_UNCHECKED_AC_RE = re.compile(r"^\s*-\s+\[ \]", re.MULTILINE)


def compute_spec_drift(spec_path: str) -> dict[str, Any]:
    """Report informational drift between a promoted spec and what shipped.

    Returns {"drift": bool, "items": [{"kind": str, "detail": str}], "summary": str}.
    Pure/read-only. Never raises for a normal spec (missing file -> drift False, empty items).
    """
    path = Path(spec_path)
    if not path.exists():
        return {"drift": False, "items": [], "summary": "spec file not found"}

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"drift": False, "items": [], "summary": "could not read spec file"}

    frontmatter = _parse_frontmatter(text)
    status = str(frontmatter.get("status", "")).strip().lower()
    is_done = status in _DONE_STATUSES

    items: list[dict[str, str]] = []

    husk = compute_husk_status(path)
    if husk.is_husk:
        items.append({
            "kind": "husk",
            "detail": f"Spec was promoted but appears empty or placeholder-only: {husk.reason}.",
        })

    if is_done:
        shipped = compute_shipped(path, _REPO_ROOT, needle_statuses={})
        if shipped.missing_files:
            missing_str = ", ".join(shipped.missing_files[:5])
            more = (
                f" (and {len(shipped.missing_files) - 5} more)"
                if len(shipped.missing_files) > 5
                else ""
            )
            items.append({
                "kind": "claims_done_nothing_shipped",
                "detail": (
                    f"Status is '{status}' but these referenced files do not exist: "
                    f"{missing_str}{more}."
                ),
            })

        body = _FRONTMATTER_RE.sub("", text)
        unchecked = _UNCHECKED_AC_RE.findall(body)
        if unchecked:
            items.append({
                "kind": "unchecked_after_done",
                "detail": (
                    f"Status is '{status}' but {len(unchecked)} acceptance "
                    f"criteria item(s) are still unchecked."
                ),
            })

    # E2: check AC link annotations for missing test/file references
    link_items = _check_ac_links(text, _REPO_ROOT)
    items.extend(link_items)

    drift = len(items) > 0
    if not items:
        summary = "No drift detected."
    else:
        kinds = ", ".join(sorted({i["kind"] for i in items}))
        summary = f"{len(items)} drift item(s): {kinds}."

    return {"drift": drift, "items": items, "summary": summary}


# Regex for E2 inline AC annotation: (test: path::name, covers: a.py, b.py)
_AC_ANNOTATION_RE = re.compile(
    r"\(\s*test:\s*([^,)]+?)(?:,\s*covers:\s*([^)]+))?\s*\)",
    re.IGNORECASE,
)

# Regex for covers-only annotation: (covers: a.py, b.py) without a test: prefix
_AC_COVERS_ONLY_RE = re.compile(
    r"\(\s*covers:\s*([^)]+)\s*\)",
    re.IGNORECASE,
)

_AC_HEADING_SCAN_RE = re.compile(r"^\s{0,3}#{2,}\s+acceptance\s+criteria\b", re.I)
_ANY_HEADING_SCAN_RE = re.compile(r"^\s{0,3}#{2,}\s+")


def _parse_ac_annotation(line: str) -> dict | None:
    """Return {test, covers} from a trailing (test: ..., covers: ...) annotation, or None."""
    m = _AC_ANNOTATION_RE.search(line)
    if not m:
        return None
    test_ref = m.group(1).strip()
    covers_raw = m.group(2)
    covers = [c.strip() for c in covers_raw.split(",")] if covers_raw else []
    return {"test": test_ref, "covers": covers}


def _check_ac_links(text: str, repo_root: Path) -> list[dict[str, str]]:
    """Return drift items for AC annotation links that point to missing test/file paths.

    Syntax: - [ ] Requirement text (test: path/to/test.py::test_name, covers: a.py, b.py)
    Both test: and covers: are optional but test: must appear first if present.
    """
    lines = text.split("\n")
    ac_start: int | None = None
    ac_end = len(lines)
    for idx, line in enumerate(lines):
        if _AC_HEADING_SCAN_RE.match(line):
            ac_start = idx + 1
            break
    if ac_start is not None:
        for idx in range(ac_start, len(lines)):
            if _ANY_HEADING_SCAN_RE.match(lines[idx]):
                ac_end = idx
                break
        scan = lines[ac_start:ac_end]
    else:
        scan = lines

    items: list[dict[str, str]] = []
    for line in scan:
        if not re.match(r"^\s*[-*]\s*\[", line):
            continue
        ann = _parse_ac_annotation(line)
        if ann:
            test_ref = ann["test"]
            test_path_str = test_ref.split("::")[0].strip()
            if test_path_str and not (repo_root / test_path_str).exists():
                items.append({
                    "kind": "ac_link_missing_test",
                    "detail": (
                        f"Requirement references test '{test_path_str}' which no longer exists."
                    ),
                })
            for cover in ann.get("covers", []):
                if cover and not (repo_root / cover).exists():
                    items.append({
                        "kind": "ac_link_missing_file",
                        "detail": f"Requirement references file '{cover}' which no longer exists.",
                    })
        else:
            # Check for a covers-only annotation: (covers: a.py, b.py) with no test: prefix
            m = _AC_COVERS_ONLY_RE.search(line)
            if m:
                for cover in (c.strip() for c in m.group(1).split(",")):
                    if cover and not (repo_root / cover).exists():
                        items.append({
                            "kind": "ac_link_missing_file",
                            "detail": f"Requirement references file '{cover}' which no longer exists.",
                        })
    return items
