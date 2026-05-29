"""Spec constitution service (phase 5).

Surfaces project principles parsed from CLAUDE.md so specs can inherit them.
Read-only and pure: no writes, no side effects, no exceptions propagated.
"""
from __future__ import annotations

from pathlib import Path

from config import PROJECT_ROOT


def load_constitution() -> list[dict]:
    """Return project principles a spec inherits, parsed from CLAUDE.md.

    Each item: {"source": "CLAUDE.md", "section": <heading text>, "text": <bullet text>}.
    Returns [] if no CLAUDE.md is found. Pure/read-only.
    """
    results: list[dict] = []
    for path in _candidate_paths():
        results.extend(_parse_claude_md(path))
    return results


def check_spec_text(spec_text: str) -> list[dict]:
    """Return informational warnings where a spec body violates a surfaced principle.

    Each item: {"principle": str, "detail": str}. Never raises.
    At minimum flags an em-dash character in the body.
    """
    warnings: list[dict] = []
    try:
        if "—" in spec_text:
            warnings.append({
                "principle": "no em-dashes",
                "detail": (
                    "Spec text contains an em-dash. "
                    "Use a period, comma, or rewrite the sentence instead."
                ),
            })
    except Exception:
        pass
    return warnings


def _candidate_paths() -> list[Path]:
    paths = []
    repo_claude = PROJECT_ROOT / "CLAUDE.md"
    if repo_claude.exists():
        paths.append(repo_claude)
    parent_claude = PROJECT_ROOT.parent / "CLAUDE.md"
    if parent_claude.exists():
        paths.append(parent_claude)
    return paths


def _parse_claude_md(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    items: list[dict] = []
    current_section: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current_section = line[3:].strip()
        elif line.startswith("- ") and current_section is not None:
            items.append({
                "source": path.name,
                "section": current_section,
                "text": line[2:].strip(),
            })
    return items
