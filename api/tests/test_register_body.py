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
    # After the subagent-name-wins inversion, the merged row is rekeyed
    # under the subagent's name so GET /api/agents surfaces it under the
    # real name. The old hook-slug key is removed from agent_metadata
    # and aliased forward so stale callers still resolve.
    assert self_name in agents_router.agent_metadata, (
        "merged row must be rekeyed under the subagent's name so the "
        "Agents page can render row.name as the real subagent name"
    )
    assert hook_name not in agents_router.agent_metadata, (
        "old hook-slug key must be removed to avoid a duplicate row"
    )
    assert agents_router.agent_aliases.get(hook_name) == self_name


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
    # Post-inversion: the merged row is rekeyed under self_name (the
    # subagent's chosen name) and the hook slug is aliased to the new
    # key so later calls to either name still resolve.
    assert self_name in agents_router.agent_metadata
    assert hook_name not in agents_router.agent_metadata
    assert agents_router.agent_aliases.get(hook_name) == self_name


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


@pytest.mark.asyncio
async def test_hook_preregister_plus_subagent_register_keeps_subagent_visible(
    client, monkeypatch,
):
    """Regression: when the PreToolUse hook preregisters a row under the Task
    description slug AND the subagent itself then POSTs /api/agents/register
    under a different, prompt-derived name, the subagent's chosen name MUST
    remain reachable via GET /api/agents (either as its own row or via the
    agent_aliases map).

    Demo-blocker: torios's headline feature is the Agents page lighting up
    live as soon as a Task-spawned subagent starts. A naive merge that
    silently absorbs the subagent's real name into the hook preregister row
    hides the subagent from the UI under an unexpected slug. This test pins
    the invariant so a future reintroduction of the aggressive merge does
    not regress visibility.
    """
    from routers import agents as agents_router
    monkeypatch.setattr(agents_router, "_save_agent_state", lambda: None)

    hook_name = "diagnose-subagent-visibility-gap"
    real_name = "agent-visibility-probe-xyz"
    for n in (hook_name, real_name):
        agents_router.agent_metadata.pop(n, None)
        agents_router.agent_aliases.pop(n, None)

    # Step 1: PreToolUse hook preregisters under the Task description slug
    # (this is what .claude/hooks/register-agent.sh does today).
    r1 = await client.post(
        "/api/agents/register",
        json={
            "name": hook_name,
            "source": "claude-code",
            "status": "running",
            "hook_preregister": True,
            "description": "Diagnose subagent visibility gap",
            "prompt": "diagnose why Task-spawned subagents are invisible",
            "budget": 5,
        },
    )
    assert r1.status_code == 200, r1.text

    # Step 2: the subagent itself then registers under its own,
    # prompt-derived name. This is the call the subagent's internal
    # register-yourself instruction issues (see agent_mailbox_instruction).
    r2 = await client.post(
        "/api/agents/register",
        json={
            "name": real_name,
            "source": "claude-code",
            "status": "running",
            "description": "visibility probe subagent",
            "prompt": "diagnose why Task-spawned subagents are invisible",
            "budget": 5,
        },
    )
    assert r2.status_code == 200, r2.text

    # Step 3: the subagent's row MUST be reachable. Either it remains as
    # its own row (no merge happened) OR it was merged into the hook row
    # and an alias maps its chosen name back. Both satisfy visibility.
    r3 = await client.get("/api/agents")
    assert r3.status_code == 200, r3.text
    payload = r3.json()
    rows = payload.get("agents", payload) if isinstance(payload, dict) else payload
    names = {row.get("name") for row in rows if isinstance(row, dict)}

    # Stronger invariant: the subagent's real name must appear as a row's
    # name field in the list response, not only as an alias key. The UI
    # (app/src/pages/Agents.tsx) renders row.name directly and has no
    # awareness of the alias map. If the list only surfaces the hook slug
    # name, the subagent is invisible to the user even though the alias
    # map could technically resolve the name server-side.
    assert real_name in names, (
        f"Subagent's chosen name {real_name!r} is NOT the name of any row "
        f"returned by GET /api/agents. The UI renders row.name and will "
        f"show only the hook-preregister slug, hiding the subagent. "
        f"Visible names sample: {sorted(n for n in names if n)[:10]} "
        f"aliases: {dict(getattr(agents_router, 'agent_aliases', {}) or {})}"
    )


# ---------------------------------------------------------------------------
# ->2986: same-second spawns cross each other's status text
#
# Root cause: when two hook rows share the same spawned_at (the common case
# when two agents start in the same shell second) AND both agents' token sets
# overlap enough with BOTH hook rows (shared words like "fix", "tests"),
# _find_recent_hook_preregister returned whichever row came first in dict
# iteration order for BOTH agents. Each agent then merged into the wrong hook
# row and showed the other's task description.  Heartbeats updated different
# dicts so they didn't literally mirror, but the visible task description was
# swapped, producing the "same status text" symptom.
#
# Fix: _hook_preregister_score returns a numeric score (exact name > substring
# > slug > token overlap count). When spawned_at values are equal the row with
# the HIGHER score wins, ensuring each agent merges into its own hook row.
#
# Secondary fix: the re-register path no longer preserves hook_preregister on
# an incoming call that is not itself a hook preregister. Without this, an
# agent whose self-name matches its hook slug would keep hook_preregister=True
# on its live row, making it look like an unclaimed target to later scans.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_second_spawns_merge_into_correct_hook_rows(
    client, monkeypatch,
):
    """Two hook rows with identical spawned_at each match BOTH agents via
    token overlap (shared words "fix" and "tests"), but hook-A matches
    agent-A more strongly (4 tokens vs 2) and hook-B matches agent-B more
    strongly (4 tokens vs 2). After both self-registers, each agent must have
    merged into its OWN hook row, not the other's.

    Before the fix: dict insertion order decided the winner for same-timestamp
    ties, so if hook-B was inserted first both agents would merge into hook-B.
    After the fix: similarity score breaks the tie correctly.
    """
    from routers import agents as agents_router
    monkeypatch.setattr(agents_router, "_save_agent_state", lambda: None)

    hook_a = "fix-broken-tests-perf-2982"
    hook_b = "fix-broken-tests-coverage-2983"
    agent_a = "saa-2982-fix-perf"
    agent_b = "saa-2983-fix-coverage"

    for n in (hook_a, hook_b, agent_a, agent_b):
        agents_router.agent_metadata.pop(n, None)
        agents_router.agent_aliases.pop(n, None)

    # Same spawned_at for both hooks (the shell-second collision scenario).
    spawned_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()

    # Insert hook-B FIRST so it would win under the old iteration-order logic.
    agents_router.agent_metadata[hook_b] = {
        "spawned_at": spawned_at,
        "last_heartbeat_at": spawned_at,
        "source": "claude-code",
        "status": "running",
        "description": "fix broken tests coverage 2983",
        "prompt": "fix the broken tests and coverage gap for task 2983",
        "budget": "5",
        "hook_preregister": True,
    }
    agents_router.agent_metadata[hook_a] = {
        "spawned_at": spawned_at,
        "last_heartbeat_at": spawned_at,
        "source": "claude-code",
        "status": "running",
        "description": "fix broken tests perf 2982",
        "prompt": "fix the broken tests and performance regression for task 2982",
        "budget": "5",
        "hook_preregister": True,
    }

    # Agent A: strongly matches hook-A (tokens: fix, broken, tests, perf, 2982)
    # and weakly matches hook-B (tokens: fix, broken, tests shared = 3, but
    # hook-A shares fix+broken+tests+perf+2982 = 5 tokens).
    resp_a = await client.post(
        "/api/agents/register",
        json={
            "name": agent_a,
            "source": "claude-code",
            "status": "running",
            "description": "fix broken tests perf regression for 2982",
            "prompt": "fix the perf regression and broken tests in task 2982",
        },
    )
    assert resp_a.status_code == 200, resp_a.text
    body_a = resp_a.json()
    assert body_a.get("merged_into") == hook_a, (
        f"agent-A must merge into its own hook row {hook_a!r}; "
        f"got merged_into={body_a.get('merged_into')!r}"
    )

    # Agent B: strongly matches hook-B (tokens: fix, broken, tests, coverage, 2983).
    resp_b = await client.post(
        "/api/agents/register",
        json={
            "name": agent_b,
            "source": "claude-code",
            "status": "running",
            "description": "fix broken tests coverage gap for 2983",
            "prompt": "fix the coverage gap and broken tests in task 2983",
        },
    )
    assert resp_b.status_code == 200, resp_b.text
    body_b = resp_b.json()
    assert body_b.get("merged_into") == hook_b, (
        f"agent-B must merge into its own hook row {hook_b!r}; "
        f"got merged_into={body_b.get('merged_into')!r}"
    )

    # Heartbeat each agent and confirm steps stay separate.
    agents_router.agent_metadata[agent_a]["current_step"] = None
    agents_router.agent_metadata[agent_b]["current_step"] = None

    agents_router.agent_metadata[agent_a]["current_step"] = "step-from-agent-a"
    agents_router.agent_metadata[agent_b]["current_step"] = "step-from-agent-b"

    assert agents_router.agent_metadata[agent_a]["current_step"] == "step-from-agent-a", (
        "agent-A current_step must be independent of agent-B"
    )
    assert agents_router.agent_metadata[agent_b]["current_step"] == "step-from-agent-b", (
        "agent-B current_step must be independent of agent-A"
    )
    # Rows must point at DIFFERENT dict objects.
    assert (
        agents_router.agent_metadata[agent_a]
        is not agents_router.agent_metadata[agent_b]
    ), "agent-A and agent-B must not share the same metadata dict"


@pytest.mark.asyncio
async def test_self_register_over_hook_slug_clears_hook_preregister_flag(
    client, monkeypatch,
):
    """When an agent self-registers under the SAME name as its hook-preregister
    row (so the merge guard is skipped and the re-register path runs), the
    hook_preregister flag must be CLEARED on the resulting row. Without this
    fix, the flag was preserved via the old 'elif existing.get(hook_preregister)'
    clause, making the live row look like an unclaimed hook target for any
    subsequent _find_recent_hook_preregister scan. Fixes ->2986.
    """
    from routers import agents as agents_router
    monkeypatch.setattr(agents_router, "_save_agent_state", lambda: None)

    name = "fix-perf-2982-self-register-test"
    agents_router.agent_metadata.pop(name, None)
    agents_router.agent_aliases.pop(name, None)

    spawned_at = (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat()
    # Hook preregisters under the exact same name the agent will use.
    agents_router.agent_metadata[name] = {
        "spawned_at": spawned_at,
        "last_heartbeat_at": spawned_at,
        "source": "claude-code",
        "status": "running",
        "description": "fix perf regression 2982",
        "hook_preregister": True,
    }

    # Agent self-registers under the same name. This hits the re-register
    # path (body.name IS in agent_metadata), so no merge happens.
    resp = await client.post(
        "/api/agents/register",
        json={
            "name": name,
            "source": "claude-code",
            "status": "running",
            "description": "fix perf regression 2982",
        },
    )
    assert resp.status_code == 200, resp.text

    row = agents_router.agent_metadata.get(name, {})
    assert not row.get("hook_preregister"), (
        "hook_preregister flag must be cleared after a subagent "
        "self-registers over its own hook row; a True value makes the "
        "live row a spurious merge target for concurrent agents"
    )
