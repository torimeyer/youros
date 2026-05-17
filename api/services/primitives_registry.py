"""
api/services/primitives_registry.py

Central registry of myOS primitives.

A primitive is a service module that satisfies the 10 rules defined in the plan
(single concern, frozen contract, callable from anywhere, observable by default,
registered, self-describing, no upstream router deps, testable in isolation,
documented, reversible via audit).

This registry is the authoritative source of truth for:
  - Which primitives exist
  - What Python module backs each one
  - What public symbols they export
  - What HTTP routes they serve
  - Where their contract doc lives
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Primitive:
    """Metadata record for a single myOS primitive."""

    name: str
    version: str
    module_path: str  # Python import path, or "external:<system>" for non-Python
    doc_path: str     # relative to repo root, e.g. "docs/primitives/time.md"
    exported_symbols: list[str] = field(default_factory=list)
    http_routes: list[str] = field(default_factory=list)
    status: str = "active"          # "active" | "deprecated"
    deprecated_after: Optional[str] = None  # ISO date if status == "deprecated"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PRIMITIVES: dict[str, Primitive] = {
    "time": Primitive(
        name="time",
        version="1.0",
        module_path="services.time_primitive",
        doc_path="docs/primitives/time.md",
        exported_symbols=[
            "start",
            "progress",
            "finish",
            "status",
            "estimate",
            "all_running",
            "TimeStatus",
        ],
        http_routes=[
            "/api/time/status/{op_id}",
            "/api/time/estimate/{op_kind}",
            "/api/time/running",
        ],
    ),
    "memory": Primitive(
        name="memory",
        version="1.0",
        module_path="services.user_memory_store",
        doc_path="docs/primitives/memory.md",
        exported_symbols=[
            "read",
            "append_bullet",
            "replace_all",
        ],
        http_routes=[
            "/memory",
        ],
    ),
    "skill": Primitive(
        name="skill",
        version="1.0",
        module_path="routers.skills",
        doc_path="docs/primitives/skill.md",
        exported_symbols=[
            "run_skill",
            "SkillRunRequest",
            "SkillRunResponse",
        ],
        http_routes=[
            "/api/skills/run",
        ],
    ),
    "heartbeat": Primitive(
        name="heartbeat",
        version="1.0",
        module_path="routers.agents",
        doc_path="docs/primitives/heartbeat.md",
        exported_symbols=[
            "agent_mailbox_instruction",
            "agent_mailbox_instruction_short",
        ],
        http_routes=[
            "/api/agents/register",
            "/api/agents/{name}/heartbeat",
        ],
    ),
    "audit": Primitive(
        name="audit",
        version="1.0",
        module_path="external:ostk",  # ostk kernel primitive — no Python import
        doc_path="docs/primitives/audit.md",
        exported_symbols=[],
        http_routes=[],
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get(name: str) -> Optional[Primitive]:
    """Return the Primitive with the given name, or None if not found."""
    return PRIMITIVES.get(name)


def all_active() -> list[Primitive]:
    """Return all primitives that are not deprecated."""
    return [p for p in PRIMITIVES.values() if p.status != "deprecated"]


def contract(name: str) -> Optional[dict]:
    """
    Return the full contract for a primitive as a plain dict.

    The dict has the same keys as the Primitive dataclass fields, so it can be
    serialised directly to JSON for `GET /api/primitives/{name}/contract`.

    Returns None if the primitive is not found.
    """
    p = PRIMITIVES.get(name)
    if p is None:
        return None
    return asdict(p)
