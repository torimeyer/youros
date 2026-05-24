#!/usr/bin/env python3
"""Pre-commit linter for the myOS primitives registry.

Exits 1 (with findings printed) if any of:
- A registry entry lists a symbol the module doesn't export.
- A registry entry lists an HTTP route not present in api/main.py + routers.
- A doc file is missing or lacks required sections (Purpose, Contract).
- A file matching api/services/*_primitive.py exists but isn't in the registry.

Run from the repo root: `python3 scripts/check_primitive_contracts.py`.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "api"))


def main() -> int:
    findings: list[str] = []

    from services.primitives_registry import all_active, PRIMITIVES  # noqa: E402

    # Check 1: module symbols
    for p in all_active():
        if p.module_path.startswith("external:"):
            continue
        try:
            mod = importlib.import_module(p.module_path)
        except Exception as exc:
            findings.append(f"[{p.name}] cannot import {p.module_path}: {exc}")
            continue
        for sym in p.exported_symbols:
            if not hasattr(mod, sym):
                findings.append(f"[{p.name}] missing symbol '{sym}' in {p.module_path}")

    # Check 2: HTTP routes present in routers code (best-effort grep)
    router_text = ""
    for routers_file in (REPO_ROOT / "api" / "routers").glob("*.py"):
        try:
            router_text += routers_file.read_text(encoding="utf-8")
        except Exception:
            pass
    main_py = (REPO_ROOT / "api" / "main.py").read_text(encoding="utf-8")
    haystack = router_text + main_py
    for p in all_active():
        for route in p.http_routes:
            # Normalize route param syntax: /api/x/{id} → /x/ or /x in source
            tail = route.replace("/api", "", 1)
            stem = tail.split("{")[0].rstrip("/")
            if stem and stem not in haystack:
                findings.append(f"[{p.name}] route '{route}' not found in routers code")

    # Check 3: doc files exist + have required sections
    for p in all_active():
        doc = REPO_ROOT / p.doc_path
        if not doc.exists():
            findings.append(f"[{p.name}] missing doc file: {p.doc_path}")
            continue
        text = doc.read_text(encoding="utf-8")
        required = ("Purpose", "Contract")
        for sec in required:
            if f"## {sec}" not in text:
                findings.append(f"[{p.name}] doc {p.doc_path} missing '## {sec}' section")

    # Check 4: orphaned *_primitive.py files in api/services/
    registered_modules = {p.module_path for p in all_active()}
    services_dir = REPO_ROOT / "api" / "services"
    for f in services_dir.glob("*_primitive.py"):
        mod_path = f"services.{f.stem}"
        if mod_path not in registered_modules:
            findings.append(
                f"orphan: {f.relative_to(REPO_ROOT)} matches *_primitive.py but is not registered"
            )

    if findings:
        print(f"primitive contract check: {len(findings)} finding(s)")
        for f in findings:
            print(f"  - {f}")
        return 1
    print(f"primitive contract check: OK ({len(list(all_active()))} primitives, 0 findings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
