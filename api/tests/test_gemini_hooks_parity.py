"""Parity tests for Gemini CLI 0.57.0 lifecycle hooks (needle ->3117).

Verifies:
1. .gitignore unignores .gemini/settings.json and .gemini/policies/** while
   ignoring transient .gemini/* files.
2. .gemini/settings.json is valid JSON and wires SessionStart, SessionEnd,
   BeforeAgent, BeforeTool, and AfterTool using $GEMINI_PROJECT_DIR.
3. Runtime-aware .claude/hooks/*.sh scripts produce gemini-cli-* agent rows
   with source="gemini-cli", default model="gemini-3.1-pro-preview", and clean
   JSON stdout under Gemini CLI, while preserving 100% backward compatibility
   (claude-code-*, source="claude-code") under Claude Code.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
GEMINI_SETTINGS_PATH = REPO_ROOT / ".gemini" / "settings.json"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"


class _RecordedServer:
    """Thread-safe local HTTP server recording all hook requests."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                with parent._lock:
                    parent.requests.append(
                        {
                            "method": "GET",
                            "path": self.path,
                            "body": None,
                        }
                    )
                payload = json.dumps({"agents": [], "tasks": []}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
                parsed = None
                if raw.strip():
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        parsed = raw
                with parent._lock:
                    parent.requests.append(
                        {
                            "method": "POST",
                            "path": self.path,
                            "body": parsed,
                            "raw": raw,
                        }
                    )
                payload = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt: str, *args: object) -> None:
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._httpd.server_port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self.requests)


@pytest.fixture()
def mock_youros_server():
    srv = _RecordedServer()
    try:
        yield srv
    finally:
        srv.close()


def _build_hook_env(
    tmp_path: Path,
    runtime: str,
    base_url: str,
    also_set_claude_dir: bool = False,
) -> tuple[dict[str, str], Path]:
    """Create a sandboxed environment for invoking hook scripts."""
    home_dir = tmp_path / f"home_{runtime}"
    proj_dir = tmp_path / f"proj_{runtime}"
    (home_dir / ".youros" / "subagents").mkdir(parents=True, exist_ok=True)
    # Stamp idle sweep so heartbeat-and-drain exits without background sweeps.
    (home_dir / ".youros" / "subagents" / "last-idle-sweep.stamp").write_text(
        str(int(time.time()))
    )
    (proj_dir / ".ostk").mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    for key in (
        "CLAUDE_PROJECT_DIR",
        "CLAUDE_SESSION_ID",
        "GEMINI_PROJECT_DIR",
        "GEMINI_SESSION_ID",
        "GEMINI_CWD",
        "YOUROS_AGENT_NAME",
        "TORIOS_API_BASE",
    ):
        env.pop(key, None)

    env["HOME"] = str(home_dir)
    env["YOUROS_BACKEND_URL"] = base_url
    env["YOUROS_AGENTS_URL"] = f"{base_url}/api/agents"
    env["YOUROS_COMPLETE_URL_BASE"] = f"{base_url}/api/agents"
    env["YOUROS_DISABLE_COMPLETION_WATCHER"] = "1"

    if runtime == "gemini":
        env["GEMINI_PROJECT_DIR"] = str(proj_dir)
        if also_set_claude_dir:
            # Gemini CLI 0.57.0 sets CLAUDE_PROJECT_DIR alongside GEMINI_PROJECT_DIR.
            env["CLAUDE_PROJECT_DIR"] = str(proj_dir)
    elif runtime == "claude":
        env["CLAUDE_PROJECT_DIR"] = str(proj_dir)
    else:
        raise ValueError(f"unknown runtime: {runtime}")

    return env, proj_dir


# ---------------------------------------------------------------------------
# 1. .gitignore allowlist for .gemini
# ---------------------------------------------------------------------------


def test_gitignore_allows_gemini_settings_and_policies():
    """Verify .gitignore unignores .gemini/settings.json and .gemini/policies/**."""
    gitignore_lines = [
        line.strip() for line in GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    ]
    for expected in (
        ".gemini/*",
        "!.gemini/settings.json",
        "!.gemini/policies/",
        "!.gemini/policies/**",
    ):
        assert expected in gitignore_lines, f"Missing {expected!r} in .gitignore"

    for allowed_rel in (
        ".gemini/settings.json",
        ".gemini/policies/readonly-critique.toml",
    ):
        proc = subprocess.run(
            ["git", "check-ignore", "-q", allowed_rel],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        assert proc.returncode == 1, (
            f"{allowed_rel} must NOT be ignored by git (got rc={proc.returncode})"
        )

    for ignored_rel in (
        ".gemini/settings.local.json",
        ".gemini/tmp-session.jsonl",
    ):
        proc = subprocess.run(
            ["git", "check-ignore", "-q", ignored_rel],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        assert proc.returncode == 0, (
            f"{ignored_rel} MUST be ignored by git (got rc={proc.returncode})"
        )


# ---------------------------------------------------------------------------
# 2. .gemini/settings.json schema and hook wiring
# ---------------------------------------------------------------------------


def test_gemini_settings_json_exists_and_wires_lifecycle_hooks():
    """Verify .gemini/settings.json wires all 5 Gemini CLI 0.57.0 hook events."""
    assert GEMINI_SETTINGS_PATH.is_file(), f"Missing {GEMINI_SETTINGS_PATH}"
    cfg = json.loads(GEMINI_SETTINGS_PATH.read_text(encoding="utf-8"))
    assert isinstance(cfg, dict)
    hooks = cfg.get("hooks")
    assert isinstance(hooks, dict), "Expected top-level 'hooks' object"

    for event_key in (
        "SessionStart",
        "SessionEnd",
        "BeforeAgent",
        "BeforeTool",
        "AfterTool",
    ):
        assert event_key in hooks, f"Missing hook event {event_key!r}"
        assert isinstance(hooks[event_key], list) and hooks[event_key]

    def _commands_for(event_key: str) -> list[tuple[str | None, list[str]]]:
        groups: list[tuple[str | None, list[str]]] = []
        for group in hooks[event_key]:
            matcher = group.get("matcher")
            cmds: list[str] = []
            for hk in group.get("hooks", []):
                assert hk.get("type") == "command"
                cmd = hk.get("command", "")
                assert cmd.startswith("$GEMINI_PROJECT_DIR/.claude/hooks/"), (
                    f"Hook command must use $GEMINI_PROJECT_DIR: {cmd!r}"
                )
                rel = cmd.removeprefix("$GEMINI_PROJECT_DIR/")
                script_path = REPO_ROOT / rel
                assert script_path.is_file(), f"Missing hook script: {script_path}"
                assert os.access(script_path, os.X_OK), (
                    f"Hook script not executable: {script_path}"
                )
                cmds.append(cmd)
            groups.append((matcher, cmds))
        return groups

    assert _commands_for("SessionStart") == [
        (None, ["$GEMINI_PROJECT_DIR/.claude/hooks/session-start.sh"])
    ]
    assert _commands_for("SessionEnd") == [
        (None, ["$GEMINI_PROJECT_DIR/.claude/hooks/session-end.sh"])
    ]
    assert _commands_for("BeforeAgent") == [
        (None, ["$GEMINI_PROJECT_DIR/.claude/hooks/prompt-header.sh"])
    ]

    before_tool = _commands_for("BeforeTool")
    assert len(before_tool) == 2
    bt_matcher, bt_agent_cmds = before_tool[0]
    assert bt_matcher is not None
    for subagent_tool in ("Agent", "codebase_investigator", "generalist"):
        assert re.search(bt_matcher, subagent_tool), (
            f"BeforeTool subagent matcher {bt_matcher!r} did not match {subagent_tool!r}"
        )
    assert not re.search(bt_matcher, "run_shell_command")
    assert bt_agent_cmds == ["$GEMINI_PROJECT_DIR/.claude/hooks/pre-agent-guard.sh"]
    assert before_tool[1] == (
        None,
        [
            "$GEMINI_PROJECT_DIR/.claude/hooks/heartbeat-and-drain.sh",
            "$GEMINI_PROJECT_DIR/.claude/hooks/pre-tool-guard.sh",
        ],
    )

    after_tool = _commands_for("AfterTool")
    assert len(after_tool) == 2
    at_matcher, at_agent_cmds = after_tool[0]
    assert at_matcher is not None
    for subagent_tool in ("Agent", "codebase_investigator", "generalist"):
        assert re.search(at_matcher, subagent_tool)
    assert not re.search(at_matcher, "run_shell_command")
    assert at_agent_cmds == [
        "$GEMINI_PROJECT_DIR/.claude/hooks/complete-agent.sh",
        "$GEMINI_PROJECT_DIR/.claude/hooks/post-agent-watch.sh",
    ]
    assert after_tool[1] == (
        None,
        ["$GEMINI_PROJECT_DIR/.claude/hooks/post-tool-watch.sh"],
    )


# ---------------------------------------------------------------------------
# 3. session-start.sh runtime parity (Gemini CLI vs Claude Code)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_gemini_registers_agent_and_passes_endpoint_contract(
    tmp_path: Path, mock_youros_server: _RecordedServer
):
    """Gemini SessionStart registers gemini-cli-<sid> with source='gemini-cli'."""
    # Also set CLAUDE_PROJECT_DIR=True here to prove compatibility with Gemini CLI 0.57.0
    # HookRunner, which exports both GEMINI_PROJECT_DIR and CLAUDE_PROJECT_DIR.
    env, proj_dir = _build_hook_env(
        tmp_path, "gemini", mock_youros_server.base_url, also_set_claude_dir=True
    )
    payload = {
        "session_id": "gem9876543210abcdef",
        "cwd": str(proj_dir),
        "hook_event_name": "SessionStart",
        "source": "startup",
    }
    res = subprocess.run(
        ["bash", str(HOOKS_DIR / "session-start.sh")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert res.returncode == 0, res.stderr
    assert isinstance(json.loads(res.stdout), dict), (
        f"Gemini SessionStart stdout must be valid JSON, got {res.stdout!r}"
    )

    reg_reqs = [
        r for r in mock_youros_server.snapshot() if r["path"] == "/api/agents/register"
    ]
    assert len(reg_reqs) == 1, mock_youros_server.snapshot()
    body = reg_reqs[0]["body"]
    assert isinstance(body, dict)
    assert body["name"] == "gemini-cli-gem9876543"
    assert body["source"] == "gemini-cli"
    assert body["model"] == "gemini-3.1-pro-preview"
    assert body["status"] == "running"
    assert body["description"] == f"Gemini CLI session (cwd: {proj_dir})"
    assert (proj_dir / ".ostk" / "current_model").read_text().strip() == (
        "gemini-3.1-pro-preview"
    )

    # Verify the exact body is accepted with HTTP 200 by POST /api/agents/register.
    from routers.agents import agent_metadata

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, patch(
            "routers.agents._save_agent_state_async", AsyncMock()
        ), patch("routers.agents.chat_ack_bot", MagicMock()):
            mock_ostk._run = AsyncMock(return_value="")
            api_resp = await client.post("/api/agents/register", json=body)
    assert api_resp.status_code == 200, api_resp.text
    assert agent_metadata[body["name"]]["source"] == "gemini-cli"
    assert agent_metadata[body["name"]]["model"] == "gemini-3.1-pro-preview"

    agent_metadata.pop(body["name"], None)


@pytest.mark.asyncio
async def test_session_start_claude_backward_compatible_and_includes_source(
    tmp_path: Path, mock_youros_server: _RecordedServer
):
    """Claude SessionStart still registers claude-code-<sid> with source='claude-code'."""
    env, proj_dir = _build_hook_env(tmp_path, "claude", mock_youros_server.base_url)
    payload = {
        "session_id": "cld1234567890abcdef",
        "cwd": str(proj_dir),
        "hook_event_name": "SessionStart",
    }
    res = subprocess.run(
        ["bash", str(HOOKS_DIR / "session-start.sh")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert res.returncode == 0, res.stderr

    reg_reqs = [
        r for r in mock_youros_server.snapshot() if r["path"] == "/api/agents/register"
    ]
    assert len(reg_reqs) == 1, mock_youros_server.snapshot()
    body = reg_reqs[0]["body"]
    assert isinstance(body, dict)
    assert body["name"] == "claude-code-cld1234567"
    assert body["source"] == "claude-code"
    assert body["model"] == "claude-sonnet-4-6"
    assert body["status"] == "running"
    assert body["description"] == f"Claude Code session (cwd: {proj_dir})"

    from routers.agents import agent_metadata

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, patch(
            "routers.agents._save_agent_state_async", AsyncMock()
        ), patch("routers.agents.chat_ack_bot", MagicMock()):
            mock_ostk._run = AsyncMock(return_value="")
            api_resp = await client.post("/api/agents/register", json=body)
    assert api_resp.status_code == 200, api_resp.text
    assert agent_metadata[body["name"]]["source"] == "claude-code"

    agent_metadata.pop(body["name"], None)


# ---------------------------------------------------------------------------
# 4. heartbeat-and-drain.sh + session-end.sh lifecycle parity
# ---------------------------------------------------------------------------


def test_heartbeat_and_session_end_gemini_and_claude_prefixes_match(
    tmp_path: Path, mock_youros_server: _RecordedServer
):
    """Verify heartbeat-and-drain.sh and session-end.sh target matching runtime prefixes."""
    # --- Gemini lifecycle ---
    gem_env, gem_proj = _build_hook_env(
        tmp_path, "gemini", mock_youros_server.base_url
    )
    hb_gem = subprocess.run(
        ["bash", str(HOOKS_DIR / "heartbeat-and-drain.sh")],
        input=json.dumps(
            {
                "session_id": "gem9876543210abcdef",
                "cwd": str(gem_proj),
                "hook_event_name": "BeforeTool",
                "tool_name": "run_shell_command",
                "tool_input": {"command": "git status"},
            }
        ),
        capture_output=True,
        text=True,
        env=gem_env,
        timeout=10,
    )
    assert hb_gem.returncode == 0, hb_gem.stderr
    assert isinstance(json.loads(hb_gem.stdout), dict), (
        f"Gemini heartbeat stdout must be JSON, got {hb_gem.stdout!r}"
    )

    deadline = time.time() + 3.0
    while time.time() < deadline:
        paths = [r["path"] for r in mock_youros_server.snapshot()]
        if "/api/agents/gemini-cli-gem9876543/heartbeat" in paths:
            break
        time.sleep(0.05)
    assert "/api/agents/gemini-cli-gem9876543/heartbeat" in [
        r["path"] for r in mock_youros_server.snapshot()
    ], mock_youros_server.snapshot()

    end_gem = subprocess.run(
        ["bash", str(HOOKS_DIR / "session-end.sh")],
        input=json.dumps(
            {
                "session_id": "gem9876543210abcdef",
                "cwd": str(gem_proj),
                "hook_event_name": "SessionEnd",
                "reason": "exit",
            }
        ),
        capture_output=True,
        text=True,
        env=gem_env,
        timeout=10,
    )
    assert end_gem.returncode == 0, end_gem.stderr
    assert isinstance(json.loads(end_gem.stdout), dict)
    assert "/api/agents/gemini-cli-gem9876543/complete" in [
        r["path"] for r in mock_youros_server.snapshot()
    ], mock_youros_server.snapshot()

    # --- Claude lifecycle ---
    cld_env, cld_proj = _build_hook_env(
        tmp_path, "claude", mock_youros_server.base_url
    )
    hb_cld = subprocess.run(
        ["bash", str(HOOKS_DIR / "heartbeat-and-drain.sh")],
        input=json.dumps(
            {
                "session_id": "cld1234567890abcdef",
                "cwd": str(cld_proj),
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git status"},
            }
        ),
        capture_output=True,
        text=True,
        env=cld_env,
        timeout=10,
    )
    assert hb_cld.returncode == 0, hb_cld.stderr

    deadline = time.time() + 3.0
    while time.time() < deadline:
        paths = [r["path"] for r in mock_youros_server.snapshot()]
        if "/api/agents/claude-code-cld1234567/heartbeat" in paths:
            break
        time.sleep(0.05)
    assert "/api/agents/claude-code-cld1234567/heartbeat" in [
        r["path"] for r in mock_youros_server.snapshot()
    ], mock_youros_server.snapshot()

    end_cld = subprocess.run(
        ["bash", str(HOOKS_DIR / "session-end.sh")],
        input=json.dumps(
            {
                "session_id": "cld1234567890abcdef",
                "cwd": str(cld_proj),
                "hook_event_name": "SessionEnd",
            }
        ),
        capture_output=True,
        text=True,
        env=cld_env,
        timeout=10,
    )
    assert end_cld.returncode == 0, end_cld.stderr
    assert "/api/agents/claude-code-cld1234567/complete" in [
        r["path"] for r in mock_youros_server.snapshot()
    ], mock_youros_server.snapshot()


# ---------------------------------------------------------------------------
# 5. pre-tool-guard.sh, post-tool-watch.sh, prompt-header.sh Gemini parity
# ---------------------------------------------------------------------------


def test_pre_tool_guard_gemini_handles_before_tool_and_bash_guards(
    tmp_path: Path, mock_youros_server: _RecordedServer
):
    """pre-tool-guard.sh handles Gemini BeforeTool payloads cleanly with JSON stdout."""
    gem_env, gem_proj = _build_hook_env(
        tmp_path, "gemini", mock_youros_server.base_url
    )

    # Safe shell command -> exit 0, clean JSON stdout
    ok_res = subprocess.run(
        ["bash", str(HOOKS_DIR / "pre-tool-guard.sh")],
        input=json.dumps(
            {
                "session_id": "gem9876543",
                "cwd": str(gem_proj),
                "hook_event_name": "BeforeTool",
                "tool_name": "run_shell_command",
                "tool_input": {"command": "git status -sb"},
            }
        ),
        capture_output=True,
        text=True,
        env=gem_env,
        timeout=10,
    )
    assert ok_res.returncode == 0, ok_res.stderr
    assert isinstance(json.loads(ok_res.stdout), dict)

    # Reserved zsh variable in Gemini run_shell_command -> triggers bash_guards advice on stderr,
    # while stdout remains valid JSON.
    bad_zsh_res = subprocess.run(
        ["bash", str(HOOKS_DIR / "pre-tool-guard.sh")],
        input=json.dumps(
            {
                "session_id": "gem9876543",
                "cwd": str(gem_proj),
                "hook_event_name": "BeforeTool",
                "tool_name": "run_shell_command",
                "tool_input": {"command": "path=/tmp/broken"},
            }
        ),
        capture_output=True,
        text=True,
        env=gem_env,
        timeout=10,
    )
    assert bad_zsh_res.returncode == 0
    assert "read-only variable" in bad_zsh_res.stderr
    assert isinstance(json.loads(bad_zsh_res.stdout), dict)

    # Subagent tool skipped cleanly with JSON stdout
    sub_res = subprocess.run(
        ["bash", str(HOOKS_DIR / "pre-tool-guard.sh")],
        input=json.dumps(
            {
                "session_id": "gem9876543",
                "cwd": str(gem_proj),
                "hook_event_name": "BeforeTool",
                "tool_name": "codebase_investigator",
                "tool_input": {"objective": "inspect hooks"},
            }
        ),
        capture_output=True,
        text=True,
        env=gem_env,
        timeout=10,
    )
    assert sub_res.returncode == 0
    assert isinstance(json.loads(sub_res.stdout), dict)


def test_post_tool_watch_and_prompt_header_gemini_json_hygiene(
    tmp_path: Path, mock_youros_server: _RecordedServer
):
    """post-tool-watch.sh (AfterTool) and prompt-header.sh (BeforeAgent) emit valid JSON."""
    gem_env, gem_proj = _build_hook_env(
        tmp_path, "gemini", mock_youros_server.base_url
    )

    # Enable edit_postwatch in sandboxed rules.json so 3 consecutive Gemini `replace`
    # edits trigger SAA REMINDER, proving plain-text rule output is wrapped in AfterTool JSON.
    rules_path = Path(gem_env["HOME"]) / ".youros" / "rules.json"
    rules_path.write_text(json.dumps({"rules": {"edit_postwatch": {"enabled": True}}}))
    target_file = str(gem_proj / "sample.py")
    last_out = ""
    for _ in range(3):
        ptw = subprocess.run(
            ["bash", str(HOOKS_DIR / "post-tool-watch.sh")],
            input=json.dumps(
                {
                    "session_id": "gem9876543",
                    "cwd": str(gem_proj),
                    "hook_event_name": "AfterTool",
                    "tool_name": "replace",
                    "tool_input": {"file_path": target_file},
                    "tool_response": {"llmContent": "ok"},
                }
            ),
            capture_output=True,
            text=True,
            env=gem_env,
            timeout=10,
        )
        assert ptw.returncode == 0, ptw.stderr
        parsed_ptw = json.loads(ptw.stdout)
        assert isinstance(parsed_ptw, dict)
        last_out = ptw.stdout

    parsed_third = json.loads(last_out)
    assert parsed_third["hookSpecificOutput"]["hookEventName"] == "AfterTool"
    assert "SAA REMINDER" in parsed_third["hookSpecificOutput"]["additionalContext"]

    # BeforeAgent -> prompt-header.sh wraps standing rules in BeforeAgent additionalContext JSON
    ph_gem = subprocess.run(
        ["bash", str(HOOKS_DIR / "prompt-header.sh")],
        input=json.dumps(
            {
                "session_id": "gem9876543",
                "cwd": str(gem_proj),
                "hook_event_name": "BeforeAgent",
                "prompt": "status check",
            }
        ),
        capture_output=True,
        text=True,
        env=gem_env,
        timeout=10,
    )
    assert ph_gem.returncode == 0, ph_gem.stderr
    parsed_ph = json.loads(ph_gem.stdout)
    assert parsed_ph["hookSpecificOutput"]["hookEventName"] == "BeforeAgent"
    assert "STANDING RULES:" in parsed_ph["hookSpecificOutput"]["additionalContext"]

    # Claude UserPromptSubmit -> prompt-header.sh keeps plain-text output
    cld_env, cld_proj = _build_hook_env(
        tmp_path, "claude", mock_youros_server.base_url
    )
    ph_cld = subprocess.run(
        ["bash", str(HOOKS_DIR / "prompt-header.sh")],
        input=json.dumps(
            {
                "session_id": "cld1234567",
                "cwd": str(cld_proj),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "status check",
            }
        ),
        capture_output=True,
        text=True,
        env=cld_env,
        timeout=10,
    )
    assert ph_cld.returncode == 0, ph_cld.stderr
    assert "STANDING RULES:" in ph_cld.stdout


def test_subagent_hooks_gemini_handle_investigator_and_generalist_cleanly(
    tmp_path: Path, mock_youros_server: _RecordedServer
):
    """pre-agent-guard.sh, complete-agent.sh, and post-agent-watch.sh emit clean JSON on Gemini subagent tools."""
    gem_env, gem_proj = _build_hook_env(
        tmp_path, "gemini", mock_youros_server.base_url
    )

    pre_res = subprocess.run(
        ["bash", str(HOOKS_DIR / "pre-agent-guard.sh")],
        input=json.dumps(
            {
                "session_id": "gem9876543",
                "cwd": str(gem_proj),
                "hook_event_name": "BeforeTool",
                "tool_name": "codebase_investigator",
                "tool_input": {"objective": "read-only codebase check"},
            }
        ),
        capture_output=True,
        text=True,
        env=gem_env,
        timeout=10,
    )
    assert pre_res.returncode == 0, pre_res.stderr
    assert isinstance(json.loads(pre_res.stdout), dict)

    comp_res = subprocess.run(
        ["bash", str(HOOKS_DIR / "complete-agent.sh")],
        input=json.dumps(
            {
                "session_id": "gem9876543",
                "cwd": str(gem_proj),
                "hook_event_name": "AfterTool",
                "tool_name": "generalist",
                "tool_input": {"request": "summarize"},
                "tool_response": {"llmContent": "done"},
            }
        ),
        capture_output=True,
        text=True,
        env=gem_env,
        timeout=10,
    )
    assert comp_res.returncode == 0, comp_res.stderr
    assert isinstance(json.loads(comp_res.stdout), dict)

    watch_res = subprocess.run(
        ["bash", str(HOOKS_DIR / "post-agent-watch.sh")],
        input=json.dumps(
            {
                "session_id": "gem9876543",
                "cwd": str(gem_proj),
                "hook_event_name": "AfterTool",
                "tool_name": "codebase_investigator",
                "tool_input": {"objective": "read-only check"},
                "tool_response": {"llmContent": "report"},
            }
        ),
        capture_output=True,
        text=True,
        env=gem_env,
        timeout=10,
    )
    assert watch_res.returncode == 0, watch_res.stderr
    assert isinstance(json.loads(watch_res.stdout), dict)

