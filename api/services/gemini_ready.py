"""Gemini-ready readiness service (→1467, →1503).

Computes whether a task or spec can be handed to Gemini. Returns a
Readiness object with 9 named checks so the UI tooltip can show exactly
which check(s) failed. All checks are always evaluated (no early-return).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ReadinessCheck:
    name: str
    passed: bool
    detail: str  # one-line explanation shown in tooltip
    required: bool = True  # False = optional ("Enhance"); does not block ready


@dataclass
class Readiness:
    ready: bool
    file_path: Optional[str]          # the linked plan/spec file (if found)
    checks: list[ReadinessCheck] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "file_path": self.file_path,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail,
                 "required": c.required}
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches docs/spec/*.md, ~/.youros/specs/*.md (~/ or absolute), ~/.claude/plans/*.md
_PLAN_PATH_RE = re.compile(
    r"(?:"
    r"docs/spec/[\w.\-]+\.md"
    r"|"
    r"\S*?\.youros/specs/[\w.\-]+\.md"
    r"|"
    r"\S*?\.claude/plans/[\w.\-]+\.md"
    r")"
)

# Matches unchecked AC checkbox lines
_AC_RE = re.compile(r"^\s*-\s+\[\s\]\s+(.+)$", re.MULTILINE)

# Matches all AC checkbox lines (checked and unchecked)
_ALL_AC_RE = re.compile(r"^\s*-\s+\[[ xX]\]\s+(.+)$", re.MULTILINE)

# Tokens that indicate vagueness (case-insensitive).
# Narrowed in →1564: dropped "either", "consider", "explore", "review", "depends"
# (false positives on normal English); changed bare "decide" to "decide what".
_VAGUE_TOKENS_RE = re.compile(
    r"\bTBD\b|\?|should we|\bTODO\b"
    r"|\bmaybe\b|\bdiscuss\b|\bclarif(?:y|ication)\b|figure out|we'll see|decide what",
    re.IGNORECASE,
)

# File paths: at least one word/slash/dot component ending with a known extension
_FILE_PATH_RE = re.compile(
    r"[\w./\-]+\.(?:py|tsx|ts|js|jsx|md|sql|sh|json|ya?ml|toml)\b"
)

# Title patterns that indicate upstream/external scope
_UPSTREAM_TITLE_RE = re.compile(r"^(?:upstream[\s:,]|\[upstream\])", re.IGNORECASE)

# Body patterns that indicate out-of-repo work
_OUT_OF_REPO_RE = re.compile(
    r"upstream ostk|upstream binary|different repo|external repo|out of scope|not in this repo",
    re.IGNORECASE,
)

# Generic title patterns: "update X" or "fix X" with nothing after the noun
_GENERIC_TITLE_RE = re.compile(r"^(?:update|fix)\s+\w+\s*$", re.IGNORECASE)

# Task check names (3 checks as of →1564)
_TASK_CHECK_NAMES = ["outcome_concrete", "in_repo_scope", "is_unblocked"]

# Spec check names (5 checks as of →1564)
_SPEC_CHECK_NAMES = [
    "has_ac_checkboxes",
    "no_vague_ac",
    "has_file_paths",
    "referenced_files_exist",
    "in_repo_scope",
]

# Legacy list kept for import compatibility — do not use in new code
_CHECK_NAMES = [
    "plan_path_present",
    "file_exists",
    "has_ac_checkboxes",
    "no_vague_ac",
    "has_file_paths",
    "ac_count_threshold",
    "referenced_files_exist",
    "in_repo_scope",
    "is_unblocked",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_project_root() -> Path:
    try:
        import config
        return Path(config.PROJECT_ROOT)
    except Exception:
        return Path(".").resolve()


def _expand(path: str) -> str:
    """Expand ~ and return absolute path."""
    return str(Path(os.path.expanduser(path)).resolve())


def _extract_plan_path(text: str) -> Optional[str]:
    """Return the first plan/spec path found in text, expanded and absolute."""
    m = _PLAN_PATH_RE.search(text)
    if not m:
        return None
    raw = m.group(0)
    if not raw.startswith("/") and not raw.startswith("~"):
        try:
            import config
            return str(Path(config.PROJECT_ROOT) / raw)
        except Exception:
            return raw
    return _expand(raw)


def _read_file(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _ac_lines(text: str) -> list[str]:
    return _AC_RE.findall(text)


def _all_ac_lines(text: str) -> list[str]:
    """Return all AC lines — both checked (- [x]) and unchecked (- [ ])."""
    return _ALL_AC_RE.findall(text)


def _has_clean_ac(lines: list[str]) -> bool:
    return bool(lines) and not any(_VAGUE_TOKENS_RE.search(ln) for ln in lines)


def _is_unblocked(task: dict) -> bool:
    blockers = task.get("blocked_by") or []
    return all(b.get("resolved", False) for b in blockers)


def _resolve_path(raw: str, root: Path) -> Path:
    """Resolve a raw path string against root if not already absolute."""
    p = Path(raw)
    return p if p.is_absolute() else root / p


def _check_has_file_paths(text: str, plan_path: Optional[str]) -> tuple[bool, str]:
    """Check 5: at least one non-self path resolves to a real file in the repo."""
    root = _get_project_root()
    plan_resolved = Path(plan_path).resolve() if plan_path else None

    all_matches = _FILE_PATH_RE.findall(text)
    for raw in all_matches:
        candidate = _resolve_path(raw, root)
        if plan_resolved and candidate.resolve() == plan_resolved:
            continue
        if candidate.exists():
            return True, f"found: {raw}"

    if not all_matches:
        return False, "no file names mentioned (e.g. foo.py, bar.tsx)"
    non_self = [
        r for r in all_matches
        if not plan_resolved or _resolve_path(r, root).resolve() != plan_resolved
    ]
    if not non_self:
        return False, "only this spec file is mentioned — no other files named"
    return False, "none of the mentioned file paths can be found in the project"


def _check_referenced_files_exist(text: str, plan_path: Optional[str]) -> tuple[bool, str]:
    """Check 7: at least 50% of non-self file paths in body resolve to real files."""
    root = _get_project_root()
    plan_resolved = Path(plan_path).resolve() if plan_path else None

    non_self: list[str] = []
    for raw in _FILE_PATH_RE.findall(text):
        candidate = _resolve_path(raw, root)
        if plan_resolved and candidate.resolve() == plan_resolved:
            continue
        non_self.append(raw)

    if not non_self:
        return False, "no file paths listed to check"

    found, missing = 0, []
    for raw in non_self:
        if _resolve_path(raw, root).exists():
            found += 1
        else:
            missing.append(raw)

    pct = found / len(non_self)
    suffix = f"; missing: {', '.join(missing[:3])}" if missing else ""
    detail = f"{found} of {len(non_self)} files found{suffix}"
    return pct >= 0.5, detail


def _check_in_repo_scope(body: str, title: str) -> tuple[bool, str]:
    """Check in_repo_scope: task/spec is scoped to this repo (not upstream/external)."""
    if _UPSTREAM_TITLE_RE.search(title):
        return False, f"title starts with upstream marker: {title[:60]}"
    m = _OUT_OF_REPO_RE.search(body)
    if m:
        return False, f"description refers to work outside this project: {m.group(0)}"
    return True, "task is scoped to this project"


def _check_outcome_concrete(title: str, description: str) -> tuple[bool, str]:
    """Check outcome_concrete: title+description don't contain vague language.

    Fails when:
    - title is empty
    - title matches a bare generic pattern ("update X", "fix X" with no qualifier)
    - _VAGUE_TOKENS_RE matches anywhere in title + " " + description
    """
    if not title or not title.strip():
        return False, "title is empty"
    if _GENERIC_TITLE_RE.match(title.strip()):
        return False, f"title is too generic: '{title.strip()}' — add a concrete specifier"
    body_text = title + " " + description
    m = _VAGUE_TOKENS_RE.search(body_text)
    if m:
        context = body_text[max(0, m.start() - 20):m.end() + 20].replace("\n", " ")
        return False, f"vague language in title/description: …{context}…"
    return True, "title and description are concrete"


# ---------------------------------------------------------------------------
# Section-presence checks for typed specs (vision / customer_docs / prototype)
# ---------------------------------------------------------------------------

# Any markdown heading (used to slice section bodies)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)

# Spec `type` read only from the leading YAML frontmatter block
_FM_TYPE_RE = re.compile(r"^type:\s*([A-Za-z_]+)\s*$", re.MULTILINE)


def _extract_spec_type(text: str) -> Optional[str]:
    """Read the spec `type` from the leading --- frontmatter, if present."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    block = text[: end if end != -1 else 500]
    m = _FM_TYPE_RE.search(block)
    return m.group(1).lower() if m else None


def _section_body(text: str, names: list[str]) -> Optional[str]:
    """Return the body under the first heading whose text contains any name."""
    headings = list(_HEADING_RE.finditer(text))
    for i, m in enumerate(headings):
        heading = m.group(1).strip().lower()
        if any(n in heading for n in names):
            start = m.end()
            stop = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            return text[start:stop].strip()
    return None


def _check_section_present(text: str, names: list[str], label: str) -> tuple[bool, str]:
    """Pass when a section matching one of `names` exists and has content."""
    body = _section_body(text, [n.lower() for n in names])
    if body is None:
        return False, f"add a '{label}' section"
    if not body:
        return False, f"'{label}' section is empty"
    return True, f"{label} provided"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_task_readiness(task: dict) -> Readiness:
    """Return Readiness for a task dict (3 checks as of →1564).

    Checks: outcome_concrete, in_repo_scope, is_unblocked.
    Tasks are their own spec; plan path / file / AC structure not required.
    """
    description = task.get("description") or ""
    title = task.get("title") or ""
    checks: list[ReadinessCheck] = []

    # Check 1: title and description are concrete (no vague language, no generic title)
    concrete, concrete_detail = _check_outcome_concrete(title, description)
    checks.append(ReadinessCheck(name="outcome_concrete", passed=concrete, detail=concrete_detail))

    # Check 2: task is scoped to this repo
    in_scope, scope_detail = _check_in_repo_scope(description, title)
    checks.append(ReadinessCheck(name="in_repo_scope", passed=in_scope, detail=scope_detail))

    # Check 3: task not blocked
    unblocked = _is_unblocked(task)
    open_blockers = [b["text"] for b in (task.get("blocked_by") or []) if not b.get("resolved")]
    checks.append(ReadinessCheck(
        name="is_unblocked",
        passed=unblocked,
        detail="no open blockers" if unblocked else f"blocked by: {open_blockers[0][:60]}",
    ))

    return Readiness(ready=all(c.passed for c in checks), file_path=None, checks=checks)


def compute_spec_readiness(spec_path: str, spec_type: Optional[str] = None) -> Readiness:
    """Return Readiness for a spec document, scoped to its type's profile.

    The spec's `type` (engineering / vision / customer_docs / prototype) selects
    a readiness profile from services.spec_types: an ordered list of checks, each
    tagged required or optional. Only those checks run. `ready` is True when every
    REQUIRED check passes; optional checks surface as "Enhance" but never block.
    Type is read from the spec frontmatter when not passed; defaults to engineering.
    """
    from services.spec_types import get_spec_type

    exists = Path(spec_path).exists()
    text = (_read_file(spec_path) or "") if exists else ""

    # Extract H1 title for in_repo_scope evaluation
    title_m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    spec_title = title_m.group(1).strip() if title_m else ""

    if spec_type is None:
        spec_type = _extract_spec_type(text)
    profile = get_spec_type(spec_type)["readiness_profile"]

    ac = _ac_lines(text) if exists else []          # unchecked only
    all_ac = _all_ac_lines(text) if exists else []  # checked + unchecked
    missing = "not evaluated — file missing"

    def _run(name: str) -> tuple[bool, str]:
        if name == "has_ac_checkboxes":
            return bool(all_ac), (
                f"{len(ac)} items to check off" if ac
                else f"all {len(all_ac)} items already checked" if all_ac
                else ("no checklist items found — add lines like: - [ ] When X happens, Y is the result"
                      if exists else missing)
            )
        if name == "no_vague_ac":
            clean = _has_clean_ac(all_ac)
            vague = [ln for ln in all_ac if _VAGUE_TOKENS_RE.search(ln)]
            return clean, (
                "all checklist items are specific" if clean
                else (f"too vague: {vague[0][:60]}" if vague else "not evaluated — no checklist items")
            )
        if name == "has_file_paths":
            return _check_has_file_paths(text, spec_path) if exists else (False, missing)
        if name == "referenced_files_exist":
            return _check_referenced_files_exist(text, spec_path) if exists else (False, missing)
        if name == "in_repo_scope":
            return _check_in_repo_scope(text, spec_title)
        if name == "has_success_measures":
            return (_check_section_present(
                text, ["success measures", "success measure", "how we'll measure", "how we will measure"],
                "Success measures") if exists else (False, missing))
        if name == "has_audience":
            return (_check_section_present(
                text, ["audience", "who is this for", "who it's for"],
                "Audience") if exists else (False, missing))
        if name == "has_learning_goal":
            return (_check_section_present(
                text, ["learning goal", "what we'll learn", "what we will learn"],
                "Learning goal") if exists else (False, missing))
        if name == "has_outline":
            return (_check_section_present(text, ["outline"], "Outline") if exists else (False, missing))
        return False, f"unknown check: {name}"

    checks: list[ReadinessCheck] = []
    for item in profile:
        name = item["check"]
        passed, detail = _run(name)
        checks.append(ReadinessCheck(name=name, passed=passed, detail=detail, required=item["required"]))

    # ready = every REQUIRED check passes; optional checks inform but never block
    ready = all(c.passed for c in checks if c.required)
    return Readiness(ready=ready, file_path=spec_path, checks=checks)
