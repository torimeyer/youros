"""Regression tests for POST /api/agents/register response body.

Context: on 2026-04-20 a subagent reported "empty body" from the register
endpoint. Diagnosis showed the endpoint actually returns a full JSON body
(status, result, mailbox_instruction, mailbox_check_interval_seconds).
The "empty body" symptom was an artifact of the caller's curl timeout on
the unrelated GET /api/agents endpoint, plus a 400 from the probe being
misread as an empty 2xx.

These tests pin the response shape so any future change that strips the
body, swaps to StreamingResponse without flush, or otherwise drops bytes
fails loudly instead of leaving subagents invisible in the Agents page.

Also includes the regression tests for the hook-preregister silent merge
bug (2026-04-20): when two Claude Code subagents spawn within the 120
second merge window, the second subagent's self-register call used to
silently land on the first subagent's hook-preregister row (matched by
time window only, no name/description similarity check). The second
subagent became invisible under its chosen name. The matcher now
requires textual similarity (name equality / substring / token
overlap / hook slug appearing in body text) in addition to the time
window.
"""
import pytest
from datetime import datetime, timezone, timedelta


@pytest.mark.asyncio
async def test_register_endpoint_returns_body_with_required_keys(client, monkeypatch):
    """A minimal register POST returns a non-empty JSON body with the
    full mailbox contract the caller needs to self-bootstrap polling.
    """
    from routers import agents as agents_router
    monkeypatch.setattr(agents_router, "_save_agent_state", lambda: None)

    payload = {
        "name": "test_register_endpoint_returns_body_probe",
        "prompt": "regression probe for register endpoint body",
        "source": "claude-code",
    }
    resp = await client.post("/api/agents/register", json=payload)

    assert resp.status_code == 200, resp.text
    # The core empty-body regression guard. If a future change (e.g. a
    # StreamingResponse without flush, a middleware that strips
    # response.body, a background task that raises after sending
    # headers) drops the body, content-length will be 0 and this
    # assert catches it.
    assert int(resp.headers.get("content-length", "0")) > 0, (
        "register response had empty body; subagents would be invisible"
    )
    body = resp.json()
    assert body, "register response body decoded to falsy JSON"
    # Required keys the caller relies on to self-register and start
    # polling the mailbox. Missing any of these breaks the subagent
    # bootstrap path documented in agent_mailbox_instruction().
    for key in (
        "result",
        "source",
        "status",
        "mailbox_instruction",
        "mailbox_check_interval_seconds",
    ):
        assert key in body, f"register body missing {key!r}: {body}"
    # Sanity: the mailbox instruction should mention the agent by name
    # so subagents copy-paste the right curl, not a stale one.
    assert "test_register_endpoint_returns_body_probe" in body["mailbox_instruction"]
    assert body["status"] == "running"
    assert body["source"] == "claude-code"


@pytest.mark.asyncio
async def test_register_endpoint_returns_body_when_only_name_supplied(client, monkeypatch):
    """Even the absolute minimal payload (just ``name``) returns a
    non-empty body. Protects against a future validator tightening
    that accidentally 500s instead of 400ing.
    """
    from routers import agents as agents_router
    monkeypatch.setattr(agents_router, "_save_agent_state", lambda: None)

    resp = await client.post(
        "/api/agents/register",
        json={
            "name": "test_register_endpoint_returns_body_bare",
            "source": "claude-code",
        },
    )

    # On this branch the register handler accepts a bare name. Whichever
    # status the endpoint chooses, the body must not be empty.
    assert int(resp.headers.get("content-length", "0")) > 0, (
        f"register returned status={resp.status_code} with empty body"
    )
    # Must be valid JSON, not a silent 200 with b"" or a truncated
    # chunk from a broken StreamingResponse.
    body = resp.json()
    assert isinstance(body, dict) and body, (
        f"register returned empty/non-dict JSON: {body!r}"
    )


# ---------------------------------------------------------------------------
# Hook-preregister silent-merge bug regression tests (2026-04-20).
#
# Bug: two subagents spawned within 120 seconds produce two hook-preregister
# rows. The second subagent's self-register call used to silently land on
# the first subagent's hook row because _find_recent_hook_preregister
# matched by time window alone. The second subagent became invisible under
# its chosen name.
#
# Fix: the matcher now requires textual similarity (exact name match /
# name substring / hook slug appearing in body text / >=2 shared non-stop
# tokens) in addition to the time window.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_unrelated_registers_in_rapid_succession_stay_separate(
    client, monkeypatch,
):
    """Two Claude Code hook-preregister rows are written back-to-back,
    then each subagent self-registers under its own (unrelated) name.
    Both self-registers MUST land on their own row, never silently
    merge into the other subagent's hook row.

    This reproduces the demo-blocking bug exactly: before the fix the
    second self-register was absorbed into the first hook row, and the
    second subagent was invisible under its chosen name.
    """
    from routers import agents as agents_router
    monkeypatch.setattr(agents_router, "_save_agent_state", lambda: None)

    # Clean slate for the two hook rows and the two self-register names.
    for n in (
        "fix-testagents-ordering-pollution",
        "refactor-chat-ack-cache-ttl",
        "diagnose-probe-agent-a6e99475",
        "diagnose-probe-agent-b7c88912",
    ):
        agents_router.agent_metadata.pop(n, None)
        agents_router.agent_aliases.pop(n, None)

    spawned_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    # Hook A: first Task-description slug.
    agents_router.agent_metadata["fix-testagents-ordering-pollution"] = {
        "spawned_at": spawned_at,
        "last_heartbeat_at": spawned_at,
        "source": "claude-code",
        "status": "running",
        "model": "claude-sonnet-4-6",
        "description": "fix testagents ordering pollution",
        "prompt": "sort tests so module fixtures do not leak between runs",
        "budget": "5",
        "hook_preregister": True,
    }
    # Hook B: second Task-description slug, unrelated topic.
    agents_router.agent_metadata["refactor-chat-ack-cache-ttl"] = {
        "spawned_at": spawned_at,
        "last_heartbeat_at": spawned_at,
        "source": "claude-code",
        "status": "running",
        "model": "claude-sonnet-4-6",
        "description": "refactor chat ack cache ttl",
        "prompt": "lower the chat ack bot cache ttl from 300 to 60",
        "budget": "5",
        "hook_preregister": True,
    }

    # Subagent A's self-register under a completely unrelated probe name.
    # Its description does not share any content tokens with EITHER hook
    # row. Without the fix it would silently merge into the newest hook
    # row (refactor-chat-ack-cache-ttl) because both fall inside the 120
    # second window. With the fix it must create its own row.
    resp_a = await client.post(
        "/api/agents/register",
        json={
            "name": "diagnose-probe-agent-a6e99475",
            "source": "claude-code",
            "status": "running",
            "description": "probe whether register endpoint merges blindly",
            "prompt": "emit a tracer payload",
        },
    )
    assert resp_a.status_code == 200, resp_a.text
    body_a = resp_a.json()
    assert "merged_into" not in body_a, (
        "first unrelated self-register must not merge into any hook row; "
        f"got merged_into={body_a.get('merged_into')!r} body={body_a}"
    )
    assert "diagnose-probe-agent-a6e99475" in agents_router.agent_metadata, (
        "first unrelated self-register must create its own row"
    )

    # Subagent B's self-register, also unrelated to either hook row.
    resp_b = await client.post(
        "/api/agents/register",
        json={
            "name": "diagnose-probe-agent-b7c88912",
            "source": "claude-code",
            "status": "running",
            "description": "second probe to catch the back-to-back merge bug",
            "prompt": "confirm separate row emerges",
        },
    )
    assert resp_b.status_code == 200, resp_b.text
    body_b = resp_b.json()
    assert "merged_into" not in body_b, (
        "second unrelated self-register must not merge into any hook row; "
        f"got merged_into={body_b.get('merged_into')!r} body={body_b}"
    )
    assert "diagnose-probe-agent-b7c88912" in agents_router.agent_metadata, (
        "second unrelated self-register must create its own row"
    )

    # Sanity: both hook rows still exist, untouched, so neither subagent
    # poached the other's slot.
    assert "fix-testagents-ordering-pollution" in agents_router.agent_metadata
    assert "refactor-chat-ack-cache-ttl" in agents_router.agent_metadata


@pytest.mark.asyncio
async def test_matching_name_register_merges_into_hook_preregister(
    client, monkeypatch,
):
    """When the self-register body's name matches the hook-preregister
    row name (or is a substring / superset of it), merging still works.
    The textual-similarity guard must not break the legitimate merge path
    the alias system was built for.
    """
    from routers import agents as agents_router
    monkeypatch.setattr(agents_router, "_save_agent_state", lambda: None)

    hook_name = "refactor-sidebar-keyboard-shortcuts"
    self_name = "refactor-sidebar-keyboard-shortcuts-v2"

    for n in (hook_name, self_name):
        agents_router.agent_metadata.pop(n, None)
        agents_router.agent_aliases.pop(n, None)

    spawned_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    agents_router.agent_metadata[hook_name] = {
        "spawned_at": spawned_at,
        "last_heartbeat_at": spawned_at,
        "source": "claude-code",
        "status": "running",
        "model": "claude-sonnet-4-6",
        "description": "refactor sidebar keyboard shortcuts",
        "prompt": "",
        "budget": "5",
        "hook_preregister": True,
    }

    # Subagent chose a -v2 suffix. The hook name is a substring of the
    # self-register name, so the matcher must still accept the merge.
    resp = await client.post(
        "/api/agents/register",
        json={
            "name": self_name,
            "source": "claude-code",
            "status": "running",
            "description": "refactor sidebar keyboard shortcuts take two",
            "prompt": "move bindings to a central hook",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("merged_into") == hook_name, (
        "matching-name self-register must merge into the hook row; "
        f"got body={body}"
    )
    assert self_name not in agents_router.agent_metadata, (
        "merged self-register name must not create a second row"
    )
    assert agents_router.agent_aliases.get(self_name) == hook_name


@pytest.mark.asyncio
async def test_matching_description_register_merges_into_hook_preregister(
    client, monkeypatch,
):
    """When names diverge but the descriptions share enough content
    tokens (the real-world case where the hook slugs the Task
    description and the subagent hand-writes its own Name but keeps
    the same Task wording in its description), the merge still works.
    """
    from routers import agents as agents_router
    monkeypatch.setattr(agents_router, "_save_agent_state", lambda: None)

    hook_name = "investigate-slow-dashboard-render"
    self_name = "dashboard-perf-probe-42"

    for n in (hook_name, self_name):
        agents_router.agent_metadata.pop(n, None)
        agents_router.agent_aliases.pop(n, None)

    spawned_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    agents_router.agent_metadata[hook_name] = {
        "spawned_at": spawned_at,
        "last_heartbeat_at": spawned_at,
        "source": "claude-code",
        "status": "running",
        "model": "claude-sonnet-4-6",
        "description": "investigate slow dashboard render",
        "prompt": "profile the dashboard query path and propose a fix",
        "budget": "5",
        "hook_preregister": True,
    }

    resp = await client.post(
        "/api/agents/register",
        json={
            "name": self_name,
            "source": "claude-code",
            "status": "running",
            "description": "investigate why the dashboard render is slow",
            "prompt": "profile render and propose fixes",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Shared content tokens between hook and body: {investigate, slow,
    # dashboard, render, profile, propose, fix} -> >=2, so merge.
    assert body.get("merged_into") == hook_name, (
        "description-overlap self-register must merge into the hook row; "
        f"got body={body}"
    )
    assert agents_router.agent_aliases.get(self_name) == hook_name


@pytest.mark.asyncio
async def test_response_body_never_leaks_unrelated_merged_into(
    client, monkeypatch,
):
    """Direct assertion on the bug symptom: the response body returned
    to a fresh unrelated /register POST must never carry a ``merged_into``
    key pointing at an unrelated hook row. This is the check a subagent
    would run to detect the absorption: "my name is X, but the response
    says merged_into=Y, and Y has nothing to do with me."
    """
    from routers import agents as agents_router
    monkeypatch.setattr(agents_router, "_save_agent_state", lambda: None)

    stale_hook = "unrelated-hook-row-for-merge-leak-test"
    self_name = "probe-that-should-never-merge-into-above"

    for n in (stale_hook, self_name):
        agents_router.agent_metadata.pop(n, None)
        agents_router.agent_aliases.pop(n, None)

    spawned_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    agents_router.agent_metadata[stale_hook] = {
        "spawned_at": spawned_at,
        "last_heartbeat_at": spawned_at,
        "source": "claude-code",
        "status": "running",
        "model": "claude-sonnet-4-6",
        "description": "completely different topic about gmail labels",
        "prompt": "apply label vacation-autoreply to last week threads",
        "budget": "5",
        "hook_preregister": True,
    }

    resp = await client.post(
        "/api/agents/register",
        json={
            "name": self_name,
            "source": "claude-code",
            "status": "running",
            "description": "diagnose workflow builder link navigation glitch",
            "prompt": "check react router wiring",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("merged_into") is None, (
        "unrelated register must not return a stale merged_into pointer; "
        f"got merged_into={body.get('merged_into')!r}"
    )
