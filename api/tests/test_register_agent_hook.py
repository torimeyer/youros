"""End-to-end tests for .claude/hooks/register-agent.sh.

This hook runs PreToolUse when Claude Code's main session invokes the
Agent tool. It must parse the hook JSON payload from stdin, derive a
stable subagent name, and POST a valid payload to /api/agents/register
so every subagent shows up on the Agents page.

Historical bug: the hook posted to http://localhost:8000 while the
backend is HTTPS-only, so every registration silently failed. These
tests lock in the fix by exercising the real hook script and asserting
the body it constructs is exactly what /api/agents/register expects.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402


HOOK_PATH = (
    Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "register-agent.sh"
)


def test_register_agent_hook_file_exists_and_executable():
    """The hook file must be on disk and marked executable."""
    assert HOOK_PATH.exists(), f"missing hook: {HOOK_PATH}"
    assert os.access(HOOK_PATH, os.X_OK), f"hook not executable: {HOOK_PATH}"


def _run_hook_dry(payload: dict, backend_url: str | None) -> dict:
    """Invoke the hook with a mocked curl that records the POST body.

    The hook shells out to `curl` to hit the backend. We intercept curl
    by prepending a temp dir with a fake `curl` that writes its flags
    to a file we can inspect. Returns a dict containing `body` (the
    JSON the hook tried to POST) and `url` (the target url).
    """
    import tempfile

    tmpdir = Path(tempfile.mkdtemp())
    curl_log = tmpdir / "curl.log"
    fake_curl = tmpdir / "curl"
    fake_curl.write_text(
        "#!/bin/bash\n"
        f"echo \"$@\" >> {curl_log}\n"
        # Read any -d value and log it on a separate line prefixed DATA=
        "for i in \"$@\"; do\n"
        "  case \"$prev\" in -d) "
        f"echo \"DATA=$i\" >> {curl_log};; esac\n"
        "  prev=\"$i\"\ndone\n"
        "exit 0\n"
    )
    fake_curl.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{tmpdir}:{env.get('PATH', '')}"
    env["CLAUDE_PROJECT_DIR"] = str(HOOK_PATH.parent.parent.parent)

    result = subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )

    logged = curl_log.read_text() if curl_log.exists() else ""
    shutil.rmtree(tmpdir, ignore_errors=True)

    body = None
    url = None
    for line in logged.splitlines():
        if line.startswith("DATA="):
            try:
                body = json.loads(line[5:])
            except Exception:
                pass
        if "/api/agents/register" in line:
            for tok in line.split():
                if tok.startswith("https://") or tok.startswith("http://"):
                    url = tok.strip('"')
                    break

    return {"exit_code": result.returncode, "body": body, "url": url, "log": logged}


def test_hook_posts_to_https_register():
    """Regression: hook must POST to HTTPS, not HTTP. The backend is HTTPS-only."""
    # Avoid bridge-guard verbs (edit/build/fix/etc) in the description — those
    # cause the hook to exit early without POSTing, which is correct bridge
    # behaviour but would give a false failure here.
    out = _run_hook_dry(
        {
            "tool_name": "Agent",
            "tool_input": {
                "description": "roadmap prewarm https probe",
                "prompt": "do roadmap prewarm",
            },
            "cwd": str(HOOK_PATH.parent.parent.parent),
        },
        backend_url=None,
    )

    assert out["exit_code"] == 0, out
    assert out["url"] is not None, out
    assert out["url"].startswith("https://"), (
        f"hook must post to https, got {out['url']}"
    )
    assert "/api/agents/register" in out["url"]


def test_hook_body_has_required_fields():
    """Every hook POST must include source, name, description, and prompt."""
    out = _run_hook_dry(
        {
            "tool_name": "Agent",
            "tool_input": {
                "description": "my sub task",
                "prompt": "please do the thing",
            },
            "cwd": str(HOOK_PATH.parent.parent.parent),
        },
        backend_url=None,
    )
    body = out["body"]
    assert body is not None, out

    # Required by the /register endpoint validator.
    assert body.get("source") == "claude-code"
    assert body.get("status") == "running"
    # One of task, description, prompt must be present. We include
    # description AND prompt for robustness.
    assert body.get("description"), body
    assert body.get("prompt"), body

    # Name must be slugified from the description. The hook no longer
    # appends a random suffix because random slugs cluttered the Agents
    # page (e.g. "Roadmap G3egyo"). Real name collisions are handled by
    # the /register endpoint returning 409.
    name = body.get("name", "")
    assert name == "my-sub-task", name


def test_hook_name_is_stable_across_identical_descriptions():
    """The hook derives names deterministically from the description.

    Earlier versions of the hook appended a random suffix so every
    spawn had a unique name. That was removed because the random slug
    cluttered the Agents page for the common case of a single spawn.
    Collisions across concurrent identical descriptions are now
    resolved by the /register endpoint (409 on terminal rows; merge
    into the pre-registration on pending rows).
    """
    payload = {
        "tool_name": "Agent",
        "tool_input": {"description": "identical task", "prompt": "x"},
        "cwd": str(HOOK_PATH.parent.parent.parent),
    }
    a = _run_hook_dry(payload, backend_url=None)
    b = _run_hook_dry(payload, backend_url=None)
    assert a["body"] and b["body"]
    assert a["body"]["name"] == b["body"]["name"] == "identical-task", (
        a["body"], b["body"],
    )


@pytest.mark.asyncio
async def test_hook_body_is_accepted_by_register_endpoint():
    """The payload the hook produces must round-trip cleanly through
    POST /api/agents/register."""
    out = _run_hook_dry(
        {
            "tool_name": "Agent",
            "tool_input": {
                "description": "register endpoint contract",
                "prompt": "exercise the register endpoint",
            },
            "cwd": str(HOOK_PATH.parent.parent.parent),
        },
        backend_url=None,
    )
    body = out["body"]
    assert body is not None, out

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, patch(
            "routers.agents._save_agent_state"
        ):
            mock_ostk._run = AsyncMock(return_value="")
            resp = await client.post("/api/agents/register", json=body)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["source"] == "claude-code"

    # Cleanup the in-memory registration we just created.
    from routers.agents import agent_metadata
    agent_metadata.pop(body["name"], None)


# ---------------------------------------------------------------------------
# Slug special-character normalization (regression for bridge slugify bug)
#
# Both register-agent.sh and task-isolation-bridge.sh slugify a task
# description into an agent name. The bug: the slug regex used "" as the
# replacement for disallowed chars, silently dropping them instead of
# replacing with "-". This caused "Diagnose+fix" to become "diagnosefix"
# rather than "diagnose-fix".
#
# These tests use descriptions that avoid bridge-guard verbs
# (edit/write/fix/diagnose/build/etc.) so the hook does not bail early.
# They cover the three separator patterns: +, parens+&, and underscores.
# ---------------------------------------------------------------------------


def test_hook_slug_plus_becomes_hyphen():
    """+ between words must produce a hyphen, not be silently dropped.

    Before fix: "search+compare" -> "searchcompare"
    After fix:  "search+compare" -> "search-compare"
    """
    out = _run_hook_dry(
        {
            "tool_name": "Agent",
            "tool_input": {
                "description": "search+compare two approaches",
                "prompt": "please compare the approaches",
            },
            "cwd": str(HOOK_PATH.parent.parent.parent),
        },
        backend_url=None,
    )
    name = (out.get("body") or {}).get("name", "")
    assert name == "search-compare-two-approaches", (
        f"+ between words should produce a hyphen, got {name!r}"
    )


def test_hook_slug_parens_and_ampersand_become_hyphens():
    """Parentheses and & with no surrounding spaces must produce hyphens, not be dropped.

    The bug only manifests when special chars are NOT adjacent to spaces, because
    .replace(" ", "-") masked the issue for space-surrounded parens. This test
    uses chars glued directly to words to expose the deletion behavior.

    Before fix: "probe(items)and&counts" -> "probeitemsandcounts" (chars deleted)
    After fix:  "probe(items)and&counts" -> "probe-items-and-counts"
    """
    out = _run_hook_dry(
        {
            "tool_name": "Agent",
            "tool_input": {
                "description": "probe(items)and&counts",
                "prompt": "probe the items and counts",
            },
            "cwd": str(HOOK_PATH.parent.parent.parent),
        },
        backend_url=None,
    )
    name = (out.get("body") or {}).get("name", "")
    assert name == "probe-items-and-counts", (
        f"parens and & glued to words should become hyphens, got {name!r}"
    )


def test_hook_slug_underscores_become_single_hyphen():
    """Multiple underscores must collapse to a single hyphen.

    Before fix: "foo___bar" -> "foo---bar" then collapsing gives "foo-bar"
    -- actually underscores were dropped giving "foobar".
    After fix:  "foo___bar" -> "foo-bar".
    """
    out = _run_hook_dry(
        {
            "tool_name": "Agent",
            "tool_input": {
                "description": "foo___bar",
                "prompt": "run foo bar task",
            },
            "cwd": str(HOOK_PATH.parent.parent.parent),
        },
        backend_url=None,
    )
    name = (out.get("body") or {}).get("name", "")
    assert name == "foo-bar", (
        f"underscores should collapse to a single hyphen, got {name!r}"
    )
