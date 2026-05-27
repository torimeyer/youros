"""Regression tests for →1739 / →1738: list_docs() spec_audit scan must not
block the event loop.

Before the fix, list_docs() called compute_shipped() and compute_husk_status()
synchronously on the event loop for every spec file (Path.read_text() +
Path.exists() per file ref). With N specs and M file refs this stalled all
concurrent requests (health, WS feeds, /api/agents/spawn) for the full scan duration.

After the fix, the spec_audit enrichment loop runs off-loop via
asyncio.to_thread so the event loop stays responsive during the scan.
"""
import asyncio
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import ostk as ostk_module
from services.ostk import OstkService


SPEC_CONTENT = """---
title: {title}
status: spec
---

## Problem

Something needs fixing.

## Acceptance criteria

- [ ] FR-001: Thing works

References: api/routers/agents.py frontend/src/App.tsx
"""


@pytest.mark.asyncio
async def test_list_docs_spec_audit_does_not_block_event_loop(tmp_path, monkeypatch):
    """The spec_audit scan must run off the event loop so concurrent work keeps ticking.

    Inject a slow compute_shipped (50 ms per spec x 5 specs = 250 ms total).
    If the scan runs on the loop, the heartbeat coroutine cannot tick during that
    window and the assertion fails. After the fix (asyncio.to_thread), the loop
    stays free and the heartbeat accumulates ticks throughout.
    """
    spec_dir = tmp_path / "docs" / "spec"
    spec_dir.mkdir(parents=True)
    for i in range(5):
        (spec_dir / f"spec-{i}.md").write_text(SPEC_CONTENT.format(title=f"Spec {i}"))

    import services.spec_audit as audit_mod

    def slow_compute_shipped(path, repo_root, needle_statuses):
        time.sleep(0.05)  # 50 ms per spec => 250 ms total
        return audit_mod.ShippedResult(is_shipped=True)

    monkeypatch.setattr(audit_mod, "compute_shipped", slow_compute_shipped)

    async def fake_list_tasks(status=None, priority=None):
        return []

    svc = OstkService(cwd=str(tmp_path))
    monkeypatch.setattr(svc, "list_tasks", fake_list_tasks)

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(50):
            await asyncio.sleep(0.01)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    await svc.list_docs()
    hb.cancel()

    assert ticks >= 15, (
        f"Event loop was starved during list_docs spec_audit scan "
        f"(only {ticks}/50 ticks fired - scan is still blocking the loop). "
        f"Fix: wrap the spec_audit enrichment loop in asyncio.to_thread."
    )


@pytest.mark.asyncio
async def test_list_docs_audit_fields_correct_after_offload(tmp_path, monkeypatch):
    """Offloading to a thread must not corrupt the returned doc list."""
    spec_dir = tmp_path / "docs" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "my-spec.md").write_text(SPEC_CONTENT.format(title="My Spec"))

    async def fake_list_tasks(status=None, priority=None):
        return []

    # Patch USER_SPECS_DIR so real ~/.myos/specs/ is not scanned
    import services.ostk as ostk_module
    empty_user_specs = tmp_path / "myos_specs"
    empty_user_specs.mkdir()
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", empty_user_specs)

    svc = OstkService(cwd=str(tmp_path))
    monkeypatch.setattr(svc, "list_tasks", fake_list_tasks)

    docs = await svc.list_docs()
    spec_docs = [d for d in docs if d.get("status") != "plan"]
    assert len(spec_docs) == 1, f"expected 1 spec, got {len(spec_docs)} (USER_SPECS_DIR not patched?)"
    doc = spec_docs[0]

    assert "stage" in doc, "stage field missing - spec_audit enrichment did not run"
    assert "husk" in doc, "husk field missing - spec_audit enrichment did not run"
    assert "missing_files" in doc, "missing_files field missing"
    assert "open_linked_needles" in doc, "open_linked_needles field missing"
