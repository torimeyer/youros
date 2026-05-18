"""Gemini-ready readiness service (→1467).

Computes whether a task or spec can be handed to Gemini. Returns a
Readiness object with 6 named checks so the UI tooltip can show exactly
which check(s) failed.

All functions are pure / synchronous — no I/O side effects.
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


@dataclass
class Readiness:
    ready: bool
    file_path: Optional[str]          # the linked plan/spec file (if found)
    checks: list[ReadinessCheck] = field(default_factory=list)  # always 6

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "file_path": self.file_path,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches docs/spec/*.md, ~/.myos/specs/*.md (~/ or absolute), ~/.claude/plans/*.md
# \S*? lazily matches any leading non-whitespace (including ~/ or /abs/path/)
# before the canonical directory suffix.
_PLAN_PATH_RE = re.compile(
    r"(?:"
    r"docs/spec/[\w.\-]+\.md"
    r"|"
    r"\S*?\.myos/specs/[\w.\-]+\.md"
    r"|"
    r"\S*?\.claude/plans/[\w.\-]+\.md"
    r")"
)

# Matches unchecked AC checkbox lines
_AC_RE = re.compile(r"^\s*-\s+\[\s\]\s+(.+)$", re.MULTILINE)

# Tokens that disqualify an AC line (case-insensitive)
_VAGUE_TOKENS_RE = re.compile(
    r"\bTBD\b|\?|should we|\bdecide\b|\bTODO\b", re.IGNORECASE
)

# File paths: at least one word/slash/dot component ending with a known extension
_FILE_PATH_RE = re.compile(
    r"[\w./\-]+\.(?:py|tsx|ts|js|jsx|md|sql|sh|json|ya?ml|toml)\b"
)

# Check names (ordered; must stay consistent with test expectations)
_CHECK_NAMES = [
    "plan_path_present",
    "file_exists",
    "has_ac_checkboxes",
    "no_vague_ac",
    "has_file_paths",
    "is_unblocked",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _expand(path: str) -> str:
    """Expand ~ and return absolute path."""
    return str(Path(os.path.expanduser(path)).resolve())


def _extract_plan_path(text: str) -> Optional[str]:
    """Return the first plan/spec path found in text, expanded and absolute."""
    m = _PLAN_PATH_RE.search(text)
    if not m:
        return None
    raw = m.group(0)
    # If relative (docs/spec/…), resolve relative to PROJECT_ROOT
    if not raw.startswith("/") and not raw.startswith("~"):
        try:
            from config import PROJECT_ROOT
            return str(Path(PROJECT_ROOT) / raw)
        except Exception:
            return raw  # fallback: return as-is
    return _expand(raw)


def _read_file(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _ac_lines(text: str) -> list[str]:
    return _AC_RE.findall(text)


def _has_clean_ac(lines: list[str]) -> bool:
    return bool(lines) and not any(_VAGUE_TOKENS_RE.search(ln) for ln in lines)


def _has_file_paths(text: str) -> bool:
    return bool(_FILE_PATH_RE.search(text))


def _is_unblocked(task: dict) -> bool:
    blockers = task.get("blocked_by") or []
    return all(b.get("resolved", False) for b in blockers)


def _make_all_checks(passed: bool, from_check: int, checks: list[ReadinessCheck]) -> list[ReadinessCheck]:
    """Pad checks list to 6 entries with passed=False for skipped ones."""
    result = list(checks)
    for name in _CHECK_NAMES[len(result):]:
        result.append(ReadinessCheck(name=name, passed=False, detail="not evaluated"))
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_task_readiness(task: dict) -> Readiness:
    """Return Readiness for a task dict (has 'description' and 'blocked_by')."""
    description = task.get("description") or ""
    checks: list[ReadinessCheck] = []

    # Check 1: plan path present in description
    plan_path = _extract_plan_path(description)
    checks.append(ReadinessCheck(
        name="plan_path_present",
        passed=plan_path is not None,
        detail=f"found: {plan_path}" if plan_path else "no docs/spec, ~/.myos/specs, or ~/.claude/plans path found",
    ))
    if plan_path is None:
        return Readiness(ready=False, file_path=None, checks=_make_all_checks(False, 1, checks))

    # Check 2: file exists
    exists = Path(plan_path).exists()
    checks.append(ReadinessCheck(
        name="file_exists",
        passed=exists,
        detail=f"{plan_path} exists" if exists else f"{plan_path} not found on disk",
    ))
    if not exists:
        return Readiness(ready=False, file_path=plan_path, checks=_make_all_checks(False, 2, checks))

    text = _read_file(plan_path) or ""

    # Check 3: has AC checkboxes
    ac = _ac_lines(text)
    has_ac = bool(ac)
    checks.append(ReadinessCheck(
        name="has_ac_checkboxes",
        passed=has_ac,
        detail=f"{len(ac)} unchecked AC items" if has_ac else "no '- [ ]' lines found",
    ))
    if not has_ac:
        return Readiness(ready=False, file_path=plan_path, checks=_make_all_checks(False, 3, checks))

    # Check 4: no vague AC
    clean = _has_clean_ac(ac)
    vague = [ln for ln in ac if _VAGUE_TOKENS_RE.search(ln)]
    checks.append(ReadinessCheck(
        name="no_vague_ac",
        passed=clean,
        detail="all AC lines are concrete" if clean else f"vague tokens in: {vague[0][:60]}",
    ))
    if not clean:
        return Readiness(ready=False, file_path=plan_path, checks=_make_all_checks(False, 4, checks))

    # Check 5: file body has at least one explicit file path
    has_files = _has_file_paths(text)
    checks.append(ReadinessCheck(
        name="has_file_paths",
        passed=has_files,
        detail="file paths found in plan body" if has_files else "no explicit file paths (e.g. foo.py, bar.tsx) found",
    ))
    if not has_files:
        return Readiness(ready=False, file_path=plan_path, checks=_make_all_checks(False, 5, checks))

    # Check 6: task not blocked
    unblocked = _is_unblocked(task)
    open_blockers = [b["text"] for b in (task.get("blocked_by") or []) if not b.get("resolved")]
    checks.append(ReadinessCheck(
        name="is_unblocked",
        passed=unblocked,
        detail="no open blockers" if unblocked else f"blocked by: {open_blockers[0][:60]}",
    ))

    ready = unblocked
    return Readiness(ready=ready, file_path=plan_path, checks=checks)


def compute_spec_readiness(spec_path: str) -> Readiness:
    """Return Readiness for a spec document.

    Check 1 is skipped (the spec IS the plan). Checks 2–6 run against
    the spec file itself. Check 6 always passes (specs have no blocked_by).
    """
    checks: list[ReadinessCheck] = []

    # Check 1: skipped — use a synthetic pass for consistent 6-item list
    checks.append(ReadinessCheck(
        name="plan_path_present",
        passed=True,
        detail="spec is its own plan file",
    ))

    # Check 2: file exists
    exists = Path(spec_path).exists()
    checks.append(ReadinessCheck(
        name="file_exists",
        passed=exists,
        detail=f"{spec_path} exists" if exists else f"{spec_path} not found on disk",
    ))
    if not exists:
        return Readiness(ready=False, file_path=spec_path, checks=_make_all_checks(False, 2, checks))

    text = _read_file(spec_path) or ""

    # Check 3: has AC checkboxes
    ac = _ac_lines(text)
    has_ac = bool(ac)
    checks.append(ReadinessCheck(
        name="has_ac_checkboxes",
        passed=has_ac,
        detail=f"{len(ac)} unchecked AC items" if has_ac else "no '- [ ]' lines found",
    ))
    if not has_ac:
        return Readiness(ready=False, file_path=spec_path, checks=_make_all_checks(False, 3, checks))

    # Check 4: no vague AC
    clean = _has_clean_ac(ac)
    vague = [ln for ln in ac if _VAGUE_TOKENS_RE.search(ln)]
    checks.append(ReadinessCheck(
        name="no_vague_ac",
        passed=clean,
        detail="all AC lines are concrete" if clean else f"vague tokens in: {vague[0][:60]}",
    ))
    if not clean:
        return Readiness(ready=False, file_path=spec_path, checks=_make_all_checks(False, 4, checks))

    # Check 5: file body has at least one explicit file path
    has_files = _has_file_paths(text)
    checks.append(ReadinessCheck(
        name="has_file_paths",
        passed=has_files,
        detail="file paths found in spec body" if has_files else "no explicit file paths found",
    ))
    if not has_files:
        return Readiness(ready=False, file_path=spec_path, checks=_make_all_checks(False, 5, checks))

    # Check 6: always passes for specs (no blocked_by concept)
    checks.append(ReadinessCheck(
        name="is_unblocked",
        passed=True,
        detail="specs have no blockers",
    ))

    return Readiness(ready=True, file_path=spec_path, checks=checks)
