"""Excerpt contract, typed source citation returned by every connector.

This is the shared seam every knowledge pipeline imports.
No GE / NR references live here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Excerpt:
    text: str
    source_id: str
    source_title: str
    deep_link: str | None
    score: float
    access_denied: bool
    provider: str


def format_excerpts(excerpts: list[Excerpt]) -> str:
    """Render excerpts as a numbered 'Reference material:' block.

    Each entry shows a numbered label (with a deep-link if available),
    followed by the excerpt text. Access-denied entries note they could
    not be retrieved.
    """
    if not excerpts:
        return ""
    lines: list[str] = ["Reference material:", ""]
    for i, exc in enumerate(excerpts, 1):
        if exc.deep_link:
            header = f"[{i}] [{exc.source_title}]({exc.deep_link})"
        else:
            header = f"[{i}] {exc.source_title}"
        lines.append(header)
        if exc.access_denied:
            lines.append("(Access denied, you may not have permission to view this source.)")
        else:
            lines.append(exc.text)
        lines.append("")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Searchable contract
# ---------------------------------------------------------------------------

from collections.abc import Awaitable, Callable

Searchable = Callable[[str, int], Awaitable[list[Excerpt]]]
