"""Tests for the career growth tracking router."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Unit tests for growth helpers
# ---------------------------------------------------------------------------


class TestGrowthHelpers:
    def test_load_empty(self, tmp_path):
        """Loading from a nonexistent file returns an empty list."""
        with patch("routers.growth.GROWTH_PATH", tmp_path / "nope.json"):
            from routers.growth import _load_skills
            skills = _load_skills()
        assert skills == []

    def test_save_and_load(self, tmp_path):
        """Round-trip save/load preserves skill data."""
        gp = tmp_path / "growth.json"
        with patch("routers.growth.MYOS_DIR", tmp_path), \
             patch("routers.growth.GROWTH_PATH", gp):
            from routers.growth import _save_skills, _load_skills
            data = [{"id": "1", "skill": "Python", "level": 4, "note": "", "category": "engineering", "created_at": "2026-01-01"}]
            _save_skills(data)
            loaded = _load_skills()
        assert len(loaded) == 1
        assert loaded[0]["skill"] == "Python"

    def test_load_corrupt_json(self, tmp_path):
        """Corrupt JSON returns empty list instead of crashing."""
        gp = tmp_path / "growth.json"
        gp.write_text("not json")
        with patch("routers.growth.GROWTH_PATH", gp):
            from routers.growth import _load_skills
            skills = _load_skills()
        assert skills == []


# ---------------------------------------------------------------------------
# Router integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_skills_empty(client, tmp_path):
    """GET /api/growth returns empty list when no skills tracked."""
    with patch("routers.growth.GROWTH_PATH", tmp_path / "nope.json"):
        resp = await client.get("/api/growth")

    assert resp.status_code == 200
    data = resp.json()
    assert data["skills"] == []
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_add_skill(client, tmp_path):
    """POST /api/growth creates a skill entry."""
    gp = tmp_path / "growth.json"
    with patch("routers.growth.MYOS_DIR", tmp_path), \
         patch("routers.growth.GROWTH_PATH", gp):
        resp = await client.post("/api/growth", json={
            "skill": "Python",
            "level": 4,
            "note": "Comfortable with async",
            "category": "engineering",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["skill"] == "Python"
    assert data["level"] == 4
    assert "id" in data


@pytest.mark.asyncio
async def test_add_skill_validates_level(client, tmp_path):
    """POST /api/growth rejects level outside 1-5."""
    gp = tmp_path / "growth.json"
    with patch("routers.growth.MYOS_DIR", tmp_path), \
         patch("routers.growth.GROWTH_PATH", gp):
        resp = await client.post("/api/growth", json={
            "skill": "Python",
            "level": 10,
        })
    assert resp.status_code == 422  # validation error


@pytest.mark.asyncio
async def test_add_and_list_skills(client, tmp_path):
    """POST then GET shows the skill in the list."""
    gp = tmp_path / "growth.json"
    with patch("routers.growth.MYOS_DIR", tmp_path), \
         patch("routers.growth.GROWTH_PATH", gp):
        await client.post("/api/growth", json={"skill": "TypeScript", "level": 3})
        resp = await client.get("/api/growth")

    assert resp.status_code == 200
    assert resp.json()["count"] == 1


@pytest.mark.asyncio
async def test_skill_summary(client, tmp_path):
    """GET /api/growth/summary groups skills by category."""
    gp = tmp_path / "growth.json"
    skills = [
        {"id": "1", "skill": "Python", "level": 4, "note": "", "category": "engineering", "created_at": "2026-01-01"},
        {"id": "2", "skill": "Python", "level": 5, "note": "mastered", "category": "engineering", "created_at": "2026-02-01"},
        {"id": "3", "skill": "Communication", "level": 3, "note": "", "category": "soft-skills", "created_at": "2026-01-01"},
    ]
    gp.write_text(json.dumps(skills))

    with patch("routers.growth.GROWTH_PATH", gp):
        resp = await client.get("/api/growth/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert "engineering" in data["categories"]
    assert "soft-skills" in data["categories"]
    # Python should show level 5 (highest)
    eng_skills = data["categories"]["engineering"]["skills"]
    python_entry = [s for s in eng_skills if s["skill"] == "Python"][0]
    assert python_entry["level"] == 5
    # Total unique skills = 2 (Python + Communication)
    assert data["total_skills"] == 2


@pytest.mark.asyncio
async def test_skill_summary_empty(client, tmp_path):
    """GET /api/growth/summary handles empty state."""
    with patch("routers.growth.GROWTH_PATH", tmp_path / "nope.json"):
        resp = await client.get("/api/growth/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["categories"] == {}
    assert data["total_skills"] == 0
