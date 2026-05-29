"""Tests for the roadmap-to-task flow (fix for →1952).

Two bugs fixed:
  Bug 1 - NameError in _latest_roadmap_path (MYOS_FILES_DIR bare name)
  Bug 2 - parse_roadmap_items returns 0 items for table and quarter-dash formats
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.automation_outputs import parse_roadmap_items


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TABLE_ROADMAP = """\
| Quarter | Theme | Initiatives |
|---------|-------|-------------|
| **Q2 2026** | Growth | Initiative Alpha · Initiative Beta · Initiative Gamma |
| **Q3 2026** | Scale | Build monitoring · Improve onboarding |
"""

QUARTER_BULLET_ROADMAP = """\
## Q2 2026 — Growth & Retention

- Launch new onboarding flow
- Improve retention metrics

## Q3 2026 — Scale & Reliability

- Add monitoring dashboard
- Reduce p99 latency
"""


# ---------------------------------------------------------------------------
# Test 1: table format — column named "Initiatives", cells split on ·
# ---------------------------------------------------------------------------

def test_parse_roadmap_items_table_format():
    items = parse_roadmap_items(TABLE_ROADMAP)
    assert len(items) >= 5, f"Expected 5+ items from table, got {len(items)}: {items}"
    titles = [i.lower() for i in items]
    assert any("initiative alpha" in t for t in titles), titles
    assert any("initiative beta" in t for t in titles), titles
    assert any("initiative gamma" in t for t in titles), titles
    assert any("build monitoring" in t for t in titles), titles
    assert any("improve onboarding" in t for t in titles), titles


# ---------------------------------------------------------------------------
# Test 2: quarter heading with trailing " — theme text" + bullet items
# ---------------------------------------------------------------------------

def test_parse_roadmap_items_quarter_bullet_format():
    items = parse_roadmap_items(QUARTER_BULLET_ROADMAP)
    assert len(items) >= 4, f"Expected 4+ items from bullet format, got {len(items)}: {items}"
    titles = [i.lower() for i in items]
    assert any("onboarding flow" in t for t in titles), titles
    assert any("retention" in t for t in titles), titles
    assert any("monitoring" in t for t in titles), titles
    assert any("latency" in t for t in titles), titles


# ---------------------------------------------------------------------------
# Test 3: _latest_roadmap_path resolves without NameError after fix
# ---------------------------------------------------------------------------

def test_latest_roadmap_path_no_nameerror(tmp_path):
    roadmap = tmp_path / "roadmap.md"
    roadmap.write_text("# Roadmap\n\n- Do the thing\n")

    from routers.chat import _latest_roadmap_path

    with patch("services.files_dir.get_files_dir", return_value=tmp_path):
        result = _latest_roadmap_path()

    assert result == roadmap
    assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# Test 4: endpoint returns status ok + created > 0 on fixture roadmap
# ---------------------------------------------------------------------------

def test_chat_create_tasks_from_roadmap_e2e(tmp_path):
    roadmap = tmp_path / "roadmap.md"
    roadmap.write_text(TABLE_ROADMAP)

    from routers.chat import chat_create_tasks_from_roadmap

    fake_tasks = [
        {"id": "t1", "title": "Initiative Alpha"},
        {"id": "t2", "title": "Initiative Beta"},
    ]

    async def _run():
        return await chat_create_tasks_from_roadmap(
            {"roadmap_path": str(roadmap), "agent_name": "e2e-roadmap-test"}
        )

    with patch(
        "services.automation_outputs.auto_create_tasks",
        new_callable=AsyncMock,
        return_value=fake_tasks,
    ):
        result = asyncio.run(_run())

    assert result["status"] == "ok", f"Expected status ok, got: {result}"
    assert len(result["created"]) > 0, f"Expected created > 0, got: {result['created']}"
