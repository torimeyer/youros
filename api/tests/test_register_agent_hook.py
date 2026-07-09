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


# Fake curl shim (→2606). Written per-invocation with __LOG__ replaced by the
# real log path. It records every invocation, performs NO network I/O, and
# answers the detached heartbeat loop's status poll so the loop terminates
# while still fully intercepted (see _run_hook_dry docstring).
_FAKE_CURL_SCRIPT = """#!/bin/bash
# Test shim (2606): record args, never touch the network.
echo "$@" >> __LOG__
prev=""
for i in "$@"; do
  case "$prev" in
    -d) echo "DATA=$i" >> __LOG__;;
  esac
  prev="$i"
done
# The hook's detached heartbeat loop polls GET <base>/api/agents and exits
# once its agent reaches a terminal status. Serve that poll from the
# register bodies recorded above, marking every name "completed", so the
# loop dies here instead of outliving the test and escaping to the live
# backend after this shim is deleted.
is_post=0
url=""
for i in "$@"; do
  if [ "$i" = "POST" ]; then is_post=1; fi
  case "$i" in
    http://*|https://*) url="$i";;
  esac
done
if [ "$is_post" -eq 0 ] && [ "${url%/api/agents}" != "$url" ]; then
  python3 - __LOG__ <<'PY'
import json, sys
names = []
try:
    lines = open(sys.argv[1]).read().splitlines()
except OSError:
    lines = []
for line in lines:
    if line.startswith("DATA="):
        try:
            body = json.loads(line[5:])
        except Exception:
            continue
        name = body.get("name")
        if name:
            names.append(name)
print(json.dumps({"agents": [{"name": n, "status": "completed"} for n in names]}))
PY
  echo "STATUS_POLL_SERVED" >> __LOG__
fi
exit 0
"""


def _run_hook_dry(payload: dict, backend_url: str | None) -> dict:
    """Invoke the hook with a mocked curl and a sandboxed $HOME.

    Isolation contract (→2606): running this suite must never touch the
    live backend on :8000 or real ~/.youros state. Three mechanisms:

    1. curl interception: a fake ``curl`` is prepended to PATH. It records
       every invocation (flags + POST body) to a log we inspect, performs
       no network I/O, and exits 0.
    2. HOME sandbox: $HOME points into the temp dir, so the hook's state
       writes (~/.youros/subagents/history.log, last.name, pending queue,
       debug log) land in the sandbox, and the pending-register drain
       cannot consume real queued entries. With no sandbox config.json the
       hook still resolves its default HTTPS API base, which is what the
       https regression test asserts.
    3. Heartbeat-loop containment: the hook spawns a detached heartbeat
       loop that OUTLIVES the hook process. Before this existed, that loop
       kept running after the test deleted the fake curl, fell back to the
       real curl on PATH, and its idle detector eventually POSTed
       /api/agents/{name}/complete to the LIVE backend, whose unknown-name
       upsert published phantom rows (identical-task, foo-bar,
       register-endpoint-contract, ...) on the real Agents page. The fake
       curl now answers the loop's status poll (GET .../api/agents) with
       every registered name marked "completed", so the loop exits on its
       first iteration while still fully intercepted. We wait for that
       poll (STATUS_POLL_SERVED marker) before deleting the temp dir; if
       it never arrives the temp dir is deliberately left in place so a
       straggler loop keeps resolving the shim and can never reach the
       real backend.

    Returns a dict with ``exit_code``, ``body`` (the JSON the hook tried
    to POST), ``url`` (the target url), ``log`` (raw curl log),
    ``loop_contained`` (the heartbeat loop exited through the intercepted
    status poll) and ``sandbox_history`` (contents of the sandboxed
    ~/.youros/subagents/history.log).
    """
    import tempfile
    import time

    tmpdir = Path(tempfile.mkdtemp())
    curl_log = tmpdir / "curl.log"
    fake_curl = tmpdir / "curl"
    fake_curl.write_text(_FAKE_CURL_SCRIPT.replace("__LOG__", str(curl_log)))
    fake_curl.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{tmpdir}:{env.get('PATH', '')}"
    env["HOME"] = str(tmpdir)
    env["CLAUDE_PROJECT_DIR"] = str(HOOK_PATH.parent.parent.parent)
    # A developer-exported base URL would defeat both the https assertion
    # and the isolation guarantee. Force the hook's default resolution.
    env.pop("TORIOS_API_BASE", None)

    result = subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )

    # Wait for the detached heartbeat loop to hit the (intercepted) status
    # poll and exit. Only after that is deleting the fake curl safe.
    loop_contained = False
    deadline = time.time() + 20
    while time.time() < deadline:
        logged = curl_log.read_text() if curl_log.exists() else ""
        if "STATUS_POLL_SERVED" in logged:
            loop_contained = True
            break
        time.sleep(0.1)
    logged = curl_log.read_text() if curl_log.exists() else ""

    sandbox_history = ""
    hist = tmpdir / ".youros" / "subagents" / "history.log"
    if hist.exists():
        sandbox_history = hist.read_text()

    if loop_contained:
        # Give the loop a beat to consume the poll response and exit.
        time.sleep(0.2)
        shutil.rmtree(tmpdir, ignore_errors=True)
    # else: leave tmpdir in place on purpose. A straggler loop must keep
    # resolving the fake curl rather than fall back to the real one.

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

    return {
        "exit_code": result.returncode,
        "body": body,
        "url": url,
        "log": logged,
        "loop_contained": loop_contained,
        "sandbox_history": sandbox_history,
    }


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


def test_hook_run_is_contained_no_live_backend_traffic():
    """→2606 regression: the suite must not touch the live backend or real
    ~/.youros state.

    Two load-bearing assertions:
    1. loop_contained — the hook's detached heartbeat loop exited through
       the fake curl's status poll BEFORE the shim dir was removed. When
       this breaks, the loop outlives the test, falls back to the real
       curl, and its idle detector POSTs /complete to the live backend,
       which upserts a phantom row on the real Agents page.
    2. sandbox_history — the hook's history.log write landed under the
       sandboxed $HOME, proving state writes are redirected away from the
       real ~/.youros/subagents/.
    """
    out = _run_hook_dry(
        {
            "tool_name": "Agent",
            "tool_input": {
                "description": "containment probe run",
                "prompt": "probe the containment seal",
            },
            "cwd": str(HOOK_PATH.parent.parent.parent),
        },
        backend_url=None,
    )
    assert out["exit_code"] == 0, out
    assert out["loop_contained"], (
        "detached heartbeat loop did not exit through the intercepted "
        "status poll; it would outlive the test and reach the live backend"
    )
    assert "containment-probe-run" in out["sandbox_history"], (
        f"hook history write missing from sandbox HOME: "
        f"{out['sandbox_history']!r}"
    )
