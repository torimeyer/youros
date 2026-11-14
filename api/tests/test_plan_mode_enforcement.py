"""Tests for engine-level read-only plan mode and Gemini CLI 0.57.0 admin policy tier
enforcement across spec critiques, read-only subagent spawns, and read-only chat streams (task ->3120).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

import services.gemini_cli_provider as gcp
from services.runtime_provider import SpawnRequest

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID_GEMINI_057_RULE_KEYS: frozenset[str] = frozenset({
    "toolName",
    "subagent",
    "mcpName",
    "argsPattern",
    "commandPrefix",
    "commandRegex",
    "decision",
    "priority",
    "modes",
    "interactive",
    "toolAnnotations",
    "allowRedirection",
    "denyMessage",
})

VALID_GEMINI_057_DECISIONS: frozenset[str] = frozenset({"allow", "deny", "ask_user"})
VALID_GEMINI_057_MODES: frozenset[str] = frozenset({"default", "autoEdit", "yolo", "plan"})


def _init_git_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "README.md").write_text("# Test Repo\n", encoding="utf-8")
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


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)


class _FakeReader:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)

    async def read(self, n: int = -1) -> bytes:
        if not self._lines:
            return b""
        joined = b"".join(self._lines)
        self._lines.clear()
        return joined


class _FakeProcess:
    def __init__(
        self,
        stdout_lines: list[bytes],
        return_code: int = 0,
        stderr_data: bytes = b"",
        pid: int = 91234,
    ) -> None:
        self.pid = pid
        self.stdout = _FakeReader(stdout_lines)
        self.stderr = _FakeReader([stderr_data] if stderr_data else [])
        self.returncode = return_code

    async def wait(self) -> int:
        return self.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        out = await self.stdout.read()
        err = await self.stderr.read()
        return out, err

    def kill(self) -> None:
        self.returncode = -9


def _jsonl(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode("utf-8")


@pytest.fixture(autouse=True)
def _isolated_youros_state(tmp_path, monkeypatch):
    yh = tmp_path / "youros_home"
    yh.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YOUROS_HOME", str(yh))
    if hasattr(gcp, "_gemini_tab_sessions"):
        gcp._gemini_tab_sessions.clear()
    import routers.agents as agents_mod

    saved_meta = dict(agents_mod.agent_metadata)
    saved_active = dict(agents_mod.active_agents)
    yield yh
    agents_mod.agent_metadata.clear()
    agents_mod.agent_metadata.update(saved_meta)
    agents_mod.active_agents.clear()
    agents_mod.active_agents.update(saved_active)


def _normalize_tool_names(rule: dict) -> list[str]:
    raw = rule.get("toolName")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return list(raw)
    return []


# ---------------------------------------------------------------------------
# 1. Gemini 0.57.0 Admin Policy TOML Schema & Rule Verification
# ---------------------------------------------------------------------------


def test_readonly_critique_policy_toml_schema_and_rules():
    policy_path = gcp.get_readonly_policy_path()
    assert policy_path == REPO_ROOT / ".gemini" / "policies" / "readonly-critique.toml"
    assert policy_path.is_file(), f"Expected policy file at {policy_path}"

    raw_text = policy_path.read_text(encoding="utf-8")
    assert "\u2014" not in raw_text, "Policy file must contain zero em-dashes"

    parsed = tomllib.loads(raw_text)
    assert set(parsed.keys()).issubset({"rule", "safety_checker"})
    rules = parsed.get("rule")
    assert isinstance(rules, list) and len(rules) >= 4

    builtin_denied: set[str] = set()
    builtin_allowed: set[str] = set()
    ostk_denied: set[str] = set()
    ostk_allowed: set[str] = set()

    for idx, rule in enumerate(rules):
        assert isinstance(rule, dict), f"Rule #{idx} must be a table"
        unknown_keys = set(rule.keys()) - VALID_GEMINI_057_RULE_KEYS
        assert not unknown_keys, f"Rule #{idx} has invalid Gemini 0.57.0 keys: {unknown_keys}"

        decision = rule.get("decision")
        assert decision in VALID_GEMINI_057_DECISIONS

        priority = rule.get("priority")
        assert isinstance(priority, int) and not isinstance(priority, bool)
        assert priority == 100, f"Rule #{idx} must use priority = 100 (admin tier 5.100 > 4.2)"

        # Admin tier transformation: 5 + priority/1000 = 5.100 > 4.2 (trusted MCP server)
        effective_priority = 5 + (priority / 1000.0)
        assert effective_priority > 4.2

        if "modes" in rule:
            assert isinstance(rule["modes"], list)
            assert set(rule["modes"]).issubset(VALID_GEMINI_057_MODES)

        tool_names = _normalize_tool_names(rule)
        assert tool_names, f"Rule #{idx} must specify non-empty toolName"
        for tname in tool_names:
            assert isinstance(tname, str) and tname.strip() != ""
            assert "__" not in tname, (
                f"Rule #{idx} toolName {tname!r} uses deprecated '__' syntax rejected by Gemini 0.57.0"
            )

        mcp_name = rule.get("mcpName")
        if mcp_name is not None:
            assert isinstance(mcp_name, str) and mcp_name.strip() != ""

        if decision == "deny":
            assert isinstance(rule.get("denyMessage"), str) and rule["denyMessage"].strip()

        if mcp_name == "ostk":
            if decision == "deny":
                ostk_denied.update(tool_names)
            elif decision == "allow":
                ostk_allowed.update(tool_names)
        elif mcp_name is None:
            for tname in tool_names:
                if tname.startswith("mcp_ostk_"):
                    short = tname[len("mcp_ostk_") :]
                    if decision == "deny":
                        ostk_denied.add(short)
                    elif decision == "allow":
                        ostk_allowed.add(short)
                else:
                    if decision == "deny":
                        builtin_denied.add(tname)
                    elif decision == "allow":
                        builtin_allowed.add(tname)

    assert {
        "write_file",
        "replace",
        "run_shell_command",
        "save_memory",
        "exit_plan_mode",
    }.issubset(builtin_denied)

    assert {
        "fs_ops",
        "bash",
        "spawn",
        "interact",
        "edit",
        "fs_write",
    }.issubset(ostk_denied)

    assert {
        "read_file",
        "grep_search",
        "glob",
        "list_directory",
    }.issubset(builtin_allowed)

    assert {"read", "search"}.issubset(ostk_allowed)
    assert builtin_denied.isdisjoint(builtin_allowed)
    assert ostk_denied.isdisjoint(ostk_allowed)


def test_build_gemini_readonly_args_returns_plan_and_admin_policy():
    args = gcp.build_gemini_readonly_args()
    assert args == [
        "--approval-mode",
        "plan",
        "--admin-policy",
        str(gcp.get_readonly_policy_path()),
    ]


def test_readonly_critique_policy_evaluates_at_admin_tier_5_1_in_real_gemini_057_engine():
    """When @google/gemini-cli 0.57.0 bundle + node are installed locally, verify
    the real PolicyEngine loads readonly-critique.toml at priority 5.1 with zero warnings.
    """
    gemini_bin = gcp._find_gemini_binary()
    if not gemini_bin:
        pytest.skip("gemini binary not installed on this runner")
    bundle_dir = Path(gemini_bin).resolve().parent.parent / "bundle"
    if not bundle_dir.is_dir():
        bundle_dir = (
            Path(gemini_bin).resolve().parent.parent
            / "lib"
            / "node_modules"
            / "@google"
            / "gemini-cli"
            / "bundle"
        )
    core_candidates = sorted(bundle_dir.glob("core-*.js")) if bundle_dir.is_dir() else []
    policies_dir = bundle_dir / "policies"
    if not core_candidates or not policies_dir.is_dir():
        pytest.skip("gemini-cli 0.57.0 bundle core-*.js not present")

    node_bin = Path(gemini_bin).resolve().parent / "node"
    node_exe = str(node_bin) if node_bin.is_file() else "node"

    script = (
        f"import * as core from {json.dumps(str(core_candidates[0]))};\n"
        f"const cfg = await core.createPolicyEngineConfig("
        f"{{ adminPolicyPaths: [{json.dumps(str(gcp.get_readonly_policy_path()))}], "
        f"mcpServers: {{ ostk: {{ command: 'ostk', trust: true }} }}, "
        f"mcp: {{ allowed: ['ostk'] }} }}, "
        f"core.ApprovalMode.PLAN, {json.dumps(str(policies_dir))}, false);\n"
        "const engine = new core.PolicyEngine(cfg);\n"
        "const checks = [\n"
        "  [{ name: 'write_file', args: { file_path: 'plan.md', content: 'x' } }, undefined, 'deny'],\n"
        "  [{ name: 'replace', args: { file_path: 'plans/plan.md', old_string: 'a', new_string: 'b' } }, undefined, 'deny'],\n"
        "  [{ name: 'run_shell_command', args: { command: 'ls' } }, undefined, 'deny'],\n"
        "  [{ name: 'save_memory', args: { fact: 'x' } }, undefined, 'deny'],\n"
        "  [{ name: 'exit_plan_mode', args: {} }, undefined, 'deny'],\n"
        "  [{ name: 'mcp_ostk_fs_ops', args: { op: 'write' } }, 'ostk', 'deny'],\n"
        "  [{ name: 'mcp_ostk_bash', args: { command: 'id' } }, 'ostk', 'deny'],\n"
        "  [{ name: 'mcp_ostk_spawn', args: {} }, 'ostk', 'deny'],\n"
        "  [{ name: 'read_file', args: { file_path: 'README.md' } }, undefined, 'allow'],\n"
        "  [{ name: 'grep_search', args: { pattern: 'x' } }, undefined, 'allow'],\n"
        "  [{ name: 'glob', args: { pattern: '*.py' } }, undefined, 'allow'],\n"
        "  [{ name: 'list_directory', args: { dir_path: '.' } }, undefined, 'allow'],\n"
        "  [{ name: 'mcp_ostk_read', args: { path: 'README.md' } }, 'ostk', 'allow'],\n"
        "  [{ name: 'mcp_ostk_search', args: { query: 'spec' } }, 'ostk', 'allow'],\n"
        "];\n"
        "for (const [call, server, expected] of checks) {\n"
        "  const res = await engine.check(call, server);\n"
        "  if (res.decision !== expected || res.rule?.priority !== 5.1) {\n"
        "    throw new Error(`${call.name}: got ${res.decision} @ ${res.rule?.priority}`);\n"
        "  }\n"
        "}\n"
        "console.log('ENGINE_OK');\n"
    )
    proc = subprocess.run(
        [node_exe, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=15.0,
        check=False,
    )
    assert proc.returncode == 0, f"Gemini 0.57.0 PolicyEngine failed: {proc.stderr}"
    assert "ENGINE_OK" in proc.stdout


# ---------------------------------------------------------------------------
# 2. Engine-Enforced Read-Only Mode in spec_critique._run_optional_live_critique
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_critique_gemini_passes_plan_mode_and_admin_policy(tmp_path):
    from routers.spec_critique import _run_optional_live_critique

    repo = tmp_path / "gem_crit_repo"
    repo.mkdir()
    captured: dict = {}
    fake_proc = _FakeProcess(stdout_lines=[b"- Audit clean\n- Edge cases covered\n"], return_code=0)

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        captured["cwd"] = kwargs.get("cwd")
        return fake_proc

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/opt/homebrew/bin/gemini"),
        patch("routers.spec_critique.asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        out = await _run_optional_live_critique(
            runtime="gemini",
            model="gemini-3.1-pro",
            spec_text="# Spec Title\n- [ ] Check 1\n",
            matched_existing_files=[],
            repo_root=repo,
        )

    assert out is not None and "Audit clean" in out
    argv = captured["argv"]
    assert argv[0] == "/opt/homebrew/bin/gemini"
    assert "-p" in argv
    assert "--skip-trust" in argv
    assert "--approval-mode" in argv
    assert argv[argv.index("--approval-mode") + 1] == "plan"
    assert "--admin-policy" in argv
    assert argv[argv.index("--admin-policy") + 1] == str(gcp.get_readonly_policy_path())
    assert "-m" in argv
    assert argv[argv.index("-m") + 1] == "gemini-3.1-pro-preview"
    assert "yolo" not in argv
    assert "-y" not in argv
    assert "--yolo" not in argv


@pytest.mark.asyncio
async def test_live_critique_claude_passes_plan_permission_mode_and_disallowed_tools(tmp_path):
    from routers.spec_critique import _run_optional_live_critique

    repo = tmp_path / "claude_crit_repo"
    repo.mkdir()
    captured: dict = {}
    fake_proc = _FakeProcess(stdout_lines=[b"- Clear acceptance criteria\n"], return_code=0)

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        captured["cwd"] = kwargs.get("cwd")
        return fake_proc

    with (
        patch("routers.spec_critique.shutil.which", return_value="/usr/local/bin/claude"),
        patch("routers.spec_critique.asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        out = await _run_optional_live_critique(
            runtime="claude",
            model="claude-sonnet-4-5",
            spec_text="---\ntitle: Demo\n---\n- [ ] AC 1\n",
            matched_existing_files=["api/existing.py"],
            repo_root=repo,
        )

    assert out == "- Clear acceptance criteria"
    argv = captured["argv"]
    assert argv[0] == "/usr/local/bin/claude"
    assert "--print" in argv
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert "--no-session-persistence" in argv
    assert (
        "--disallowed-tools=Edit,Write,Bash,NotebookEdit,"
        "mcp__ostk__fs_ops,mcp__ostk__bash,mcp__ostk__spawn"
    ) in argv
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-5"
    assert argv[-2] == "--"
    assert "Existing files wrongly marked Create" in argv[-1]
    assert "--dangerously-skip-permissions" not in argv
    assert "bypassPermissions" not in argv


@pytest.mark.asyncio
async def test_cross_critique_endpoint_live_llm_enforces_readonly_for_both_runtimes(
    tmp_path,
    _isolated_youros_state,
):
    from routers import spec_critique

    repo = tmp_path / "endpoint_repo"
    _init_git_repo(repo)
    specs_dir = _isolated_youros_state / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_file = specs_dir / "plan-enforced.md"
    spec_file.write_text(
        "---\ntitle: Plan Mode Spec\ntasks:\n  - \"3120\"\n---\n- [ ] Enforce read-only critique\n",
        encoding="utf-8",
    )

    invocations: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        invocations.append(list(args))
        return _FakeProcess(stdout_lines=[b"- Verified read-only live critique\n"], return_code=0)

    app = FastAPI()
    app.include_router(spec_critique.router, prefix="/api")
    transport = ASGITransport(app=app)

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/opt/homebrew/bin/gemini"),
        patch("routers.spec_critique.shutil.which", return_value="/usr/local/bin/claude"),
        patch("routers.spec_critique.asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r_gem = await client.post(
                f"/api/specs/{spec_file}/cross-critique",
                json={"reviewer_runtime": "gemini", "run_live_llm": True, "repo_path": str(repo)},
            )
            r_cla = await client.post(
                f"/api/specs/{spec_file}/cross-critique",
                json={"reviewer_runtime": "claude", "run_live_llm": True, "repo_path": str(repo)},
            )

    assert r_gem.status_code == 200
    assert r_cla.status_code == 200
    assert r_gem.json()["live_critique"] == "- Verified read-only live critique"
    assert r_cla.json()["live_critique"] == "- Verified read-only live critique"
    assert len(invocations) == 2

    gem_argv, cla_argv = invocations[0], invocations[1]
    assert gem_argv[gem_argv.index("--approval-mode") + 1] == "plan"
    assert gem_argv[gem_argv.index("--admin-policy") + 1] == str(gcp.get_readonly_policy_path())
    assert cla_argv[cla_argv.index("--permission-mode") + 1] == "plan"
    assert "--no-session-persistence" in cla_argv


# ---------------------------------------------------------------------------
# 3. GeminiCliProvider.spawn_subagent & stream_gemini_cli read_only=True vs yolo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_spawn_subagent_read_only_vs_default_yolo(tmp_path):
    provider = gcp.GeminiCliProvider()
    repo = tmp_path / "spawn_ro_repo"
    _init_git_repo(repo)

    captured_argvs: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        captured_argvs.append(list(args))
        return _FakeProcess(
            stdout_lines=[_jsonl({"type": "result", "status": "success", "stats": {"input_tokens": 5, "output_tokens": 2}})],
            return_code=0,
        )

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/opt/homebrew/bin/gemini"),
        patch("services.gemini_cli_provider.asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        # 1. extra={"read_only": True} -> plan + --admin-policy
        await provider.spawn_subagent(
            SpawnRequest(
                name="gem-ro-critique",
                prompt="Audit spec without mutating files",
                isolation="none",
                repo_path=str(repo),
                extra={"read_only": True},
            )
        )
        # 2. keyword read_only=True -> plan + --admin-policy
        await provider.spawn_subagent(
            name="gem-ro-kw",
            prompt="Audit via keyword read_only",
            isolation="none",
            repo_path=str(repo),
            read_only=True,
        )
        # 3. default (read_only not set) -> yolo
        await provider.spawn_subagent(
            SpawnRequest(
                name="gem-default-yolo",
                prompt="Implement feature",
                isolation="none",
                repo_path=str(repo),
            )
        )
        await asyncio.sleep(0.05)

    assert len(captured_argvs) == 3

    for ro_argv in (captured_argvs[0], captured_argvs[1]):
        assert "--approval-mode" in ro_argv
        assert ro_argv[ro_argv.index("--approval-mode") + 1] == "plan"
        assert "--admin-policy" in ro_argv
        assert ro_argv[ro_argv.index("--admin-policy") + 1] == str(gcp.get_readonly_policy_path())
        assert "yolo" not in ro_argv

    yolo_argv = captured_argvs[2]
    assert "--approval-mode" in yolo_argv
    assert yolo_argv[yolo_argv.index("--approval-mode") + 1] == "yolo"
    assert "--admin-policy" not in yolo_argv


@pytest.mark.asyncio
async def test_gemini_spawn_subagent_readonly_agentfile_auto_enforces_plan_policy(tmp_path):
    provider = gcp.GeminiCliProvider()
    repo = tmp_path / "ro_agentfile_repo"
    _init_git_repo(repo)

    ro_agentfile = tmp_path / "strict-reader.agent"
    ro_agentfile.write_text(
        "FROM auto\n"
        'PROMPT "Read-only auditor"\n'
        "TOOL file:read\n",
        encoding="utf-8",
    )

    captured_argv: list[str] = []

    async def fake_exec(*args, **kwargs):
        captured_argv.extend(args)
        return _FakeProcess(
            stdout_lines=[_jsonl({"type": "result", "status": "success"})],
            return_code=0,
        )

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/opt/homebrew/bin/gemini"),
        patch("services.agentfile_parser._find_any_agentfile", return_value=ro_agentfile),
        patch("services.gemini_cli_provider.asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        await provider.spawn_subagent(
            name="gem-ro-template",
            prompt="Check files",
            template="strict-reader",
            isolation="none",
            repo_path=str(repo),
        )
        await asyncio.sleep(0.05)

    assert captured_argv[captured_argv.index("--approval-mode") + 1] == "plan"
    assert captured_argv[captured_argv.index("--admin-policy") + 1] == str(gcp.get_readonly_policy_path())
    assert "yolo" not in captured_argv


@pytest.mark.asyncio
async def test_stream_gemini_cli_read_only_vs_default_yolo():
    captured_argvs: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        captured_argvs.append(list(args))
        return _FakeProcess(
            stdout_lines=[
                _jsonl({"type": "message", "role": "assistant", "content": "Read-only reply", "delta": True}),
                _jsonl({"type": "result", "status": "success", "stats": {"input_tokens": 8, "output_tokens": 3}}),
            ],
            return_code=0,
        )

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        ws_ro = _FakeWebSocket()
        text_ro = await gcp.stream_gemini_cli(
            [{"role": "user", "content": "Inspect architecture"}],
            ws_ro,
            read_only=True,
        )
        ws_default = _FakeWebSocket()
        text_def = await gcp.stream_gemini_cli(
            [{"role": "user", "content": "Edit code"}],
            ws_default,
        )

    assert text_ro == "Read-only reply"
    assert text_def == "Read-only reply"
    assert len(captured_argvs) == 2

    ro_argv = captured_argvs[0]
    assert ro_argv[ro_argv.index("--approval-mode") + 1] == "plan"
    assert "--admin-policy" in ro_argv
    assert ro_argv[ro_argv.index("--admin-policy") + 1] == str(gcp.get_readonly_policy_path())
    assert "yolo" not in ro_argv

    def_argv = captured_argvs[1]
    assert def_argv[def_argv.index("--approval-mode") + 1] == "yolo"
    assert "--admin-policy" not in def_argv


@pytest.mark.asyncio
async def test_no_em_dashes_in_modified_plan_mode_files():
    for rel in (
        ".gemini/policies/readonly-critique.toml",
        "api/routers/spec_critique.py",
        "api/services/gemini_cli_provider.py",
        "api/tests/test_plan_mode_enforcement.py",
        "api/tests/test_gemini_cli_057_parity.py",
    ):
        target = REPO_ROOT / rel
        assert target.is_file(), f"Missing file: {target}"
        content = target.read_text(encoding="utf-8")
        assert "\u2014" not in content, f"Em-dash found in {rel}"
