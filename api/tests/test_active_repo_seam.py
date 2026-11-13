"""Tests for the Phase 2A active repository seam and repo discovery endpoints.

Covers:
1. Default fallback to PROJECT_ROOT when active_repo_path is unset/None.
2. OSTK_PROJECT_ROOT / OSTK_ROOT environment variable precedence.
3. POST /api/projects/active-repo validation (rejects missing or non-git dirs with 400).
4. POST /api/projects/active-repo acceptance of both standard .git directories and
   worktree .git files, updating settings, get_active_repo_path(), get_effective_root(),
   and OstkService.cwd, plus resetting back to None / PROJECT_ROOT.
5. GET /api/projects/repos local git repository discovery, deduplication, active flag,
   and fast in-memory .git/HEAD branch parsing.
6. Read-only file preview (_resolve_readable_path / GET /api/files/read) inside the
   selected active external repository.
7. Repo-scoped routing in specs.py and sessions.py when active_repo_path points to an
   external repository.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


def _clean_root_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("OSTK_PROJECT_ROOT", "OSTK_ROOT")
    }


def test_default_settings_and_fallback_to_project_root():
    """active_repo_path defaults to None and falls back to PROJECT_ROOT."""
    import config
    from models.schemas import Settings, SettingsResponse, SettingsUpdate
    from services import settings_store as ss_mod
    from services.ostk import get_effective_root

    repo_root = Path(__file__).resolve().parent.parent.parent
    default_json = json.loads((repo_root / "settings.default.json").read_text())
    assert "active_repo_path" in default_json
    assert default_json["active_repo_path"] is None

    assert Settings().active_repo_path is None
    assert SettingsResponse().active_repo_path is None
    upd = SettingsUpdate(active_repo_path="/tmp/demo-repo")
    assert upd.active_repo_path == "/tmp/demo-repo"

    loaded = ss_mod.load_settings()
    assert loaded["active_repo_path"] is None

    with patch.dict(os.environ, _clean_root_env(), clear=True):
        assert config.get_active_repo_path() == Path(config.PROJECT_ROOT)
        assert get_effective_root() == Path(config.PROJECT_ROOT)


def test_env_var_takes_priority_over_saved_active_repo_path(tmp_path):
    """OSTK_PROJECT_ROOT and OSTK_ROOT override saved active_repo_path."""
    import config
    from services import settings_store as ss_mod
    from services.ostk import get_effective_root

    ext_repo = tmp_path / "ext-repo"
    (ext_repo / ".git").mkdir(parents=True)
    ss_mod.save_settings({"active_repo_path": str(ext_repo)})

    with patch.dict(os.environ, _clean_root_env(), clear=True):
        assert config.get_active_repo_path() == ext_repo.resolve()
        assert get_effective_root() == ext_repo.resolve()

    worktree_override = tmp_path / "spawn-worktree"
    worktree_override.mkdir()
    env_with_override = {**_clean_root_env(), "OSTK_PROJECT_ROOT": str(worktree_override)}
    with patch.dict(os.environ, env_with_override, clear=True):
        assert config.get_active_repo_path() == worktree_override
        assert get_effective_root() == worktree_override


@pytest.mark.asyncio
async def test_post_active_repo_rejects_missing_or_non_git_dir(client, tmp_path):
    """POST /api/projects/active-repo returns 400 for missing, file, or non-git paths."""
    missing = tmp_path / "does-not-exist"
    resp_missing = await client.post(
        "/api/projects/active-repo",
        json={"path": str(missing)},
    )
    assert resp_missing.status_code == 400

    plain_dir = tmp_path / "plain-folder"
    plain_dir.mkdir()
    resp_plain = await client.post(
        "/api/projects/active-repo",
        json={"path": str(plain_dir)},
    )
    assert resp_plain.status_code == 400

    regular_file = tmp_path / "notes.txt"
    regular_file.write_text("hello")
    resp_file = await client.post(
        "/api/projects/active-repo",
        json={"path": str(regular_file)},
    )
    assert resp_file.status_code == 400


@pytest.mark.asyncio
async def test_post_active_repo_switches_effective_root_and_resets(client, tmp_path):
    """POST /api/projects/active-repo accepts .git dir and worktree .git file, and resets on None/empty."""
    import config
    from services import settings_store as ss_mod
    from services.ostk import get_effective_root, ostk

    ext_repo = tmp_path / "ext-repo"
    (ext_repo / ".git").mkdir(parents=True)
    (ext_repo / ".git" / "HEAD").write_text("ref: refs/heads/feat/seam-test\n")

    ss_mod.save_settings({"instance_name": "CustomOS"})

    with patch.dict(os.environ, _clean_root_env(), clear=True):
        resp = await client.post(
            "/api/projects/active-repo",
            json={"path": str(ext_repo)},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["active_repo_path"] == str(ext_repo.resolve())
        assert payload["name"] == "ext-repo"

        assert config.get_active_repo_path() == ext_repo.resolve()
        assert get_effective_root() == ext_repo.resolve()
        assert ostk.cwd == str(ext_repo.resolve())

        saved = ss_mod.load_settings()
        assert saved["active_repo_path"] == str(ext_repo.resolve())
        assert saved["instance_name"] == "CustomOS"

        # Verify git worktree (.git file pointing at gitdir) is also accepted.
        wt_gitdir = tmp_path / "gitdirs" / "wt-1"
        wt_gitdir.mkdir(parents=True)
        (wt_gitdir / "HEAD").write_text("ref: refs/heads/feat/worktree-branch\n")
        wt_repo = tmp_path / "wt-repo"
        wt_repo.mkdir()
        (wt_repo / ".git").write_text(f"gitdir: {wt_gitdir}\n")

        resp_wt = await client.post(
            "/api/projects/active-repo",
            json={"path": str(wt_repo)},
        )
        assert resp_wt.status_code == 200
        assert resp_wt.json()["active_repo_path"] == str(wt_repo.resolve())
        assert get_effective_root() == wt_repo.resolve()

        # Reset with None
        resp_reset = await client.post(
            "/api/projects/active-repo",
            json={"path": None},
        )
        assert resp_reset.status_code == 200
        assert resp_reset.json()["active_repo_path"] == str(Path(config.PROJECT_ROOT))
        assert ss_mod.load_settings()["active_repo_path"] is None
        assert get_effective_root() == Path(config.PROJECT_ROOT)

        # Activate again and reset with empty string ""
        await client.post("/api/projects/active-repo", json={"path": str(ext_repo)})
        resp_empty = await client.post("/api/projects/active-repo", json={"path": ""})
        assert resp_empty.status_code == 200
        assert ss_mod.load_settings()["active_repo_path"] is None
        assert get_effective_root() == Path(config.PROJECT_ROOT)


@pytest.mark.asyncio
async def test_get_projects_repos_discovers_repos_and_parses_branch(client, tmp_path, monkeypatch):
    """GET /api/projects/repos lists discovered git repos, active status, and .git/HEAD branch."""
    import routers.projects as projects_mod

    fake_home = tmp_path / "fake-home"
    fake_youros = tmp_path / "fake-youros"
    claude_dir = fake_home / "claude"
    sibling_repo = claude_dir / "sibling-service"
    (sibling_repo / ".git").mkdir(parents=True)
    (sibling_repo / ".git" / "HEAD").write_text("a1b2c3d4e5f60718293a4b5c6d7e8f9012345678\n")

    non_git_dir = claude_dir / "notes-only"
    non_git_dir.mkdir(parents=True)

    ext_repo = tmp_path / "outside-active-repo"
    (ext_repo / ".git").mkdir(parents=True)
    (ext_repo / ".git" / "HEAD").write_text("ref: refs/heads/feat/active-branch\n")

    torios_repo = tmp_path / "torios-main"
    (torios_repo / ".git").mkdir(parents=True)
    (torios_repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    monkeypatch.setattr(projects_mod, "TORIOS_DIR", torios_repo)
    monkeypatch.setattr(projects_mod, "youros_home", lambda: fake_youros)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    with patch.dict(os.environ, _clean_root_env(), clear=True):
        post_resp = await client.post(
            "/api/projects/active-repo",
            json={"path": str(ext_repo)},
        )
        assert post_resp.status_code == 200

        resp = await client.get("/api/projects/repos")
        assert resp.status_code == 200
        data = resp.json()

        assert data["active_repo_path"] == str(ext_repo.resolve())
        repos = data["repos"]
        paths = [r["path"] for r in repos]
        # Must be deduplicated
        assert len(paths) == len(set(paths))

        by_name = {r["name"]: r for r in repos}
        assert "outside-active-repo" in by_name
        assert by_name["outside-active-repo"]["is_active"] is True
        assert by_name["outside-active-repo"]["branch"] == "feat/active-branch"

        assert "torios-main" in by_name
        assert by_name["torios-main"]["is_active"] is False
        assert by_name["torios-main"]["branch"] == "main"

        assert "sibling-service" in by_name
        assert by_name["sibling-service"]["is_active"] is False
        assert by_name["sibling-service"]["branch"] == "a1b2c3d"

        assert "notes-only" not in by_name


@pytest.mark.asyncio
async def test_files_read_allows_active_external_repo(client, tmp_path, monkeypatch):
    """GET /api/files/read permits reading files inside the active external repository."""
    main_repo = tmp_path / "main-workspace"
    (main_repo / ".git").mkdir(parents=True)
    monkeypatch.setattr("config.PROJECT_ROOT", main_repo)
    monkeypatch.setattr("routers.projects.TORIOS_DIR", main_repo)

    ext_repo = tmp_path / "ext-readable"
    (ext_repo / ".git").mkdir(parents=True)
    readme = ext_repo / "README.md"
    readme.write_text("# External Repo\nHello world.\n")

    with patch.dict(os.environ, _clean_root_env(), clear=True):
        # Before activating, a sibling external path outside workspace/myos is 403.
        denied = await client.get("/api/files/read", params={"path": str(readme)})
        assert denied.status_code == 403

        activate = await client.post(
            "/api/projects/active-repo",
            json={"path": str(ext_repo)},
        )
        assert activate.status_code == 200

        allowed = await client.get("/api/files/read", params={"path": str(readme)})
        assert allowed.status_code == 200
        assert "Hello world." in allowed.json()["content"]


@pytest.mark.asyncio
async def test_specs_and_sessions_route_to_active_repo(client, tmp_path):
    """Switching active_repo_path routes repo-relative specs and .ostk sessions to that repo."""
    ext_repo = tmp_path / "ext-routed"
    (ext_repo / ".git").mkdir(parents=True)

    # 1. Seed a repo-relative spec in ext_repo
    spec_file = ext_repo / "docs" / "spec" / "ext-feature.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        "---\ntitle: External Feature\nstatus: spec\n---\nOld body content.\n",
        encoding="utf-8",
    )

    # 2. Seed an active ostk session in ext_repo/.ostk/sessions/
    sess_dir = ext_repo / ".ostk" / "sessions" / "ext-session-42"
    sess_dir.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    (sess_dir / "events.jsonl").write_text(
        json.dumps({"ts": now_iso, "kind": "tool_call", "data": {"tool": "read"}}) + "\n",
        encoding="utf-8",
    )

    with patch.dict(os.environ, _clean_root_env(), clear=True):
        activate = await client.post(
            "/api/projects/active-repo",
            json={"path": str(ext_repo)},
        )
        assert activate.status_code == 200

        # Spec patch routes to ext_repo/docs/spec/ext-feature.md
        patch_resp = await client.patch(
            "/api/specs/docs/spec/ext-feature.md/body",
            json={"body": "Updated body in external repo.\n"},
        )
        assert patch_resp.status_code == 200
        updated_text = spec_file.read_text(encoding="utf-8")
        assert "title: External Feature" in updated_text
        assert "Updated body in external repo." in updated_text

        # Sessions endpoint reads from ext_repo/.ostk/sessions/
        sess_resp = await client.get("/api/sessions/active")
        assert sess_resp.status_code == 200
        session_ids = [s["session_id"] for s in sess_resp.json()["sessions"]]
        assert "ext-session-42" in session_ids
