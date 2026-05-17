"""myOS primitives registry.

A primitive is a service module that satisfies the 10 rules in
~/.claude/plans/any-time-youre-working-effervescent-shore.md.

This registry is the central catalog. Each entry is frozen at registry
time; bumping a primitive's contract bumps its `version` field. The
linter at `scripts/check_primitive_contracts.py` enforces that registered
symbols actually exist in their modules and that contract docs are
present.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Primitive:
    """A myOS primitive: frozen contract, callable from anywhere."""

    name: str
    version: str
    module_path: str
    doc_path: str
    exported_symbols: tuple[str, ...]
    http_routes: tuple[str, ...] = field(default_factory=tuple)
    status: Literal["active", "deprecated"] = "active"
    deprecated_after: str | None = None

    def asdict(self) -> dict:
        """Serializable form for the /api/primitives endpoint."""
        return {
            "name": self.name,
            "version": self.version,
            "module_path": self.module_path,
            "doc_path": self.doc_path,
            "exported_symbols": list(self.exported_symbols),
            "http_routes": list(self.http_routes),
            "status": self.status,
            "deprecated_after": self.deprecated_after,
        }


PRIMITIVES: dict[str, Primitive] = {
    "time": Primitive(
        name="time",
        version="v1.0",
        module_path="services.time_primitive",
        doc_path="docs/primitives/time.md",
        exported_symbols=(
            "start",
            "progress",
            "finish",
            "status",
            "estimate",
            "all_running",
            "TimeStatus",
        ),
        http_routes=(
            "/api/time/status/{op_id}",
            "/api/time/estimate/{op_kind}",
            "/api/time/running",
        ),
    ),
    "memory": Primitive(
        name="memory",
        version="v1.0",
        module_path="services.user_memory_store",
        doc_path="docs/primitives/memory.md",
        exported_symbols=("read", "append_bullet", "replace_all"),
        http_routes=("/api/memory",),
    ),
    "skill": Primitive(
        name="skill",
        version="v1.0",
        module_path="routers.skills",
        doc_path="docs/primitives/skill.md",
        exported_symbols=("router",),
        http_routes=("/api/skills/run",),
    ),
    "heartbeat": Primitive(
        name="heartbeat",
        version="v1.0",
        module_path="routers.agents",
        doc_path="docs/primitives/heartbeat.md",
        exported_symbols=("router",),
        http_routes=(
            "/api/agents/register",
            "/api/agents/{name}/heartbeat",
            "/api/agents/{name}/complete",
        ),
    ),
    "audit": Primitive(
        name="audit",
        version="v1.0",
        module_path="external:ostk",
        doc_path="docs/primitives/audit.md",
        exported_symbols=(),
        http_routes=(),
    ),
}


def get(name: str) -> Primitive | None:
    """Return the Primitive named `name`, or None if not registered."""
    return PRIMITIVES.get(name)


def all_active() -> list[Primitive]:
    """Return all active primitives, deprecated ones excluded."""
    return [p for p in PRIMITIVES.values() if p.status == "active"]


_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def contract(name: str) -> dict | None:
    """Return the parsed contract doc for `name`, or None if unknown.

    Parses the markdown doc by `## ` headers. Returns a dict with keys
    being lowercased section names: purpose, contract, events_emitted,
    versioning_history, worked_examples, what_this_primitive_is_not.
    """
    p = get(name)
    if p is None:
        return None
    doc = REPO_ROOT / p.doc_path
    if not doc.exists():
        return None
    text = doc.read_text(encoding="utf-8")

    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        key = re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections[key] = body

    return sections
