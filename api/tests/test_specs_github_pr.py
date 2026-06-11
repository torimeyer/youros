"""F1: spec gains an optional github_pr frontmatter field."""

import os
from pathlib import Path

import pytest

import config


def _write_spec(tmp: Path, name: str = "demo.md", pr: str | None = None) -> str:
    spec_dir = Path(config.PROJECT_ROOT) / "docs" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    fm = ["---", "title: Demo", "status: spec"]
    if pr is not None:
        fm.append(f"github_pr: {pr}")
    fm.append("---")
    body = "\n".join(fm) + "\n\n## Goal\n\n- [ ] do the thing\n"
    (spec_dir / name).write_text(body)
    return f"docs/spec/{name}"


@pytest.mark.asyncio
async def test_review_includes_github_pr_when_set(client, tmp_path):
    path = _write_spec(tmp_path, pr="acme/web#123")
    resp = await client.get(f"/api/specs/{path}/review")
    assert resp.status_code == 200
    assert resp.json()["github_pr"] == "acme/web#123"


@pytest.mark.asyncio
async def test_review_github_pr_empty_when_absent(client, tmp_path):
    path = _write_spec(tmp_path)
    resp = await client.get(f"/api/specs/{path}/review")
    assert resp.status_code == 200
    assert resp.json()["github_pr"] == ""


@pytest.mark.asyncio
async def test_patch_github_pr_sets_field(client, tmp_path):
    path = _write_spec(tmp_path)
    resp = await client.patch(f"/api/specs/{path}/github-pr", json={"github_pr": "acme/web#123"})
    assert resp.status_code == 200
    assert resp.json()["github_pr"] == "acme/web#123"
    review = await client.get(f"/api/specs/{path}/review")
    assert review.json()["github_pr"] == "acme/web#123"


@pytest.mark.asyncio
async def test_patch_github_pr_clears_field(client, tmp_path):
    path = _write_spec(tmp_path, pr="acme/web#123")
    resp = await client.patch(f"/api/specs/{path}/github-pr", json={"github_pr": ""})
    assert resp.status_code == 200
    review = await client.get(f"/api/specs/{path}/review")
    assert review.json()["github_pr"] == ""
