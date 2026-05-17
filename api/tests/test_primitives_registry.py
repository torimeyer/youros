"""
api/tests/test_primitives_registry.py

TDD suite for the myOS primitives registry (Phase 5).
These tests are written BEFORE the implementation — run RED first, then GREEN.

Coverage:
  - get / all_active / contract functions
  - Every non-external module_path must be importable
  - Every declared exported_symbol must exist in the module
  - audit primitive must be external:ostk
  - Linter script must exit 0 on a clean repo
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from services.primitives_registry import (
    PRIMITIVES,
    Primitive,
    all_active,
    contract,
    get,
)


# ── Registry lookups ──────────────────────────────────────────────────────────


def test_get_known_primitive():
    p = get("time")
    assert p is not None
    assert isinstance(p, Primitive)
    assert p.name == "time"
    assert p.version == "1.0"


def test_get_unknown_returns_none():
    assert get("nonexistent_primitive_xyz") is None


def test_all_active_includes_required_primitives():
    active = all_active()
    names = {p.name for p in active}
    assert {"time", "memory", "skill", "heartbeat", "audit"} <= names


def test_all_active_excludes_deprecated():
    active = all_active()
    for p in active:
        assert p.status != "deprecated"


def test_all_active_returns_primitives():
    active = all_active()
    for p in active:
        assert isinstance(p, Primitive)


# ── contract() ────────────────────────────────────────────────────────────────


def test_contract_time_has_required_keys():
    c = contract("time")
    assert c is not None
    for key in ("name", "version", "module_path", "doc_path", "exported_symbols", "http_routes"):
        assert key in c, f"contract() result is missing key '{key}'"


def test_contract_unknown_returns_none():
    assert contract("nonexistent_xyz") is None


def test_contract_all_primitives_non_empty():
    for name in PRIMITIVES:
        c = contract(name)
        assert c is not None, f"contract('{name}') returned None"
        assert c["name"] == name


# ── Primitive field sanity ─────────────────────────────────────────────────────


def test_all_primitives_have_doc_path():
    for name, p in PRIMITIVES.items():
        assert p.doc_path, f"Primitive '{name}' has empty doc_path"
        assert p.doc_path.endswith(".md"), f"Primitive '{name}' doc_path should be a .md file"


def test_all_primitives_have_version():
    for name, p in PRIMITIVES.items():
        assert p.version, f"Primitive '{name}' has empty version"


# ── Module imports ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["time", "memory", "skill", "heartbeat"])
def test_module_path_imports_cleanly(name):
    """Every non-external module_path must be importable."""
    p = get(name)
    assert p is not None
    if p.module_path.startswith("external:"):
        pytest.skip("external primitive has no Python module")
    mod = importlib.import_module(p.module_path)
    assert mod is not None


@pytest.mark.parametrize("name", ["time", "memory", "skill", "heartbeat"])
def test_exported_symbols_exist_in_module(name):
    """Every declared symbol must exist in the module."""
    p = get(name)
    assert p is not None
    if p.module_path.startswith("external:"):
        pytest.skip("external primitive has no Python module")
    mod = importlib.import_module(p.module_path)
    for sym in p.exported_symbols:
        assert hasattr(mod, sym), (
            f"Module '{p.module_path}' is missing declared symbol '{sym}'"
        )


# ── audit is external ─────────────────────────────────────────────────────────


def test_audit_primitive_is_external():
    p = get("audit")
    assert p is not None
    assert p.module_path.startswith("external:"), (
        "audit primitive has no Python module; module_path must start with 'external:'"
    )


# ── Linter ────────────────────────────────────────────────────────────────────


def test_linter_exits_zero():
    """scripts/check_primitive_contracts.py must exit 0 on a clean repo."""
    # api/tests -> api -> worktree root
    repo_root = Path(__file__).parent.parent.parent
    script = repo_root / "scripts" / "check_primitive_contracts.py"
    assert script.exists(), f"Linter script not found: {script}"

    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    assert result.returncode == 0, (
        f"Linter exited {result.returncode}:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
