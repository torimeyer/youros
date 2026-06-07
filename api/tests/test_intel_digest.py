"""Tests for GET /api/intel/digest (→1441 Theme C v2 — SC-016, SC-017, FR-005)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def captures_dir(tmp_path, monkeypatch):
    """Redirect CAPTURES_DIR to a temp path so tests never write to ~/.youros/."""
    d = tmp_path / "intel" / "captures"
    d.mkdir(parents=True, exist_ok=True)
    import routers.intel as intel_router
    monkeypatch.setattr(intel_router, "CAPTURES_DIR", d)
    return d


@pytest.fixture()
def specs_dir(tmp_path, monkeypatch):
    """Redirect DIGEST_SPECS_DIR to a temp path so tests never write to ~/.youros/specs/."""
    d = tmp_path / "specs"
    d.mkdir(parents=True, exist_ok=True)
    import routers.intel as intel_router
    monkeypatch.setattr(intel_router, "DIGEST_SPECS_DIR", d)
    return d


@pytest.fixture()
def mock_synthesizer(monkeypatch):
    """Replace the LLM synthesizer with a deterministic stub so tests are offline."""
    import routers.intel as intel_router

    async def _stub(captures: list[dict]) -> str:
        if not captures:
            return "## Intel Digest\n\nNo signals captured in this window."
        names = [c.get("competitor", "?") for c in captures]
        return f"## Intel Digest\n\n{len(captures)} signal(s): {', '.join(names)}"

    monkeypatch.setattr(intel_router, "_synthesize", _stub)


@pytest_asyncio.fixture()
async def client(captures_dir, specs_dir, mock_synthesizer):
    from main import app

    async with AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_capture(d: Path, competitor: str, days_ago: int = 0, **extra) -> dict:
    """Write a capture JSON at a specific age relative to now."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    cap_id = f"cap-{competitor.replace(' ', '-')}-{days_ago}"
    record = {
        "id": cap_id,
        "competitor": competitor,
        "tags": [],
        "url": None,
        "text": None,
        "captured_at": ts,
        **extra,
    }
    (d / f"{cap_id}.json").write_text(json.dumps(record))
    return record


# ---------------------------------------------------------------------------
# SC-016 — GET /api/intel/digest exists and returns 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_endpoint_exists(client):
    """SC-016: endpoint is reachable."""
    resp = await client.get("/api/intel/digest")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_digest_response_shape(client):
    """SC-016: response has digest, capture_count, spec_path keys."""
    resp = await client.get("/api/intel/digest")
    assert resp.status_code == 200
    body = resp.json()
    assert "digest" in body
    assert "capture_count" in body
    assert "spec_path" in body


# ---------------------------------------------------------------------------
# SC-016 — window filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_includes_recent_captures(client, captures_dir):
    """SC-016: captures within window appear in digest."""
    _write_capture(captures_dir, "Recent Co", days_ago=1)
    resp = await client.get("/api/intel/digest?window_days=7")
    assert resp.status_code == 200
    assert "Recent Co" in resp.json()["digest"]


@pytest.mark.asyncio
async def test_digest_excludes_old_captures(client, captures_dir):
    """SC-016: captures older than window are excluded."""
    _write_capture(captures_dir, "Old Co", days_ago=10)
    resp = await client.get("/api/intel/digest?window_days=7")
    body = resp.json()
    assert body["capture_count"] == 0
    assert "Old Co" not in body["digest"]


@pytest.mark.asyncio
async def test_digest_mixed_window(client, captures_dir):
    """SC-016: only in-window captures appear when both old and new exist."""
    _write_capture(captures_dir, "Recent Co", days_ago=2)
    _write_capture(captures_dir, "Old Co", days_ago=30)
    resp = await client.get("/api/intel/digest?window_days=7")
    body = resp.json()
    assert body["capture_count"] == 1
    assert "Recent Co" in body["digest"]
    assert "Old Co" not in body["digest"]


@pytest.mark.asyncio
async def test_digest_reports_capture_count(client, captures_dir):
    """SC-016: capture_count matches how many captures are in the window."""
    _write_capture(captures_dir, "A", days_ago=1)
    _write_capture(captures_dir, "B", days_ago=2)
    _write_capture(captures_dir, "C", days_ago=8)  # outside 7-day window
    resp = await client.get("/api/intel/digest?window_days=7")
    assert resp.json()["capture_count"] == 2


@pytest.mark.asyncio
async def test_digest_empty_when_no_captures(client):
    """SC-016 edge case: empty CAPTURES_DIR returns valid response, not 500."""
    resp = await client.get("/api/intel/digest?window_days=7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["capture_count"] == 0
    assert isinstance(body["digest"], str)


# ---------------------------------------------------------------------------
# SC-017 — spec file promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_promotes_spec_file(client, captures_dir, specs_dir):
    """SC-017: a spec file is written to DIGEST_SPECS_DIR after the request."""
    _write_capture(captures_dir, "Acme Corp", days_ago=1)
    resp = await client.get("/api/intel/digest?window_days=7")
    assert resp.status_code == 200
    spec_files = list(specs_dir.glob("intel-digest-*.md"))
    assert len(spec_files) >= 1, "spec file must be promoted to DIGEST_SPECS_DIR"


@pytest.mark.asyncio
async def test_digest_spec_file_has_digest_content(client, captures_dir, specs_dir):
    """SC-017: spec file content matches the digest returned in the response."""
    _write_capture(captures_dir, "Acme Corp", days_ago=1)
    resp = await client.get("/api/intel/digest?window_days=7")
    body = resp.json()
    spec_files = list(specs_dir.glob("intel-digest-*.md"))
    assert len(spec_files) >= 1
    content = spec_files[0].read_text()
    assert "Acme Corp" in content


@pytest.mark.asyncio
async def test_digest_spec_path_in_response(client, captures_dir, specs_dir):
    """SC-017: spec_path in response points to the written file."""
    _write_capture(captures_dir, "Acme Corp", days_ago=1)
    resp = await client.get("/api/intel/digest?window_days=7")
    body = resp.json()
    spec_path = body["spec_path"]
    assert spec_path is not None
    assert "intel-digest-" in spec_path


# ---------------------------------------------------------------------------
# FR-005 — TODO marker present in chat.py
# ---------------------------------------------------------------------------


def test_todo_marker_exists_in_chat_router():
    """FR-005: the upgrade-path comment must be present in chat.py."""
    chat_py = Path(__file__).parent.parent / "routers" / "chat.py"
    assert chat_py.exists(), "chat.py must exist"
    assert "TODO: replace with intel-synthesize skill" in chat_py.read_text(), (
        "FR-005: chat.py must contain the TODO marker for the intel-synthesize skill"
    )
