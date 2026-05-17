#!/usr/bin/env python3
"""
scripts/check_primitive_contracts.py

Pre-commit linter for the myOS primitives registry.

Exits 0 when everything is consistent. Exits 1 and prints a list of
violations if any check fails.

Checks performed:
  1. Every declared exported_symbol exists in the module (non-external only).
  2. Every doc_path exists and has "## Purpose" and "## Contract" sections.
  3. Every api/services/*_primitive.py has a corresponding registry entry.
  4. Every non-empty http_route prefix appears in some api/routers/*.py file.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

# Resolve paths relative to the repo root (one level above scripts/).
_REPO_ROOT = Path(__file__).parent.parent
_API_DIR = _REPO_ROOT / "api"

# Put the api/ directory on sys.path so we can import services.* / routers.*
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from services.primitives_registry import PRIMITIVES  # noqa: E402

_errors: list[str] = []


# ---------------------------------------------------------------------------
# Check 1: exported symbols exist in module
# ---------------------------------------------------------------------------


def _check_symbols() -> None:
    for name, prim in PRIMITIVES.items():
        if prim.module_path.startswith("external:"):
            continue  # kernel primitive — no Python module to inspect
        try:
            mod = importlib.import_module(prim.module_path)
        except ImportError as exc:
            _errors.append(f"[{name}] Cannot import '{prim.module_path}': {exc}")
            continue
        for sym in prim.exported_symbols:
            if not hasattr(mod, sym):
                _errors.append(
                    f"[{name}] Symbol '{sym}' declared in registry"
                    f" but not found in module '{prim.module_path}'"
                )


# ---------------------------------------------------------------------------
# Check 2: doc file exists and has required sections
# ---------------------------------------------------------------------------


def _check_docs() -> None:
    for name, prim in PRIMITIVES.items():
        doc = _REPO_ROOT / prim.doc_path
        if not doc.exists():
            _errors.append(f"[{name}] Doc file missing: {prim.doc_path}")
            continue
        text = doc.read_text(encoding="utf-8")
        for section in ("Purpose", "Contract"):
            if not re.search(rf"^\s*#{{1,6}}\s+{section}\b", text, re.IGNORECASE | re.MULTILINE):
                _errors.append(
                    f"[{name}] Doc '{prim.doc_path}' is missing"
                    f" a '## {section}' section"
                )


# ---------------------------------------------------------------------------
# Check 3: no orphan *_primitive.py files in api/services/
# ---------------------------------------------------------------------------


def _check_orphan_primitives() -> None:
    registered = set(PRIMITIVES)
    services_dir = _API_DIR / "services"
    for f in sorted(services_dir.glob("*_primitive.py")):
        derived = f.stem.removesuffix("_primitive")  # "time_primitive" -> "time"
        if derived not in registered:
            _errors.append(
                f"[orphan] '{f.name}' exists in api/services/ but"
                f" '{derived}' has no entry in PRIMITIVES"
            )


# ---------------------------------------------------------------------------
# Check 4: http_route prefixes appear in some api/routers/*.py source file
# ---------------------------------------------------------------------------


def _check_routes() -> None:
    routers_dir = _API_DIR / "routers"
    router_text = "\n".join(f.read_text(encoding="utf-8") for f in routers_dir.glob("*.py"))

    for name, prim in PRIMITIVES.items():
        for route in prim.http_routes:
            # Strip the leading /api prefix that main.py adds via prefix=
            path = route.removeprefix("/api")
            # Extract the static prefix before any {param} segment
            prefix = path.split("{")[0].rstrip("/")
            if not prefix:
                continue
            # The prefix (e.g. "/time/status/") should appear inside a
            # @router.get(...) or similar decorator in some router file.
            if prefix not in router_text:
                _errors.append(
                    f"[{name}] Route '{route}' (static prefix '{prefix}')"
                    f" not found in any api/routers/*.py file"
                )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    _check_symbols()
    _check_docs()
    _check_orphan_primitives()
    _check_routes()

    if _errors:
        print(f"Primitive contract linter FAILED ({len(_errors)} violation(s)):")
        for err in _errors:
            print(f"  ✗ {err}")
        sys.exit(1)

    count = len(PRIMITIVES)
    print(f"Primitive contract linter OK — {count} primitive(s) checked, 0 violations")
    sys.exit(0)


if __name__ == "__main__":
    main()
