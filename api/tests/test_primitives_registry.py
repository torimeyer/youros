"""Tests for the myOS primitives registry.

See plan: ~/.claude/plans/any-time-youre-working-effervescent-shore.md
The 10-rule definition of a myOS primitive lives in that plan.
"""
from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_registry_imports_cleanly():
    """The registry module imports without side effects."""
    import services.primitives_registry as reg
    assert hasattr(reg, "Primitive")
    assert hasattr(reg, "PRIMITIVES")
    assert hasattr(reg, "get")
    assert hasattr(reg, "all_active")
    assert hasattr(reg, "contract")


def test_five_primitives_registered():
    """time, memory, skill, heartbeat, audit are all registered."""
    from services.primitives_registry import PRIMITIVES
    expected = {"time", "memory", "skill", "heartbeat", "audit"}
    assert set(PRIMITIVES.keys()) >= expected, f"missing: {expected - set(PRIMITIVES.keys())}"


def test_get_returns_primitive_record():
    """get('time') returns a full Primitive dataclass."""
    from services.primitives_registry import get, Primitive
    p = get("time")
    assert p is not None
    assert isinstance(p, Primitive)
    assert p.name == "time"
    assert p.version.startswith("v1") or p.version == "1.0"
    assert p.module_path == "services.time_primitive"
    assert "TimeStatus" in p.exported_symbols
    assert "start" in p.exported_symbols
    assert any("/api/time/" in r for r in p.http_routes)


def test_get_returns_none_for_unknown():
    from services.primitives_registry import get
    assert get("does-not-exist") is None


def test_all_active_returns_active_only():
    from services.primitives_registry import all_active, get
    active = all_active()
    assert len(active) >= 5
    for p in active:
        assert p.status == "active"


def test_module_paths_import_cleanly():
    """Every registered primitive's module_path imports without error.

    External primitives (audit lives in ostk, not Python) are excluded
    by the 'external:' prefix.
    """
    from services.primitives_registry import all_active
    for p in all_active():
        if p.module_path.startswith("external:"):
            continue
        mod = importlib.import_module(p.module_path)
        for sym in p.exported_symbols:
            assert hasattr(mod, sym), f"{p.module_path} missing {sym}"


def test_contract_returns_parsed_doc():
    """contract('time') returns parsed sections from docs/primitives/time.md."""
    from services.primitives_registry import contract
    c = contract("time")
    assert c is not None
    assert "purpose" in c
    assert "contract" in c
    assert len(c["purpose"]) > 0
    assert len(c["contract"]) > 0


def test_contract_returns_none_for_unknown():
    from services.primitives_registry import contract
    assert contract("does-not-exist") is None


def test_contract_doc_files_exist():
    """Every registered primitive's doc_path file exists on disk."""
    from services.primitives_registry import all_active
    for p in all_active():
        doc = REPO_ROOT / p.doc_path
        assert doc.exists(), f"missing doc file: {doc}"


def test_linter_exits_zero():
    """scripts/check_primitive_contracts.py exits 0 against the current registry."""
    linter = REPO_ROOT / "scripts" / "check_primitive_contracts.py"
    assert linter.exists()
    result = subprocess.run(
        ["python3", str(linter)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"linter failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_api_primitives_route_present_in_main():
    """The /api/primitives router is mounted in api/main.py."""
    main_py = (REPO_ROOT / "api" / "main.py").read_text()
    assert "primitives" in main_py.lower()
