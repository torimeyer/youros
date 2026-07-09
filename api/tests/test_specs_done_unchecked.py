"""Done-with-unchecked-criteria behavior (→2231 →2232 →2233 →2234 →2235).

The product rule: informs, never blocks. Marking a spec done while it
still has unchecked acceptance criteria always succeeds, and the
response carries the list of unchecked criteria as information so the
board and the person can see what was left unconfirmed. A companion
endpoint lets the review step tick individual criteria.
"""
from __future__ import annotations

import pytest


SPEC_WITH_UNCHECKED = (
    "---\n"
    "title: Half reviewed\n"
    "status: ready\n"
    "---\n"
    "\n"
    "## Problem\n"
    "Something worth building.\n"
    "\n"
    "## Acceptance criteria\n"
    "- [x] first thing works\n"
    "- [ ] second thing works\n"
    "- [ ] third thing works\n"
)

SPEC_ALL_CHECKED = (
    "---\n"
    "title: Fully reviewed\n"
    "status: ready\n"
    "---\n"
    "\n"
    "## Acceptance criteria\n"
    "- [x] first thing works\n"
    "- [x] second thing works\n"
)


def _write_spec(tmp_path, monkeypatch, name: str, content: str):
    (tmp_path / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)
    spec = tmp_path / "docs" / "spec" / name
    spec.write_text(content)
    return spec


@pytest.mark.asyncio
async def test_mark_done_with_unchecked_criteria_returns_unchecked_list(
    client, tmp_path, monkeypatch
):
    """→2231 →2235: marking done returns the unchecked criteria list."""
    _write_spec(tmp_path, monkeypatch, "half.md", SPEC_WITH_UNCHECKED)

    resp = await client.patch(
        "/api/specs/docs/spec/half.md/status", json={"status": "done"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "done"
    assert data["unchecked_criteria"] == [
        "second thing works",
        "third thing works",
    ]


@pytest.mark.asyncio
async def test_mark_done_never_blocks_status_is_applied(
    client, tmp_path, monkeypatch
):
    """→2234: unchecked criteria are information, not a refusal.

    The frontmatter status actually flips to done even though two
    criteria are still unchecked.
    """
    spec = _write_spec(tmp_path, monkeypatch, "half.md", SPEC_WITH_UNCHECKED)

    resp = await client.patch(
        "/api/specs/docs/spec/half.md/status", json={"status": "done"}
    )
    assert resp.status_code == 200
    assert "status: done" in spec.read_text()


@pytest.mark.asyncio
async def test_mark_done_all_checked_returns_empty_list(
    client, tmp_path, monkeypatch
):
    """→2232: no unchecked criteria means an empty list, the fully done case."""
    _write_spec(tmp_path, monkeypatch, "full.md", SPEC_ALL_CHECKED)

    resp = await client.patch(
        "/api/specs/docs/spec/full.md/status", json={"status": "complete"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["unchecked_criteria"] == []


@pytest.mark.asyncio
async def test_non_done_status_change_has_no_unchecked_list(
    client, tmp_path, monkeypatch
):
    """Only done/complete transitions carry the unchecked list."""
    _write_spec(tmp_path, monkeypatch, "half.md", SPEC_WITH_UNCHECKED)

    resp = await client.patch(
        "/api/specs/docs/spec/half.md/status", json={"status": "building"}
    )
    assert resp.status_code == 200
    assert "unchecked_criteria" not in resp.json()


@pytest.mark.asyncio
async def test_tick_criterion_checks_it_in_the_spec_body(
    client, tmp_path, monkeypatch
):
    """→2233: the review step can tick a criterion the person confirms."""
    spec = _write_spec(tmp_path, monkeypatch, "half.md", SPEC_WITH_UNCHECKED)

    resp = await client.patch(
        "/api/specs/docs/spec/half.md/criteria",
        json={"text": "second thing works", "checked": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["checked"] is True
    text = spec.read_text()
    assert "- [x] second thing works" in text
    # The rest stay exactly as they were: confirm one, leave the rest.
    assert "- [ ] third thing works" in text
    assert "- [x] first thing works" in text


@pytest.mark.asyncio
async def test_untick_criterion_unchecks_it(client, tmp_path, monkeypatch):
    """→2233: ticking is reversible; the person can also uncheck."""
    spec = _write_spec(tmp_path, monkeypatch, "half.md", SPEC_WITH_UNCHECKED)

    resp = await client.patch(
        "/api/specs/docs/spec/half.md/criteria",
        json={"text": "first thing works", "checked": False},
    )
    assert resp.status_code == 200
    assert "- [ ] first thing works" in spec.read_text()


@pytest.mark.asyncio
async def test_tick_unknown_criterion_returns_404(client, tmp_path, monkeypatch):
    _write_spec(tmp_path, monkeypatch, "half.md", SPEC_WITH_UNCHECKED)

    resp = await client.patch(
        "/api/specs/docs/spec/half.md/criteria",
        json={"text": "does not exist", "checked": True},
    )
    assert resp.status_code == 404
