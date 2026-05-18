"""Spec audit service (→1469).

Walks spec directories, scores each spec against the 10-section template,
and returns a JSON-serialisable report. Used by:
  - scripts/spec-audit.sh (CLI)
  - GET /api/specs/audit (HTTP)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


TEMPLATE_SECTIONS = [
    "Problem",
    "Goals",
    "Non-goals",
    "Solution",
    "Edge cases",
    "Success criteria",
    "Acceptance criteria",
    "Verification",
    "USER FEEDBACK",
    "DECISION",
]

# Alternate header patterns that map to canonical section names.
# Keys are canonical names; values are extra patterns to match.
_SECTION_ALIASES: dict[str, list[str]] = {
    "Success criteria": ["SC-"],
    "Acceptance criteria": ["FR-"],
}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


def _to_serializable(v: Any) -> Any:
    """Convert yaml-parsed values to JSON-safe types."""
    import datetime as dt
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _to_serializable(vv) for k, vv in v.items()}
    if isinstance(v, list):
        return [_to_serializable(i) for i in v]
    return v


def _parse_frontmatter(text: str) -> dict[str, Any]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    block = m.group(1)
    if yaml is not None:
        try:
            parsed = yaml.safe_load(block)
            if not isinstance(parsed, dict):
                return {}
            return {k: _to_serializable(v) for k, v in parsed.items()}
        except Exception:
            return {}
    result: dict[str, Any] = {}
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip() or None
    return result


def _extract_headings(text: str) -> list[str]:
    return [m.group(1).strip() for m in _HEADING_RE.finditer(text)]


def _section_present(canonical: str, headings: list[str]) -> bool:
    lower_headings = [h.lower() for h in headings]
    if canonical.lower() in lower_headings:
        return True
    for alias in _SECTION_ALIASES.get(canonical, []):
        if any(alias.lower() in h for h in lower_headings):
            return True
    return False


def audit_spec_file(path: Path) -> dict[str, Any]:
    """Audit a single spec file and return a result dict."""
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter = _parse_frontmatter(text)
    headings = _extract_headings(text)

    present = [s for s in TEMPLATE_SECTIONS if _section_present(s, headings)]
    missing = [s for s in TEMPLATE_SECTIONS if s not in present]
    score = len(present)

    return {
        "path": str(path),
        "frontmatter": frontmatter,
        "sections_present": present,
        "sections_missing": missing,
        "score": score,
    }


def audit_all_specs(
    spec_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Walk spec directories and return the full audit report."""
    if spec_dirs is None:
        home = Path.home()
        spec_dirs = [
            home / ".myos" / "specs",
            Path("docs") / "spec",
        ]

    results: list[dict[str, Any]] = []
    for d in spec_dirs:
        d = Path(d)
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            if f.name.startswith("_"):
                continue
            results.append(audit_spec_file(f))

    total = len(results)
    fully_templated = sum(1 for r in results if r["score"] == 10)
    partial = total - fully_templated
    score_avg = (sum(r["score"] for r in results) / total) if total > 0 else 0.0

    return {
        "specs": results,
        "summary": {
            "total": total,
            "fully_templated": fully_templated,
            "partial": partial,
            "score_avg": round(score_avg, 2),
        },
    }
