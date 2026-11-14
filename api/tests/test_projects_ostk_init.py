"""Tests for ostk repository readiness metadata and POST /api/projects/ostk-init (->3113)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


def _clean_root_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("OSTK_PROJECT_ROOT", "OSTK_ROOT")
    }


def test_ostk_repo_status_helper(tmp_path: Path):
    """_ostk_repo_status reports .ostk directory and ostk.sock readiness flags."""
    from routers.projects import _ostk_repo_status

    bare_repo = tmp_path / "bare-repo"
    (bare_repo / ".git").mkdir(parents=True)
    assert _ostk_repo_status(bare_repo) == {
        "ostk_initialized": False,
        "ostk_socket_live": False,
    }

    # A plain file named .ostk is not an initialized ostk directory.
    file_marker_repo = tmp_path / "file-marker-repo"
    (file_marker_repo / ".git").mkdir(parents=True)
    (file_marker_repo / ".ostk").write_text("not a dir")
    assert _ostk_repo_status(file_marker_repo) == {
        "ostk_initialized": False,
        "ostk_socket_live": False,
    }

    init_repo = tmp_path / "init-repo"
    (init_repo / ".git").mkdir(parents=True)
    (init_repo / ".ostk").mkdir()
    assert _ostk_repo_status(init_repo) == {
        "ostk_initialized": True,
        "ostk_socket_live": False,
    }

    (init_repo / ".ostk" / "ostk.sock").write_text("")
    assert _ostk_repo_status(init_repo) == {
        "ostk_initialized": True,
        "ostk_socket_live": True,
    }


@pytest.mark.asyncio
async def test_get_projects_repos_includes_ostk_readiness_metadata(client, tmp_path: Path, monkeypatch):
    """GET /api/projects/repos includes ostk_initialized and ostk_socket_live on every repo."""
    import routers.projects as projects_mod

    fake_home = tmp_path / "fake-home"
    fake_youros = tmp_path / "fake-youros"
    claude_dir = fake_home / "claude"

    live_repo = claude_dir / "live-service"
    (live_repo / ".git").mkdir(parents=True)
    (live_repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (live_repo / ".ostk").mkdir(parents=True)
    (live_repo / ".ostk" / "ostk.sock").write_text("")

    init_only_repo = claude_dir / "init-only-service"
    (init_only_repo / ".git").mkdir(parents=True)
    (init_only_repo / ".git" / "HEAD").write_text("ref: refs/heads/develop\n")
    (init_only_repo / ".ostk").mkdir(parents=True)

    uninit_repo = claude_dir / "raw-git-service"
    (uninit_repo / ".git").mkdir(parents=True)
    (uninit_repo / ".git" / "HEAD").write_text("ref: refs/heads/feat/new\n")

    torios_repo = tmp_path / "torios-main"
    (torios_repo / ".git").mkdir(parents=True)
    (torios_repo / ".ostk").mkdir(parents=True)

    monkeypatch.setattr(projects_mod, "TORIOS_DIR", torios_repo)
    monkeypatch.setattr(projects_mod, "youros_home", lambda: fake_youros)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    with patch.dict(os.environ, _clean_root_env(), clear=True):
        resp = await client.get("/api/projects/repos")
        assert resp.status_code == 200
        by_name = {r["name"]: r for r in resp.json()["repos"]}

        assert by_name["live-service"]["ostk_initialized"] is True
        assert by_name["live-service"]["ostk_socket_live"] is True

        assert by_name["init-only-service"]["ostk_initialized"] is True
        assert by_name["init-only-service"]["ostk_socket_live"] is False

        assert by_name["raw-git-service"]["ostk_initialized"] is False
        assert by_name["raw-git-service"]["ostk_socket_live"] is False


@pytest.mark.asyncio
async def test_get_and_post_active_repo_include_ostk_readiness_metadata(client, tmp_path: Path):
    """GET and POST /api/projects/active-repo include ostk_initialized and ostk_socket_live."""
    ext_repo = tmp_path / "ext-uninit-repo"
    (ext_repo / ".git").mkdir(parents=True)
    (ext_repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    with patch.dict(os.environ, _clean_root_env(), clear=True):
        post_resp = await client.post(
            "/api/projects/active-repo",
            json={"path": str(ext_repo)},
        )
        assert post_resp.status_code == 200
        post_data = post_resp.json()
        assert post_data["active_repo_path"] == str(ext_repo.resolve())
        assert post_data["name"] == "ext-uninit-repo"
        assert post_data["ostk_initialized"] is False
        assert post_data["ostk_socket_live"] is False

        get_resp = await client.get("/api/projects/active-repo")
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data["active_repo_path"] == str(ext_repo.resolve())
        assert get_data["name"] == "ext-uninit-repo"
        assert get_data["ostk_initialized"] is False
        assert get_data["ostk_socket_live"] is False

        # Simulate ostk init + live daemon socket
        (ext_repo / ".ostk").mkdir()
        (ext_repo / ".ostk" / "ostk.sock").write_text("")

        get_live = await client.get("/api/projects/active-repo")
        assert get_live.status_code == 200
        assert get_live.json()["ostk_initialized"] is True
        assert get_live.json()["ostk_socket_live"] is True

        post_live = await client.post(
            "/api/projects/active-repo",
            json={"path": str(ext_repo)},
        )
        assert post_live.status_code == 200
        assert post_live.json()["ostk_initialized"] is True
        assert post_live.json()["ostk_socket_live"] is True

        # Reset back to PROJECT_ROOT
        reset_resp = await client.post("/api/projects/active-repo", json={"path": None})
        assert reset_resp.status_code == 200
        reset_data = reset_resp.json()
        assert isinstance(reset_data["ostk_initialized"], bool)
        assert isinstance(reset_data["ostk_socket_live"], bool)


@pytest.mark.asyncio
async def test_post_ostk_init_rejects_invalid_or_non_git_paths(client, tmp_path: Path):
    """POST /api/projects/ostk-init rejects missing paths, files, and non-git directories."""
    missing = tmp_path / "no-such-repo"
    resp_missing = await client.post("/api/projects/ostk-init", json={"path": str(missing)})
    assert resp_missing.status_code in (400, 404)

    plain_dir = tmp_path / "plain-dir"
    plain_dir.mkdir()
    resp_plain = await client.post("/api/projects/ostk-init", json={"path": str(plain_dir)})
    assert resp_plain.status_code == 400

    regular_file = tmp_path / "notes.txt"
    regular_file.write_text("hello")
    resp_file = await client.post("/api/projects/ostk-init", json={"path": str(regular_file)})
    assert resp_file.status_code == 400


@pytest.mark.asyncio
async def test_post_ostk_init_runs_async_subprocess_with_optional_import_flag(client, tmp_path: Path):
    """POST /api/projects/ostk-init runs ostk init asynchronously and supports import_existing."""
    target_repo = tmp_path / "target-repo"
    (target_repo / ".git").mkdir(parents=True)

    captured_calls: list[dict] = []

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            (target_repo / ".ostk").mkdir(exist_ok=True)
            return b"initialized ostk project\n", b""

        def kill(self):
            pass

        async def wait(self):
            return 0

    async def _fake_exec(*cmd, **kwargs):
        captured_calls.append({"cmd": cmd, "kwargs": kwargs})
        return _FakeProc()

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        resp = await client.post(
            "/api/projects/ostk-init",
            json={"path": str(target_repo), "import_existing": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "initialized": True,
            "path": str(target_repo.resolve()),
            "ostk_initialized": True,
            "ostk_socket_live": False,
            "output": "initialized ostk project",
        }
        assert len(captured_calls) == 1
        assert captured_calls[0]["cmd"] == ("ostk", "init")
        assert captured_calls[0]["kwargs"]["cwd"] == str(target_repo.resolve())
        assert captured_calls[0]["kwargs"]["stdin"] == asyncio.subprocess.DEVNULL

        resp_import = await client.post(
            "/api/projects/ostk-init",
            json={"path": str(target_repo), "import_existing": True},
        )
        assert resp_import.status_code == 200
        assert captured_calls[1]["cmd"] == ("ostk", "init", "--import")


@pytest.mark.asyncio
async def test_post_ostk_init_defaults_to_effective_root_when_path_omitted(client, tmp_path: Path):
    """POST /api/projects/ostk-init defaults path to get_effective_root() when omitted or None."""
    active_repo = tmp_path / "effective-root-repo"
    (active_repo / ".git").mkdir(parents=True)

    captured_cwds: list[str] = []

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            (active_repo / ".ostk").mkdir(exist_ok=True)
            return b"bootstrapped effective root\n", b""

    async def _fake_exec(*cmd, **kwargs):
        captured_cwds.append(kwargs["cwd"])
        return _FakeProc()

    with patch.dict(os.environ, _clean_root_env(), clear=True):
        activate = await client.post(
            "/api/projects/active-repo",
            json={"path": str(active_repo)},
        )
        assert activate.status_code == 200

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            resp = await client.post("/api/projects/ostk-init", json={})
            assert resp.status_code == 200
            data = resp.json()
            assert data["initialized"] is True
            assert data["path"] == str(active_repo.resolve())
            assert data["ostk_initialized"] is True
            assert captured_cwds == [str(active_repo.resolve())]


@pytest.mark.asyncio
async def test_post_ostk_init_returns_503_when_ostk_binary_missing(client, tmp_path: Path):
    """POST /api/projects/ostk-init returns 503 if ostk is not installed."""
    repo = tmp_path / "repo-no-binary"
    (repo / ".git").mkdir(parents=True)

    async def _raise_not_found(*cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "ostk")

    with patch("asyncio.create_subprocess_exec", side_effect=_raise_not_found):
        resp = await client.post("/api/projects/ostk-init", json={"path": str(repo)})
        assert resp.status_code == 503
        assert "ostk" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_post_ostk_init_returns_error_with_stderr_on_failure(client, tmp_path: Path):
    """POST /api/projects/ostk-init surfaces stderr summary when ostk init exits non-zero."""
    repo = tmp_path / "repo-init-fail"
    (repo / ".git").mkdir(parents=True)

    class _FailingProc:
        returncode = 1

        async def communicate(self):
            return b"", b"error: unable to write .primefile: permission denied\n"

    async def _fake_exec(*cmd, **kwargs):
        return _FailingProc()

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        resp = await client.post("/api/projects/ostk-init", json={"path": str(repo)})
        assert resp.status_code in (422, 500)
        assert "permission denied" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_post_ostk_init_handles_timeout_and_worktree_git_file(client, tmp_path: Path):
    """POST /api/projects/ostk-init accepts worktree .git files and kills hung subprocesses on timeout."""
    wt_gitdir = tmp_path / "gitdirs" / "wt-init"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "HEAD").write_text("ref: refs/heads/feat/wt\n")
    wt_repo = tmp_path / "wt-target"
    wt_repo.mkdir()
    (wt_repo / ".git").write_text(f"gitdir: {wt_gitdir}\n")

    killed = {"called": False}

    class _HungProc:
        returncode = None

        async def communicate(self):
            raise asyncio.TimeoutError()

        def kill(self):
            killed["called"] = True

        async def wait(self):
            return -9

    async def _hung_exec(*cmd, **kwargs):
        return _HungProc()

    with patch("asyncio.create_subprocess_exec", side_effect=_hung_exec):
        resp = await client.post("/api/projects/ostk-init", json={"path": str(wt_repo)})
        assert resp.status_code == 500
        assert "timed out" in resp.json()["detail"].lower()
        assert killed["called"] is True


@pytest.mark.skipif(shutil.which("ostk") is None, reason="ostk binary not installed")
@pytest.mark.asyncio
async def test_post_ostk_init_real_cli_creates_dot_ostk(client, tmp_path: Path):
    """Integration check against real ostk init on a fresh temporary git repository."""
    real_repo = tmp_path / "real-ostk-init-repo"
    real_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=real_repo, check=True)

    assert not (real_repo / ".ostk").exists()

    resp = await client.post(
        "/api/projects/ostk-init",
        json={"path": str(real_repo)},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["initialized"] is True
    assert payload["path"] == str(real_repo.resolve())
    assert payload["ostk_initialized"] is True
    assert (real_repo / ".ostk").is_dir()
