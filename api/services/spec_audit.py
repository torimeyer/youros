"""Spec audit service (→1469).

Walks spec directories, scores each spec against the 10-section template,
and returns a JSON-serialisable report. Used by:
  - scripts/spec-audit.sh (CLI)
  - GET /api/specs/audit (HTTP)
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


def _find_repo_root() -> Path:
    """Return the repo root, resolved regardless of the server's CWD.

    Resolution order:
      1. MYOS_REPO_ROOT env var (explicit override)
      2. git rev-parse --show-toplevel (reliable when inside a git repo)
      3. Walk up from this file looking for CLAUDE.md or pyproject.toml
      4. CWD as last resort
    """
    env_root = os.environ.get("MYOS_REPO_ROOT")
    if env_root:
        return Path(env_root).resolve()

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).resolve()
    except Exception:
        pass

    # Walk up from this file's location.
    candidate = Path(__file__).resolve().parent
    for _ in range(10):
        if (candidate / "CLAUDE.md").exists() or (candidate / "pyproject.toml").exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    return Path.cwd()


# Resolved once at import time so callers don't pay the subprocess cost per request.
_REPO_ROOT: Path = _find_repo_root()


TEMPLATE_SECTIONS = [
    "Problem",
    "Goals",
    "Non-goals",
    "Solution",
    "Success criteria",
    "Acceptance criteria",
    "USER FEEDBACK",
    "DECISION",
    "References",
]

# Sections that are genuinely optional — a spec without them is not incomplete.
# Excluded from sections_missing so they don't trigger clarity or audit failures.
OPTIONAL_SECTIONS: frozenset[str] = frozenset({"References"})

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
    all_missing = [s for s in TEMPLATE_SECTIONS if s not in present]
    # Optional sections don't count against the score or clarity gate.
    missing = [s for s in all_missing if s not in OPTIONAL_SECTIONS]
    score = len(present)

    return {
        "path": str(path),
        "frontmatter": frontmatter,
        "sections_present": present,
        "sections_missing": missing,
        "score": score,
    }


from dataclasses import dataclass, field as _field


@dataclass
class ShippedResult:
    is_shipped: bool
    missing_files: list[str] = _field(default_factory=list)
    open_needles: list[str] = _field(default_factory=list)


@dataclass
class HuskResult:
    is_husk: bool
    reason: str = ""


_FILE_REF_RE = re.compile(
    r"[\w./\-]+\.(?:py|tsx?|jsx?|md|sql|sh|json|ya?ml|toml)\b"
)
_NEEDLE_RE = re.compile(r"→(\d+)")
_PLACEHOLDER_AC_RE = re.compile(r"^\s*-\s+\[\s*\]\s+\((?:replace with|your)", re.IGNORECASE | re.MULTILINE)
_AC_ITEM_RE = re.compile(r"^\s*-\s+\[[ x]\]", re.MULTILINE)


def compute_shipped(
    spec_path: Path,
    repo_root: Path,
    needle_statuses: dict[str, str],
) -> ShippedResult:
    """Check whether all files referenced in the spec exist and all linked needles are closed."""
    try:
        text = Path(spec_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ShippedResult(is_shipped=False, missing_files=[], open_needles=[])

    # Strip frontmatter before scanning
    body = _FRONTMATTER_RE.sub("", text)

    # Parse file references
    file_refs = _FILE_REF_RE.findall(body)
    # Filter to plausible relative paths (skip bare extensions like ".py")
    # Require at least one "/" so bare filenames like `SpecReview.tsx` are
    # excluded. Bare names can't be reliably resolved to a repo location and
    # always produce false-positive missing_files entries (→2163).
    file_refs = [r for r in file_refs if "/" in r and len(r) > 4]
    # Deduplicate
    seen: set[str] = set()
    unique_refs = []
    for r in file_refs:
        if r not in seen:
            seen.add(r)
            unique_refs.append(r)

    missing = []
    for ref in unique_refs:
        candidate = (Path(repo_root) / ref).resolve()
        if not candidate.exists():
            missing.append(ref)

    # Parse needle references
    needle_ids = _NEEDLE_RE.findall(body)
    open_needles = [
        nid for nid in needle_ids
        if needle_statuses.get(nid, "open") != "closed"
    ]

    # A spec is "shipped" only when it has at least one needle (task) reference
    # AND all those tasks are closed AND no referenced files are missing.
    # File existence alone is not a reliable "shipped" signal: fix-existing-code
    # specs reference files that pre-existed the spec, so their presence proves
    # nothing about whether the fix was actually applied (→2487).
    is_shipped = bool(needle_ids) and (not open_needles) and (not missing)
    return ShippedResult(is_shipped=is_shipped, missing_files=missing, open_needles=open_needles)


def compute_husk_status(spec_path: Path) -> HuskResult:
    """Detect empty or placeholder-only drafts."""
    try:
        text = Path(spec_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return HuskResult(is_husk=True, reason="unreadable file")

    # Strip frontmatter
    body = _FRONTMATTER_RE.sub("", text)

    # Check for placeholder ACs
    if _PLACEHOLDER_AC_RE.search(body):
        return HuskResult(is_husk=True, reason="only placeholder acceptance criteria")

    # Check for meaningful content lines (non-blank, non-heading, non-comment)
    non_trivial = [
        ln for ln in body.splitlines()
        if ln.strip()
        and not ln.strip().startswith("#")
        and not ln.strip().startswith("---")
        and not ln.strip().startswith("<!--")
    ]
    has_ac = bool(_AC_ITEM_RE.search(body))

    if not non_trivial or (not has_ac and len(non_trivial) == 0):
        return HuskResult(is_husk=True, reason="no content")

    return HuskResult(is_husk=False, reason="")


def compute_stage(
    spec: dict,
    husk: "HuskResult | None" = None,
    shipped: "ShippedResult | None" = None,
) -> str:
    """Compute the display stage for a spec dict.

    Stage priority (highest to lowest):
      draft       — status is draft, OR husk detected
      in_progress — frontmatter status is "in-progress" or "building"
      ready       — has content, no issues

    Open needle refs in the spec body do NOT drive in_progress (→1617).
    Body mentions of →NNN are future-work descriptions present in every
    healthy ready spec, not building signals. Only the frontmatter status
    field drives in_progress.

    Specs that meet the shipped condition (all needles closed + all files
    exist) are auto-archived before reaching this function and will not
    appear in the list.
    """
    status = spec.get("status", "draft")

    # Husk → always draft
    if husk is not None and husk.is_husk:
        return "draft"

    # Explicit draft status
    if status == "draft":
        return "draft"

    # Complete / done → explicit complete stage so the frontend doesn't
    # show a finished spec as open. Without this check status="complete"
    # fell through to "ready", making every complete spec appear as open
    # on the spec board (→2207).
    if status in ("complete", "done"):
        return "complete"

    # In progress — only when frontmatter signals active building
    if status in ("in-progress", "building"):
        return "in_progress"

    return "ready"


def audit_all_specs(
    spec_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Walk spec directories and return the full audit report."""
    if spec_dirs is None:
        home = Path.home()
        spec_dirs = [
            home / ".youros" / "specs",
            _REPO_ROOT / "docs" / "spec",
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
