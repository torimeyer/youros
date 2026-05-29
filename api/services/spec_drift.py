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

    drift = len(items) > 0
    if not items:
        summary = "No drift detected."
    else:
        kinds = ", ".join(sorted({i["kind"] for i in items}))
        summary = f"{len(items)} drift item(s): {kinds}."

    return {"drift": drift, "items": items, "summary": summary}
