"""Phase 3B tests: Gemini subagent spawn via spawn_pipeline, native ostk handoff,
and cross-model spec critique + receipt gate (task ->3095).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from services.runtime_provider import (
    Feature,
    SpawnNotSupportedError,
    SpawnRequest,
    SpawnResult,
)


def _init_git_repo(repo_dir: Path) -> str:
    """Create a real temporary git repo with an initial commit on main."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "README.md").write_text("# Demo External Repo\n", encoding="utf-8")
    existing_svc = repo_dir / "api" / "services" / "existing_mod.py"
    existing_svc.parent.mkdir(parents=True, exist_ok=True)
    existing_svc.write_text("def helper() -> int:\n    return 42\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo_dir, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    return head.stdout.strip()


class FakeStreamReader:
    def __init__(self, lines: list[bytes], gate: asyncio.Event | None = None) -> None:
        self._lines = list(lines)
        self._gate = gate

    async def readline(self) -> bytes:
        if self._gate is not None:
            await self._gate.wait()
        if not self._lines:
            return b""
        return self._lines.pop(0)

    async def read(self, n: int = -1) -> bytes:
        if self._gate is not None:
            await self._gate.wait()
        if not self._lines:
            return b""
        joined = b"".join(self._lines)
        self._lines.clear()
        return joined


class FakeSubprocess:
    def __init__(
        self,
        stdout_lines: list[bytes],
        return_code: int = 0,
        stderr_data: bytes = b"",
        pid: int = 51500,
        finish_gate: asyncio.Event | None = None,
    ) -> None:
        self.pid = pid
        self.stdout = FakeStreamReader(stdout_lines, gate=finish_gate)
        self.stderr = FakeStreamReader([stderr_data] if stderr_data else [], gate=finish_gate)
        self._return_code = return_code
        self.returncode: int | None = None
        self._finish_gate = finish_gate

    async def wait(self) -> int:
        if self._finish_gate is not None:
            await self._finish_gate.wait()
        self.returncode = self._return_code
        return self._return_code

    async def communicate(self) -> tuple[bytes, bytes]:
        out = await self.stdout.read()
        err = await self.stderr.read()
        await self.wait()
        return out, err

    def kill(self) -> None:
        self._return_code = -9
        self.returncode = -9
        if self._finish_gate is not None:
            self._finish_gate.set()


def _jsonl(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode("utf-8")


@pytest.fixture(autouse=True)
def _isolated_youros_home(tmp_path, monkeypatch):
    yh = tmp_path / "youros_home"
    yh.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YOUROS_HOME", str(yh))
    import routers.agents as agents_mod

    saved_meta = dict(agents_mod.agent_metadata)
    saved_active = dict(agents_mod.active_agents)
    yield yh
    agents_mod.agent_metadata.clear()
    agents_mod.agent_metadata.update(saved_meta)
    agents_mod.active_agents.clear()
    agents_mod.active_agents.update(saved_active)


# ---------------------------------------------------------------------------
# 1. GeminiCliRuntimeProvider.spawn_subagent via spawn_pipeline
# ---------------------------------------------------------------------------


def test_gemini_provider_declares_spawn_and_worktree_features():
    from services.gemini_cli_provider import GeminiCliRuntimeProvider

    ge = GeminiCliRuntimeProvider()
    assert ge.features() == {
        Feature.STREAMING,
        Feature.SUBAGENTS,
        Feature.ISOLATION,
        Feature.WORKTREES,
    }
    assert Feature.MONITOR not in ge.features()
    assert Feature.PLAN_MODE not in ge.features()
    assert Feature.HOOKS not in ge.features()


@pytest.mark.asyncio
async def test_gemini_spawn_subagent_missing_binary_raises_plain_language_and_skips_claude_fn():
    from services.gemini_cli_provider import GeminiCliRuntimeProvider

    claude_calls: list[SpawnRequest] = []

    async def claude_only_spawner(req: SpawnRequest) -> SpawnResult:
        claude_calls.append(req)
        return SpawnResult(name=req.name)

    provider = GeminiCliRuntimeProvider(spawn_fn=claude_only_spawner)
    with patch("services.gemini_cli_provider._find_gemini_binary", return_value=None):
        with pytest.raises(SpawnNotSupportedError) as exc_info:
            await provider.spawn_subagent(
                SpawnRequest(name="gem-missing-bin", prompt="check status")
            )

    msg = str(exc_info.value)
    assert "Gemini" in msg
    assert "Settings" in msg
    assert claude_calls == []


@pytest.mark.asyncio
async def test_gemini_adhoc_spawn_provisions_youros_worktree_streams_transcript_and_completes(
    tmp_path,
):
    import routers.agents as agents_mod
    from services.event_bus import AGENT_COMPLETED, AGENT_SPAWNED, bus as event_bus
    from services.gemini_cli_provider import GeminiCliRuntimeProvider

    ext_repo = tmp_path / "ext_repo"
    initial_sha = _init_git_repo(ext_repo)

    claude_calls: list[SpawnRequest] = []

    async def claude_only_spawner(req: SpawnRequest) -> SpawnResult:
        claude_calls.append(req)
        return SpawnResult(name=req.name)

    gate = asyncio.Event()
    fake_proc = FakeSubprocess(
        stdout_lines=[
            _jsonl({"type": "init", "session_id": "gem-sub-1", "model": "gemini-3.1-pro-preview"}),
            _jsonl({"type": "message", "role": "assistant", "content": "Working in worktree", "delta": True}),
            _jsonl({"type": "result", "status": "success", "stats": {"input_tokens": 50, "output_tokens": 20}}),
        ],
        return_code=0,
        pid=64210,
        finish_gate=gate,
    )

    captured_exec: dict = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_exec["argv"] = list(args)
        captured_exec["cwd"] = kwargs.get("cwd")
        captured_exec["env"] = dict(kwargs.get("env") or {})
        return fake_proc

    bus_events = []
    provider = GeminiCliRuntimeProvider(spawn_fn=claude_only_spawner)
    async with event_bus.subscribe() as q:
        with (
            patch("services.gemini_cli_provider._find_gemini_binary", return_value="/opt/homebrew/bin/gemini"),
            patch("services.gemini_cli_provider.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec),
        ):
            res = await provider.spawn_subagent(
                SpawnRequest(
                    name="gem-adhoc-worker",
                    prompt="Implement helper endpoint and run tests",
                    model="gemini-3.1-pro",
                    isolation="worktree",
                    repo_path=str(ext_repo),
                )
            )

        assert claude_calls == [], "Gemini provider must NEVER invoke injected Claude spawn_fn"
        assert res.name == "gem-adhoc-worker"
        assert res.pid == 64210
        assert res.status == "running"
        assert res.repo_path == str(ext_repo.resolve())
        assert res.base_sha == initial_sha
        assert res.worktree_path is not None

        wt_path = Path(res.worktree_path)
        assert wt_path.exists()
        assert str(wt_path).startswith(str(ext_repo.resolve() / ".youros" / "worktrees" / "agent-"))

        # External repo git status must stay clean (.youros/ excluded via .git/info/exclude)
        st = subprocess.run(
            ["git", "-C", str(ext_repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert st.stdout.strip() == ""

        # Verify CLI invocation flags
        argv = captured_exec["argv"]
        assert argv[0] == "/opt/homebrew/bin/gemini"
        assert "-p" in argv
        assert "--output-format" in argv
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert "--skip-trust" in argv
        assert "--approval-mode" in argv
        assert argv[argv.index("--approval-mode") + 1] == "yolo"
        assert "-m" in argv
        assert argv[argv.index("-m") + 1] == "gemini-3.1-pro-preview"
        assert "-w" not in argv, "Must NEVER pass Gemini's un-reaped -w worktree flag"
        assert Path(captured_exec["cwd"]).resolve() == wt_path.resolve()

        # Verify short socket env
        env = captured_exec["env"]
        assert "YOUROS_SOCKET_DIR" in env
        assert len(env["YOUROS_SOCKET_DIR"]) < 60
        assert Path(env["OSTK_PROJECT_ROOT"]).resolve() == wt_path.resolve()

        # Verify running metadata row before exit
        meta = agents_mod.agent_metadata.get("gem-adhoc-worker")
        assert meta is not None
        assert meta["status"] == "running"
        assert meta["runtime"] == "gemini"
        assert meta["pid"] == 64210
        assert meta["repo_path"] == str(ext_repo.resolve())
        assert meta["worktree_path"] == str(wt_path)
        assert meta["branch_name"] == res.branch_name
        assert meta["base_sha"] == initial_sha

        # Release subprocess exit gate and let background watcher finish
        gate.set()
        for _ in range(50):
            if agents_mod.agent_metadata["gem-adhoc-worker"]["status"] == "completed":
                break
            await asyncio.sleep(0.02)

        assert agents_mod.agent_metadata["gem-adhoc-worker"]["status"] == "completed"
        assert res.transcript_path is not None
        tpath = Path(res.transcript_path)
        assert tpath.is_file()
        transcript_lines = [json.loads(line) for line in tpath.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert any(ev.get("type") == "message" and "Working in worktree" in ev.get("content", "") for ev in transcript_lines)

        while not q.empty():
            bus_events.append(q.get_nowait())

    event_types = {e.type for e in bus_events}
    assert AGENT_SPAWNED in event_types
    assert AGENT_COMPLETED in event_types


@pytest.mark.asyncio
async def test_gemini_spawn_marks_failed_on_nonzero_exit(tmp_path):
    import routers.agents as agents_mod
    from services.gemini_cli_provider import GeminiCliRuntimeProvider

    ext_repo = tmp_path / "ext_repo_fail"
    _init_git_repo(ext_repo)

    fake_proc = FakeSubprocess(
        stdout_lines=[_jsonl({"type": "error", "message": "boom"})],
        return_code=2,
        stderr_data=b"Fatal error",
        pid=64211,
    )

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/opt/homebrew/bin/gemini"),
        patch(
            "services.gemini_cli_provider.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ),
    ):
        await GeminiCliRuntimeProvider().spawn_subagent(
            name="gem-fail-worker",
            prompt="run failing task",
            isolation="none",
            repo_path=str(ext_repo),
        )
        for _ in range(50):
            if agents_mod.agent_metadata["gem-fail-worker"]["status"] == "failed":
                break
            await asyncio.sleep(0.02)

    assert agents_mod.agent_metadata["gem-fail-worker"]["status"] == "failed"


# ---------------------------------------------------------------------------
# 2. Native ostk handoff + ostk work activate & worktree reuse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_and_task_runtime_handoff_reuses_worktree_and_records_receipts(
    tmp_path,
    _isolated_youros_home,
):
    import routers.agents as agents_mod
    import routers.tasks as tasks_mod
    from services.spawn_pipeline import pin_decision, provision_agent_worktree

    ext_repo = tmp_path / "handoff_repo"
    base_sha = _init_git_repo(ext_repo)

    # Provision initial worktree for a Claude agent and make a real commit inside it
    lease = provision_agent_worktree(ext_repo, "claude-origin-agent")
    wt_path = lease.worktree_path
    (wt_path / "feature_part1.py").write_text("PART_ONE = True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(wt_path), "add", "feature_part1.py"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(wt_path), "commit", "-m", "feat: part 1 progress (→3095)"],
        check=True,
        capture_output=True,
    )
    prior_commit_sha = subprocess.run(
        ["git", "-C", str(wt_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert prior_commit_sha != base_sha

    # Write a failing pytest audit entry in the worktree's .ostk/audit.jsonl
    ostk_dir = wt_path / ".ostk"
    ostk_dir.mkdir(parents=True, exist_ok=True)
    (ostk_dir / "audit.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-11-13T20:10:00Z",
                "cmd": "pytest api/tests/test_part2.py",
                "exit_code": 1,
                "stderr": "AssertionError: part 2 missing",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # Pin an architectural decision for this repo
    pin_decision(
        repo_root=ext_repo,
        key="auth_strategy",
        value="oauth_first",
        reason="Preserve subscription quota before API key fallback",
    )

    # Register source Claude agent in agent_metadata
    agents_mod.agent_metadata["claude-origin-agent"] = {
        "status": "completed",
        "runtime": "claude",
        "model": "claude-sonnet-4-5",
        "repo_path": str(ext_repo),
        "worktree_path": str(wt_path),
        "branch_name": lease.branch_name,
        "worktree_branch": lease.branch_name,
        "base_sha": base_sha,
        "task_id": "→3095",
        "needle_id": "→3095",
        "prompt": "Build part 1 and part 2",
    }

    ostk_cli_calls: list[list[str]] = []
    real_subprocess_run = subprocess.run

    def intercept_ostk_subprocess(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "ostk":
            ostk_cli_calls.append(list(cmd))
            if len(cmd) >= 2 and cmd[1] == "handoff":
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=json.dumps({"ok": True, "target": cmd[2], "kernel_state": "synced"}),
                    stderr="",
                )
            if len(cmd) >= 3 and cmd[1] == "work" and cmd[2] == "activate":
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="Needle →3095 active | Sphere: runtime-handoff | Blockers: none",
                    stderr="",
                )
        return real_subprocess_run(cmd, *args, **kwargs)

    captured_gemini_exec: dict = {}
    gem_proc = FakeSubprocess(
        stdout_lines=[
            _jsonl({"type": "init", "session_id": "gem-ho-1", "model": "gemini-3.1-pro-preview"}),
            _jsonl({"type": "message", "role": "assistant", "content": "Picked up from Claude commit!", "delta": True}),
            _jsonl({"type": "result", "status": "success", "stats": {"input_tokens": 80, "output_tokens": 25}}),
        ],
        return_code=0,
        pid=71001,
    )

    async def fake_gem_exec(*args, **kwargs):
        captured_gemini_exec["argv"] = list(args)
        captured_gemini_exec["cwd"] = kwargs.get("cwd")
        return gem_proc

    app = FastAPI()
    app.include_router(agents_mod.router, prefix="/api")
    app.include_router(tasks_mod.router, prefix="/api")

    with (
        patch("services.ostk_handoff.subprocess.run", side_effect=intercept_ostk_subprocess),
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/opt/homebrew/bin/gemini"),
        patch("services.gemini_cli_provider.asyncio.create_subprocess_exec", side_effect=fake_gem_exec),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/claude-origin-agent/handoff-runtime",
                json={
                    "target_runtime": "gemini",
                    "target_model": "gemini-3.1-pro",
                    "task_id": "→3095",
                    "transition_note": "Finish part 2 and make pytest pass",
                },
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    new_agent = body["to_agent"]
    assert body["from_agent"] == "claude-origin-agent"
    assert body["target_runtime"] == "gemini"
    assert body["worktree_path"] == str(wt_path)
    assert body["branch_name"] == lease.branch_name
    assert body["base_sha"] == base_sha

    # Verify ostk handoff and ostk work activate were both invoked natively
    assert any(c[:2] == ["ostk", "handoff"] and "--transition" in c and "--json" in c for c in ostk_cli_calls)
    assert any(c[:3] == ["ostk", "work", "activate"] and "→3095" in c for c in ostk_cli_calls)

    # Verify prior commit was preserved untouched in the reused worktree
    head_after = subprocess.run(
        ["git", "-C", str(wt_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head_after == prior_commit_sha
    assert Path(captured_gemini_exec["cwd"]).resolve() == wt_path.resolve()

    # Verify composed handoff prompt carries git commit log, failing test receipt, pinned decision, and ostk briefing
    gem_argv = captured_gemini_exec["argv"]
    composed_prompt = gem_argv[gem_argv.index("-p") + 1]
    assert "part 1 progress" in composed_prompt
    assert "pytest api/tests/test_part2.py" in composed_prompt
    assert "auth_strategy" in composed_prompt
    assert "oauth_first" in composed_prompt
    assert "Sphere: runtime-handoff" in composed_prompt

    # Verify metadata linkage on both agents
    assert agents_mod.agent_metadata["claude-origin-agent"]["handed_off_to"] == new_agent
    new_meta = agents_mod.agent_metadata[new_agent]
    assert new_meta["handed_off_from"] == "claude-origin-agent"
    assert new_meta["source"] == "handoff"
    assert new_meta["runtime"] == "gemini"
    assert new_meta["worktree_path"] == str(wt_path)

    # Verify ~/.youros/memory/handoffs.jsonl record
    handoffs_file = _isolated_youros_home / "memory" / "handoffs.jsonl"
    assert handoffs_file.is_file()
    records = [json.loads(line) for line in handoffs_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["from_agent"] == "claude-origin-agent"
    assert records[0]["to_agent"] == new_agent
    assert records[0]["target_runtime"] == "gemini"
    assert records[0]["worktree_path"] == str(wt_path)
    assert records[0]["head_sha"] == prior_commit_sha

    # Now test reverse handoff via POST /api/tasks/{task_id}/handoff-runtime back to Claude!
    captured_claude_exec: dict = {}
    claude_proc = FakeSubprocess(
        stdout_lines=[_jsonl({"type": "result", "result": "Claude finished review"})],
        return_code=0,
        pid=71002,
    )

    async def fake_claude_exec(*args, **kwargs):
        captured_claude_exec["argv"] = list(args)
        captured_claude_exec["cwd"] = kwargs.get("cwd")
        return claude_proc

    with (
        patch("services.ostk_handoff.subprocess.run", side_effect=intercept_ostk_subprocess),
        patch("services.ostk_handoff._find_claude_binary", return_value="/usr/local/bin/claude"),
        patch("services.ostk_handoff.asyncio.create_subprocess_exec", side_effect=fake_claude_exec),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp2 = await client.post(
                "/api/tasks/3095/handoff-runtime",
                json={
                    "target_runtime": "claude",
                    "target_model": "claude-sonnet-4-5",
                    "transition_note": "Final verification pass on Claude",
                },
            )

    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    claude_agent = body2["to_agent"]
    assert body2["target_runtime"] == "claude"
    assert body2["worktree_path"] == str(wt_path)
    assert body2["branch_name"] == lease.branch_name
    assert Path(captured_claude_exec["cwd"]).resolve() == wt_path.resolve()
    assert captured_claude_exec["argv"][0] == "/usr/local/bin/claude"
    assert agents_mod.agent_metadata[claude_agent]["handed_off_from"] == new_agent
    assert agents_mod.agent_metadata[claude_agent]["source"] == "handoff"

    records2 = [json.loads(line) for line in handoffs_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records2) == 2
    assert records2[1]["target_runtime"] == "claude"


# ---------------------------------------------------------------------------
# 3. Cross-Model Spec Critique & Spec Receipt Gate (api/routers/spec_critique.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spec_cross_critique_catches_existing_file_listed_as_create(
    tmp_path,
    _isolated_youros_home,
):
    from routers import spec_critique

    ext_repo = tmp_path / "critique_repo"
    _init_git_repo(ext_repo)

    specs_dir = _isolated_youros_home / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    bad_spec = specs_dir / "dup-create-spec.md"
    bad_spec.write_text(
        "---\n"
        "title: Duplicate Create Plan\n"
        "status: spec\n"
        "tasks:\n"
        '  - "3095"\n'
        "---\n\n"
        "## Files\n"
        "- Create: `api/services/existing_mod.py`\n"
        "- Create: `api/services/brand_new_mod.py`\n\n"
        "## Acceptance criteria\n"
        "- [ ] Wire brand new module cleanly\n",
        encoding="utf-8",
    )

    good_spec = specs_dir / "clean-reuse-spec.md"
    good_spec.write_text(
        "---\n"
        "title: Clean Reuse Plan\n"
        "status: spec\n"
        "tasks:\n"
        '  - "3095"\n'
        "---\n\n"
        "## Files\n"
        "- Existing (reuse): `api/services/existing_mod.py`\n"
        "- Create: `api/services/brand_new_mod.py`\n\n"
        "## Acceptance criteria\n"
        "- [ ] Wire brand new module cleanly\n",
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(spec_critique.router, prefix="/api")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r_bad = await client.post(
            f"/api/specs/{bad_spec}/cross-critique",
            json={"reviewer_runtime": "gemini", "repo_path": str(ext_repo)},
        )
        assert r_bad.status_code == 200, r_bad.text
        d_bad = r_bad.json()
        assert d_bad["audit_clear"] is False
        assert "api/services/existing_mod.py" in d_bad["matched_existing_files"]
        assert "api/services/brand_new_mod.py" not in d_bad["matched_existing_files"]
        assert any("Existing (reuse)" in f.get("message", "") for f in d_bad["findings"])

        r_good = await client.post(
            f"/api/specs/{good_spec}/cross-critique",
            json={"reviewer_runtime": "gemini", "repo_path": str(ext_repo)},
        )
        assert r_good.status_code == 200, r_good.text
        d_good = r_good.json()
        assert d_good["audit_clear"] is True
        assert d_good["matched_existing_files"] == []


@pytest.mark.asyncio
async def test_spec_receipts_gate_blocks_unverified_and_passes_verified_tasks(
    tmp_path,
    _isolated_youros_home,
):
    from routers import spec_critique

    ext_repo = tmp_path / "receipt_repo"
    base_sha = _init_git_repo(ext_repo)

    specs_dir = _isolated_youros_home / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_file = specs_dir / "gated-feature.md"
    spec_file.write_text(
        "---\n"
        "title: Gated Feature\n"
        "status: spec\n"
        "tasks:\n"
        '  - "4101"\n'
        "---\n\n"
        "## Acceptance criteria\n"
        "- [ ] Must have cryptographic git + audit test receipt\n",
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(spec_critique.router, prefix="/api")
    transport = ASGITransport(app=app)

    # 1. Before any commit or test run -> all_verified MUST be False
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get(
            f"/api/specs/{spec_file}/receipts",
            params={"repo_path": str(ext_repo)},
        )
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["all_verified"] is False
    assert len(d1["task_receipts"]) == 1
    assert d1["task_receipts"][0]["task_id"] == "4101"
    assert d1["task_receipts"][0]["verified"] is False

    # 2. Add attributed git commit + exit-0 pytest entry in .ostk/audit.jsonl
    (ext_repo / "gated_impl.py").write_text("READY = True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(ext_repo), "add", "gated_impl.py"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(ext_repo), "commit", "-m", "feat: ship gated feature (→4101)"],
        check=True,
        capture_output=True,
    )
    ostk_dir = ext_repo / ".ostk"
    ostk_dir.mkdir(parents=True, exist_ok=True)
    (ostk_dir / "audit.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-11-13T20:20:00Z",
                "cmd": "pytest api/tests/test_gated.py",
                "exit_code": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r2 = await client.get(
            f"/api/specs/{spec_file}/receipts",
            params={"repo_path": str(ext_repo), "base_sha": base_sha},
        )
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["all_verified"] is True
    assert len(d2["task_receipts"]) == 1
    assert d2["task_receipts"][0]["verified"] is True
    assert "gated_impl.py" in d2["task_receipts"][0]["changed_files"]


@pytest.mark.asyncio
async def test_agents_spawn_endpoint_adhoc_gemini_never_400s(tmp_path, monkeypatch):
    import routers.agents as agents_mod

    ext_repo = tmp_path / "adhoc_ep_repo"
    _init_git_repo(ext_repo)
    monkeypatch.setenv("YOUROS_RUNTIME", "gemini")
    monkeypatch.setenv("OSTK_PROJECT_ROOT", str(ext_repo))

    fake_proc = FakeSubprocess(
        stdout_lines=[_jsonl({"type": "result", "status": "success", "stats": {"input_tokens": 5, "output_tokens": 5}})],
        return_code=0,
        pid=88101,
    )
    app = FastAPI()
    app.include_router(agents_mod.router, prefix="/api")

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/opt/homebrew/bin/gemini"),
        patch(
            "services.gemini_cli_provider.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": "ep-adhoc-gemini",
                    "prompt": "Summarize repository structure",
                    "model": "gemini-3.1-pro",
                    "isolation": "none",
                },
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "ep-adhoc-gemini"
    assert data["runtime"] == "gemini"


@pytest.mark.asyncio
async def test_runtime_handoff_degrades_cleanly_when_external_repo_lacks_ostk_dir(tmp_path):
    from services.ostk_handoff import execute_runtime_handoff

    ext_repo = tmp_path / "no_ostk_repo"
    _init_git_repo(ext_repo)

    real_run = subprocess.run

    def failing_ostk_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "ostk":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="error: not an ostk workspace",
            )
        return real_run(cmd, *args, **kwargs)

    fake_proc = FakeSubprocess(
        stdout_lines=[_jsonl({"type": "result", "status": "success"})],
        return_code=0,
        pid=88102,
    )

    with (
        patch("services.ostk_handoff.subprocess.run", side_effect=failing_ostk_run),
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/opt/homebrew/bin/gemini"),
        patch(
            "services.gemini_cli_provider.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ),
    ):
        out = await execute_runtime_handoff(
            target_runtime="gemini",
            task_id="9999",
            repo_path=str(ext_repo),
            transition_note="Start fresh in external repo without .ostk",
        )

    assert out["ok"] is True
    assert out["ostk_handoff"]["invoked"] is True
    assert out["ostk_handoff"]["ok"] is False
    assert Path(out["worktree_path"]).exists()


@pytest.mark.asyncio
async def test_spec_receipts_empty_tasks_is_not_verified(tmp_path, _isolated_youros_home):
    from routers import spec_critique

    ext_repo = tmp_path / "empty_spec_repo"
    _init_git_repo(ext_repo)

    specs_dir = _isolated_youros_home / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    empty_spec = specs_dir / "unlinked-spec.md"
    empty_spec.write_text(
        "---\ntitle: Unlinked Spec\nstatus: spec\n---\n\n## Acceptance criteria\n- [ ] Build it\n",
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(spec_critique.router, prefix="/api")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/api/specs/{empty_spec}/receipts", params={"repo_path": str(ext_repo)})

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["all_verified"] is False
    assert data["linked_task_count"] == 0
    assert data["missing_tasks_reason"]
