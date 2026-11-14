"""Tests for ostk tiered context-pages service, router endpoints, and handoff integration (->3114)."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import routers.ostk as ostk_router
from services import ostk_context_pages
from services.ostk_handoff import execute_runtime_handoff


class _FakeAsyncProc:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0, hang: bool = False):
        self._stdout = stdout.encode("utf-8")
        self._stderr = stderr.encode("utf-8")
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(30)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


class _FakeStreamSubprocess:
    """Minimal stream subprocess fake for Gemini/Claude handoff spawn tests."""

    def __init__(self, stdout_lines: list[bytes], return_code: int = 0, pid: int = 91001):
        self.pid = pid
        self.returncode: int | None = None
        self._return_code = return_code
        self.stdout = asyncio.StreamReader()
        for line in stdout_lines:
            self.stdout.feed_data(line)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()

    async def wait(self) -> int:
        self.returncode = self._return_code
        return self._return_code


def _init_git_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_dir, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    return head.stdout.strip()


def test_parse_context_pages_output_verified_ostk_777_format():
    envelope = json.dumps(
        {
            "ok": True,
            "verb": "context_pages",
            "output": (
                "  warm      1648 tok  s015-google-setup-inflight-survey @claude-code-p50248\n"
                "  hot       2524 tok  atlassian-widget-research @claude-code-p73643\n\n"
                "5 pages, 6242 tokens total (2524 hot)"
            ),
        }
    )
    parsed = ostk_context_pages.parse_context_pages_output(envelope)
    assert parsed == {
        "available": True,
        "pages": [
            {
                "name": "s015-google-setup-inflight-survey",
                "tier": "warm",
                "tokens": 1648,
                "owner": "claude-code-p50248",
                "pinned": False,
            },
            {
                "name": "atlassian-widget-research",
                "tier": "hot",
                "tokens": 2524,
                "owner": "claude-code-p73643",
                "pinned": False,
            },
        ],
        "total_pages": 5,
        "total_tokens": 6242,
        "hot_tokens": 2524,
    }


def test_parse_context_pages_output_empty_pinned_ownerless_and_future_json():
    for empty_raw in (
        "memory: no pages stored",
        "No context pages",
        "0 pages, 0 tokens total (0 hot)",
        "",
        json.dumps({"ok": True, "verb": "context_pages", "output": "memory: no pages stored"}),
    ):
        res = ostk_context_pages.parse_context_pages_output(empty_raw)
        assert res["available"] is True
        assert res["pages"] == []
        assert res["total_pages"] == 0
        assert res["total_tokens"] == 0
        assert res["hot_tokens"] == 0

    mixed_raw = (
        "  cold         4 tok  demo-page [pinned] @test-agent\n"
        "  cold         1 tok  no-owner-page [pinned]\n"
        "  * hot      512 tok  starred-hot-page\n"
        "  warm       300 tok  word-pinned-page pinned @agent-2\n"
        "  warm       120 tok  plain-ownerless-page\n"
    )
    mixed = ostk_context_pages.parse_context_pages_output(mixed_raw)
    assert mixed["available"] is True
    assert mixed["total_pages"] == 5
    assert mixed["total_tokens"] == 937
    assert mixed["hot_tokens"] == 512
    assert mixed["pages"] == [
        {"name": "demo-page", "tier": "cold", "tokens": 4, "owner": "test-agent", "pinned": True},
        {"name": "no-owner-page", "tier": "cold", "tokens": 1, "owner": None, "pinned": True},
        {"name": "starred-hot-page", "tier": "hot", "tokens": 512, "owner": None, "pinned": True},
        {"name": "word-pinned-page", "tier": "warm", "tokens": 300, "owner": "agent-2", "pinned": True},
        {"name": "plain-ownerless-page", "tier": "warm", "tokens": 120, "owner": None, "pinned": False},
    ]

    direct_json = json.dumps(
        {
            "ok": True,
            "pages": [
                {"name": "json-hot", "tier": "hot", "tokens": 900, "owner": "@gem-1", "pinned": True},
                {"name": "json-cold", "tier": "cold", "tokens": 100, "owner": None, "pinned": False},
            ],
        }
    )
    parsed_json = ostk_context_pages.parse_context_pages_output(direct_json)
    assert parsed_json == {
        "available": True,
        "pages": [
            {"name": "json-hot", "tier": "hot", "tokens": 900, "owner": "gem-1", "pinned": True},
            {"name": "json-cold", "tier": "cold", "tokens": 100, "owner": None, "pinned": False},
        ],
        "total_pages": 2,
        "total_tokens": 1000,
        "hot_tokens": 900,
    }


@pytest.mark.asyncio
async def test_list_context_pages_subprocess_and_graceful_degradation(tmp_path):
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = dict(kwargs)
        return _FakeAsyncProc(
            stdout=json.dumps(
                {
                    "ok": True,
                    "verb": "context_pages",
                    "output": "  hot  1200 tok  active-brief @claude-1\n\n1 pages, 1200 tokens total (1200 hot)",
                }
            ),
            returncode=0,
        )

    with patch("services.ostk_context_pages.asyncio.create_subprocess_exec", side_effect=fake_exec):
        res = await ostk_context_pages.list_context_pages(cwd=tmp_path)

    assert captured["args"] == ["ostk", "context-pages", "--json"]
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.DEVNULL
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert res["available"] is True
    assert len(res["pages"]) == 1
    assert res["pages"][0]["name"] == "active-brief"
    assert res["hot_tokens"] == 1200

    # Non-zero exit (no .ostk dir) degrades cleanly
    with patch(
        "services.ostk_context_pages.asyncio.create_subprocess_exec",
        new=AsyncMock(
            return_value=_FakeAsyncProc(
                stdout="",
                stderr="error: no .ostk/ directory found in any parent",
                returncode=1,
            )
        ),
    ):
        degraded = await ostk_context_pages.list_context_pages(cwd=tmp_path)
    assert degraded == {
        "available": False,
        "pages": [],
        "total_pages": 0,
        "total_tokens": 0,
        "hot_tokens": 0,
    }

    # Missing binary degrades cleanly
    with patch(
        "services.ostk_context_pages.asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError("ostk not found"),
    ):
        missing = await ostk_context_pages.list_context_pages(cwd=tmp_path)
    assert missing["available"] is False
    assert missing["pages"] == []

    # Hung subprocess times out and gets killed cleanly
    hung_proc = _FakeAsyncProc(hang=True)
    with (
        patch.object(ostk_context_pages, "_SUBPROCESS_TIMEOUT_SECONDS", 0.02),
        patch(
            "services.ostk_context_pages.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=hung_proc),
        ),
    ):
        timed_out = await ostk_context_pages.list_context_pages(cwd=tmp_path)
    assert hung_proc.killed is True
    assert timed_out["available"] is False
    assert timed_out["pages"] == []


@pytest.mark.asyncio
async def test_pin_restore_store_commands_and_name_validation(tmp_path):
    calls: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        calls.append(list(args))
        verb = args[1]
        return _FakeAsyncProc(
            stdout=json.dumps({"ok": True, "verb": verb.replace("-", "_"), "output": f"{verb} ok"}),
            returncode=0,
        )

    with patch("services.ostk_context_pages.asyncio.create_subprocess_exec", side_effect=fake_exec):
        pin_res = await ostk_context_pages.pin_context_page("arch-plan", unpin=False, cwd=tmp_path)
        unpin_res = await ostk_context_pages.pin_context_page("arch-plan", unpin=True, cwd=tmp_path)
        restore_res = await ostk_context_pages.restore_context_page("arch-plan", cwd=tmp_path)
        store_res = await ostk_context_pages.store_context_page(
            "arch-plan",
            "Tiered memory design",
            owner="saa-3114",
            cwd=tmp_path,
        )
        store_no_owner = await ostk_context_pages.store_context_page(
            "plain-note",
            "Body without owner",
            cwd=tmp_path,
        )

    assert pin_res["ok"] is True
    assert pin_res["pinned"] is True
    assert unpin_res["ok"] is True
    assert unpin_res["pinned"] is False
    assert restore_res["ok"] is True
    assert store_res["ok"] is True
    assert store_no_owner["ok"] is True

    assert calls == [
        ["ostk", "context-pin", "arch-plan", "--json"],
        ["ostk", "context-pin", "arch-plan", "--unpin", "--json"],
        ["ostk", "context-restore", "arch-plan", "--json"],
        ["ostk", "context-store", "arch-plan", "Tiered memory design", "--owner", "saa-3114", "--json"],
        ["ostk", "context-store", "plain-note", "Body without owner", "--json"],
    ]

    # Guard against empty names and option injection
    for bad_name in ("", "   ", "-v", "--unpin", "--help"):
        with pytest.raises(ValueError):
            await ostk_context_pages.pin_context_page(bad_name, cwd=tmp_path)
        with pytest.raises(ValueError):
            await ostk_context_pages.restore_context_page(bad_name, cwd=tmp_path)
        with pytest.raises(ValueError):
            await ostk_context_pages.store_context_page(bad_name, "body", cwd=tmp_path)


@pytest.mark.asyncio
async def test_ostk_router_context_pages_endpoints():
    app = FastAPI()
    app.include_router(ostk_router.router, prefix="/api")

    sample_list = {
        "available": True,
        "pages": [
            {"name": "hot-page", "tier": "hot", "tokens": 800, "owner": "agent-a", "pinned": True},
        ],
        "total_pages": 1,
        "total_tokens": 800,
        "hot_tokens": 800,
    }

    with (
        patch("routers.ostk.ostk_context_pages.list_context_pages", new=AsyncMock(return_value=sample_list)) as m_list,
        patch(
            "routers.ostk.ostk_context_pages.store_context_page",
            new=AsyncMock(return_value={"ok": True, "name": "new-page", "output": "stored"}),
        ) as m_store,
        patch(
            "routers.ostk.ostk_context_pages.pin_context_page",
            new=AsyncMock(side_effect=ostk_context_pages.pin_context_page),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r_get = await client.get("/api/ostk/context-pages")
            assert r_get.status_code == 200
            assert r_get.json() == sample_list
            m_list.assert_awaited_once()

            r_store = await client.post(
                "/api/ostk/context-pages",
                json={"name": "new-page", "content": "hello", "owner": "agent-a"},
            )
            assert r_store.status_code == 200
            assert r_store.json()["ok"] is True
            m_store.assert_awaited_once_with("new-page", "hello", owner="agent-a")

            # Flag injection name must return 400
            r_bad_pin = await client.post("/api/ostk/context-pages/-flag/pin")
            assert r_bad_pin.status_code == 400

    with (
        patch(
            "routers.ostk.ostk_context_pages.pin_context_page",
            new=AsyncMock(return_value={"ok": True, "name": "hot-page", "pinned": True, "unpin": False, "output": "pinned"}),
        ) as m_pin,
        patch(
            "routers.ostk.ostk_context_pages.restore_context_page",
            new=AsyncMock(return_value={"ok": True, "name": "hot-page", "tier": "hot", "output": "restored"}),
        ) as m_restore,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Optional body: works with empty body AND explicit {"unpin": true}
            r_pin_default = await client.post("/api/ostk/context-pages/hot-page/pin")
            assert r_pin_default.status_code == 200
            m_pin.assert_awaited_with("hot-page", unpin=False)

            r_unpin = await client.post("/api/ostk/context-pages/hot-page/pin", json={"unpin": True})
            assert r_unpin.status_code == 200
            m_pin.assert_awaited_with("hot-page", unpin=True)

            r_restore = await client.post("/api/ostk/context-pages/hot-page/restore")
            assert r_restore.status_code == 200
            m_restore.assert_awaited_once_with("hot-page")


@pytest.mark.asyncio
async def test_execute_runtime_handoff_promotes_pins_and_embeds_context_page_pointers(
    tmp_path,
    monkeypatch,
):
    import routers.agents as agents_mod

    isolated_home = tmp_path / "youros_home"
    isolated_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YOUROS_HOME", str(isolated_home))

    ext_repo = tmp_path / "handoff_ctx_repo"
    base_sha = _init_git_repo(ext_repo)

    saved_meta = dict(agents_mod.agent_metadata)
    saved_active = dict(agents_mod.active_agents)
    agents_mod.agent_metadata.clear()
    agents_mod.active_agents.clear()

    try:
        agents_mod.agent_metadata["origin-claude"] = {
            "status": "running",
            "runtime": "claude",
            "model": "claude-sonnet-4-5",
            "repo_path": str(ext_repo),
            "worktree_path": str(ext_repo),
            "branch_name": "main",
            "base_sha": base_sha,
            "task_id": "3114",
        }

        captured_gemini: dict = {}
        gem_proc = _FakeStreamSubprocess(
            stdout_lines=[
                (json.dumps({"type": "result", "status": "success"}) + "\n").encode("utf-8"),
            ],
            return_code=0,
            pid=92001,
        )

        async def fake_gem_exec(*args, **kwargs):
            captured_gemini["argv"] = list(args)
            captured_gemini["cwd"] = kwargs.get("cwd")
            return gem_proc

        real_run = subprocess.run

        def fake_ostk_run(cmd, *args, **kwargs):
            if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "ostk":
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='{"ok": true}', stderr="")
            return real_run(cmd, *args, **kwargs)

        pin_mock = AsyncMock(return_value={"ok": True, "pinned": True})
        restore_mock = AsyncMock(return_value={"ok": True, "tier": "hot"})

        with (
            patch("services.ostk_handoff.subprocess.run", side_effect=fake_ostk_run),
            patch("services.ostk_context_pages.pin_context_page", new=pin_mock),
            patch("services.ostk_context_pages.restore_context_page", new=restore_mock),
            patch("services.gemini_cli_provider._find_gemini_binary", return_value="/opt/homebrew/bin/gemini"),
            patch("services.gemini_cli_provider.asyncio.create_subprocess_exec", side_effect=fake_gem_exec),
        ):
            out = await execute_runtime_handoff(
                target_runtime="gemini",
                agent_name="origin-claude",
                target_model="gemini-3.1-pro",
                task_id="3114",
                transition_note="Continue implementation with research pages",
                context_pages=["atlassian-widget-research", "s015-google-setup-inflight-survey"],
            )

        assert out["ok"] is True
        assert out["context_pages"] == [
            "atlassian-widget-research",
            "s015-google-setup-inflight-survey",
        ]

        # Both pages were pinned and restored in the target worktree
        assert [c.args[0] for c in pin_mock.await_args_list] == [
            "atlassian-widget-research",
            "s015-google-setup-inflight-survey",
        ]
        assert [c.args[0] for c in restore_mock.await_args_list] == [
            "atlassian-widget-research",
            "s015-google-setup-inflight-survey",
        ]

        gem_argv = captured_gemini["argv"]
        composed_prompt = gem_argv[gem_argv.index("-p") + 1]
        assert "Pinned ostk context pages (load via ostk context-load <name>):" in composed_prompt
        assert "ostk context-load atlassian-widget-research" in composed_prompt
        assert "ostk context-load s015-google-setup-inflight-survey" in composed_prompt

        handoffs_file = isolated_home / "memory" / "handoffs.jsonl"
        records = [json.loads(line) for line in handoffs_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert records[-1]["context_pages"] == [
            "atlassian-widget-research",
            "s015-google-setup-inflight-survey",
        ]

        # Also verify HTTP route compatibility via /api/agents/{name}/handoff-runtime
        # when HandoffRuntimeModal supplies ostk context-load pointers in transition_note
        pin_mock.reset_mock()
        restore_mock.reset_mock()
        gem_proc_2 = _FakeStreamSubprocess(
            stdout_lines=[
                (json.dumps({"type": "result", "status": "success"}) + "\n").encode("utf-8"),
            ],
            return_code=0,
            pid=92002,
        )

        async def fake_gem_exec_2(*args, **kwargs):
            captured_gemini["argv2"] = list(args)
            return gem_proc_2

        app = FastAPI()
        app.include_router(agents_mod.router, prefix="/api")
        modal_note = (
            "Switching runtimes\n\n"
            "Pinned ostk context pages (load via ostk context-load <name>):\n"
            "- ostk context-load modal-selected-page"
        )
        with (
            patch("services.ostk_handoff.subprocess.run", side_effect=fake_ostk_run),
            patch("services.ostk_context_pages.pin_context_page", new=pin_mock),
            patch("services.ostk_context_pages.restore_context_page", new=restore_mock),
            patch("services.gemini_cli_provider._find_gemini_binary", return_value="/opt/homebrew/bin/gemini"),
            patch("services.gemini_cli_provider.asyncio.create_subprocess_exec", side_effect=fake_gem_exec_2),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r_http = await client.post(
                    "/api/agents/origin-claude/handoff-runtime",
                    json={
                        "target_runtime": "gemini",
                        "target_model": "gemini-3.1-pro",
                        "task_id": "3114",
                        "transition_note": modal_note,
                        "context_pages": ["modal-selected-page"],
                    },
                )
        assert r_http.status_code == 200, r_http.text
        assert r_http.json()["context_pages"] == ["modal-selected-page"]
        assert [c.args[0] for c in pin_mock.await_args_list] == ["modal-selected-page"]
        assert [c.args[0] for c in restore_mock.await_args_list] == ["modal-selected-page"]
        gem_argv2 = captured_gemini["argv2"]
        prompt2 = gem_argv2[gem_argv2.index("-p") + 1]
        assert prompt2.count("Pinned ostk context pages (load via ostk context-load <name>):") == 1
        assert "ostk context-load modal-selected-page" in prompt2
    finally:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(saved_meta)
        agents_mod.active_agents.clear()
        agents_mod.active_agents.update(saved_active)
