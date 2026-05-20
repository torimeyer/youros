"""Wave 2 tests for the unified Specs flow.

Covers:

- POST /api/specs/draft auto-promotes the new plan once AI has written
  acceptance criteria (the user never has to click Promote).
- POST /api/specs/{path}/build runs decompose first when the plan has
  no linked tasks, then spawns builders in the same call.
- POST /api/specs/{path}/unlock moves a ready plan back to draft so the
  user can edit the acceptance criteria.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_create_draft_auto_promotes_when_ac_generation_succeeds(
    client, tmp_path, monkeypatch
):
    """When AI writes acceptance criteria, the new plan should land in ready.

    The Wave 2 UX: a user types a plan title and presses New Plan. The
    backend drafts the file, generates AC, then immediately promotes so
    the page lands on a ready plan with a single Build it button.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path / "docs" / "spec")

    draft_file = tmp_path / "docs" / "draft" / "wave2-autopromote.md"
    spec_file = tmp_path / "docs" / "spec" / "wave2-autopromote.md"

    async def fake_run(*args, **kwargs):
        if args[:2] == ("doc", "draft"):
            draft_file.write_text(
                "---\ntitle: wave2 autopromote\nstatus: draft\n---\n\n"
            )
            return str(draft_file.relative_to(tmp_path))
        raise AssertionError(f"unexpected ostk call: {args}")

    monkeypatch.setattr(ostk_module.ostk, "_run", fake_run)

    # Stub the AI call to simulate a successful AC generation. Instead
    # of mocking Anthropic itself we patch _resolve_api_key to return
    # None; the draft route then skips the real API call but we still
    # need acceptance criteria on disk for promote to succeed. We
    # simulate the AI path by monkeypatching the anthropic client.
    class FakeMessages:
        async def create(self, **_kw):
            class Content:
                text = (
                    "## What we want\n"
                    "Automate the Promote click away.\n\n"
                    "## Acceptance criteria\n"
                    "- [ ] Draft is created\n"
                    "- [ ] AI writes AC\n"
                    "- [ ] Plan lands in ready\n"
                )

            class Resp:
                content = [Content()]

            return Resp()

    class FakeClient:
        def __init__(self, **_kw):
            self.messages = FakeMessages()

    monkeypatch.setattr(
        "services.chat_providers._resolve_api_key",
        AsyncMock(return_value="fake-key"),
    )
    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)

    resp = await client.post(
        "/api/specs/draft", json={"title": "wave2 autopromote", "kind": "spec"}
    )
    assert resp.status_code == 200
    data = resp.json()
    # The new plan lands already promoted so the UI renders Build it
    # immediately with no Promote step in between.
    assert data["status"] == "ready"
    assert data["promoted_path"] is not None
    # The file moved from draft/ to spec/.
    assert spec_file.exists()
    assert not draft_file.exists()


@pytest.mark.asyncio
async def test_create_draft_leaves_as_draft_when_ac_generation_fails(
    client, tmp_path, monkeypatch
):
    """No AI key -> no AC written -> the plan stays as a draft.

    This is the graceful-fallback path. The user can hand-edit the
    checklist and promote manually.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path / "docs" / "spec")

    draft_file = tmp_path / "docs" / "draft" / "wave2-no-ac.md"

    async def fake_run(*args, **kwargs):
        if args[:2] == ("doc", "draft"):
            draft_file.write_text(
                "---\ntitle: wave2 no ac\nstatus: draft\n---\n\n"
            )
            return str(draft_file.relative_to(tmp_path))
        raise AssertionError(f"unexpected ostk call: {args}")

    monkeypatch.setattr(ostk_module.ostk, "_run", fake_run)

    # No API key -> the route skips the AI branch entirely.
    monkeypatch.setattr(
        "services.chat_providers._resolve_api_key",
        AsyncMock(return_value=None),
    )

    resp = await client.post(
        "/api/specs/draft", json={"title": "wave2 no ac", "kind": "spec"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "draft"
    assert data["promoted_path"] is None
    # The file stays in draft/.
    assert draft_file.exists()
    # After the →1463 fix lands, the draft must also have a placeholder checkbox.
    assert "- [ ]" in draft_file.read_text(), (
        "No-API-key path must write placeholder AC checkboxes so the spinner clears (→1463)."
    )


@pytest.mark.asyncio
async def test_create_draft_no_api_key_writes_placeholder_not_stuck(
    client, tmp_path, monkeypatch
):
    """No API key (subscription auth) must write placeholder AC, not leave
    the draft with an empty body that causes the infinite spinner (→1463).

    RED test: fails before the fix lands. After the fix, the draft file
    must contain at least one '- [ ]' checkbox so the frontend spinner
    resolves immediately instead of spinning forever.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path / "docs" / "spec")

    draft_file = tmp_path / "docs" / "draft" / "pattern-watcher-v2.md"

    async def fake_run(*args, **kwargs):
        if args[:2] == ("doc", "draft"):
            draft_file.write_text(
                "---\ntitle: Pattern watcher v2\nstatus: draft\n---\n\n"
            )
            return str(draft_file.relative_to(tmp_path))
        # doc_promote is pure-Python, doesn't hit _run
        raise AssertionError(f"unexpected ostk call: {args}")

    monkeypatch.setattr(ostk_module.ostk, "_run", fake_run)

    # Subscription auth: _resolve_api_key returns empty string (not None).
    monkeypatch.setattr(
        "services.chat_providers._resolve_api_key",
        AsyncMock(return_value=""),
    )

    resp = await client.post(
        "/api/specs/draft", json={"title": "Pattern watcher v2", "kind": "spec"}
    )
    assert resp.status_code == 200
    data = resp.json()
    # Draft stays as draft (placeholder, not auto-promoted — user edits first).
    assert data["status"] == "draft"
    assert data["promoted_path"] is None
    assert draft_file.exists()
    # CRITICAL: draft must have at least one checkbox so the frontend
    # "Generating acceptance criteria..." spinner can resolve.
    draft_text = draft_file.read_text()
    assert "- [ ]" in draft_text, (
        "No-API-key path must write placeholder AC checkboxes. "
        "Without them the Specs page spinner shows forever (→1463)."
    )


@pytest.mark.asyncio
async def test_build_auto_decomposes_when_plan_has_no_tasks(
    client, tmp_path, monkeypatch
):
    """One-click Build it: decompose first, then spawn builders.

    Simulates a freshly-promoted plan that has no linked tasks yet. The
    build endpoint should run decompose, re-fetch the agent configs,
    and spawn one builder per task in the same call.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "wave2-build-one-click.md"
    spec_file.write_text(
        "---\n"
        "title: wave2 build one click\n"
        "status: spec\n"
        "---\n\n"
        "- [ ] One\n- [ ] Two\n"
    )

    # Track call order so we can assert decompose ran before the
    # second spec_build sweep.
    decompose_calls = {"count": 0}
    build_calls = {"count": 0}

    async def fake_spec_build(path):
        build_calls["count"] += 1
        # First call: plan has no tasks yet.
        if build_calls["count"] == 1:
            return {"agents": []}
        # Second call (after decompose): plan has agents ready.
        return {
            "agents": [
                {
                    "name": "spec-wave2-901",
                    "task_id": "901",
                    "task_title": "One",
                    "prompt": "Build task 901",
                },
                {
                    "name": "spec-wave2-902",
                    "task_id": "902",
                    "task_title": "Two",
                    "prompt": "Build task 902",
                },
            ]
        }

    async def fake_doc_decompose(path, auto=False):
        decompose_calls["count"] += 1
        return {"result": "ok", "task_ids": ["901", "902"]}

    monkeypatch.setattr(ostk_module.ostk, "spec_build", fake_spec_build)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", fake_doc_decompose)

    # Stub the per-task spawn call so we do not actually launch agents.
    spawned_names: list[str] = []

    async def fake_spawn_agent(body):
        spawned_names.append(body.name)
        return {"agent": body.name}

    import routers.agents as agents_router

    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    resp = await client.post(
        "/api/specs/docs/spec/wave2-build-one-click.md/build"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agents"] == ["spec-wave2-901", "spec-wave2-902"]
    # Decompose was called exactly once, as part of the Build it flow.
    assert decompose_calls["count"] == 1
    # spec_build was called twice: once to check for tasks (empty),
    # once after decompose to grab the agent configs.
    assert build_calls["count"] == 2
    assert spawned_names == ["spec-wave2-901", "spec-wave2-902"]


@pytest.mark.asyncio
async def test_build_spawns_builders_in_parallel_and_assigns_tasks_up_front(
    client, tmp_path, monkeypatch
):
    """Build it fans out spawns in parallel and records task assignments
    before spawn_agent returns, so the demo shows progress in under three
    seconds even with slow per-spawn setup.

    The regression targets two past bugs:
      1. Serial for-loop spawning meant N slow subprocess starts stacked
         up end to end, which blew the ~3 s demo budget.
      2. Task assignments were only written AFTER await spawn_agent
         returned, so GET /specs/{path}/tasks showed assigned_agent=None
         on every poll while the subprocess warmed up.
    This test simulates three slow spawns and asserts the total wall
    time is close to a single spawn (parallel), and that assignments
    land before spawn_agent resolves.
    """
    import asyncio
    import time
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "parallel-build.md"
    spec_file.write_text(
        "---\ntitle: parallel\nstatus: spec\n---\n\n- [ ] a\n"
    )

    agent_configs = [
        {"name": "spec-parallel-a", "task_id": "1001",
         "task_title": "A", "prompt": "build A"},
        {"name": "spec-parallel-b", "task_id": "1002",
         "task_title": "B", "prompt": "build B"},
        {"name": "spec-parallel-c", "task_id": "1003",
         "task_title": "C", "prompt": "build C"},
    ]

    async def fake_spec_build(path):
        return {"agents": agent_configs}

    monkeypatch.setattr(ostk_module.ostk, "spec_build", fake_spec_build)

    # Observed assignments at the exact moment the spawn coroutine is
    # first scheduled (not after it completes). If the route records
    # _task_assignments up front, all three should be visible on each
    # call. Each fake spawn sleeps 200 ms to simulate subprocess setup.
    observed_assignments: list[dict] = []
    call_count = {"n": 0}

    async def fake_spawn(body):
        call_count["n"] += 1
        # Snapshot assignments the instant we enter the spawn. Parallel
        # dispatch means all three entries should be present on all
        # three calls (or at minimum by the second call).
        observed_assignments.append(dict(specs_router._task_assignments))
        await asyncio.sleep(0.2)
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn)

    # Reset assignments so we measure this run only.
    specs_router._task_assignments.clear()

    t0 = time.perf_counter()
    resp = await client.post(
        "/api/specs/docs/spec/parallel-build.md/build"
    )
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200
    data = resp.json()
    assert sorted(data["agents"]) == [
        "spec-parallel-a", "spec-parallel-b", "spec-parallel-c",
    ]
    assert call_count["n"] == 3

    # Parallel: three 200 ms spawns should finish in ~0.2 s plus
    # per-task overhead. Budget 0.6 s here; serial would be 0.6 s
    # minimum (3 x 0.2) and almost always higher. The strict bound
    # catches any future regression back to a serial for-loop.
    assert elapsed < 0.5, (
        f"build took {elapsed:.2f}s; expected <0.5s with parallel spawn"
    )

    # Assignments recorded up front: each spawn coroutine stamps its
    # own task_id into _task_assignments BEFORE awaiting spawn_agent.
    # That means by the time the final spawn enters, every prior
    # assignment is already visible. The UI's /tasks poll only needs
    # to see the assignment for task K before spawn K completes, which
    # this guarantees because gather starts every coroutine before
    # awaiting any of them.
    #
    # Strongest observable guarantee: the last snapshot (taken just
    # before the final sleep) contains every assignment. The earlier
    # snapshots each contain their own assignment, and any that ran
    # before them. Serial for-loop code would NOT show all three at
    # the end before spawn_agent returned; it would record them one
    # at a time in the previous call's finally block.
    assert len(observed_assignments) == 3
    assert set(observed_assignments[-1].keys()) == {"1001", "1002", "1003"}
    assert specs_router._task_assignments["1001"] == "spec-parallel-a"
    assert specs_router._task_assignments["1002"] == "spec-parallel-b"
    assert specs_router._task_assignments["1003"] == "spec-parallel-c"


@pytest.mark.asyncio
async def test_unlock_moves_spec_back_to_draft(client, tmp_path, monkeypatch):
    """Unlock and edit: ready plan returns to draft with status flipped."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "wave2-unlock.md"
    spec_file.write_text(
        "---\n"
        "title: wave2 unlock\n"
        "status: spec\n"
        "promoted_at: 2026-04-17T00:00:00Z\n"
        "---\n\n"
        "- [ ] One\n"
    )

    resp = await client.post("/api/specs/docs/spec/wave2-unlock.md/unlock")
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "docs/draft/wave2-unlock.md"

    draft_file = tmp_path / "docs" / "draft" / "wave2-unlock.md"
    assert draft_file.exists()
    assert not spec_file.exists()
    text = draft_file.read_text()
    assert "status: draft" in text
    # promoted_at is dropped on unlock.
    assert "promoted_at:" not in text
    # Body survives the demote.
    assert "- [ ] One" in text


@pytest.mark.asyncio
async def test_unlock_rejects_non_spec_paths(client, tmp_path, monkeypatch):
    """Unlock only applies to ready plans; drafts and bad paths fail cleanly."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    draft_file = tmp_path / "docs" / "draft" / "already-draft.md"
    draft_file.write_text(
        "---\ntitle: already draft\nstatus: draft\n---\n\n- [ ] One\n"
    )

    resp = await client.post(
        "/api/specs/docs/draft/already-draft.md/unlock"
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_templates_endpoint_returns_migrated_fleets(client):
    """GET /api/specs/templates returns the starter plan templates.

    The migration covers every fleet that used to live on the Agents
    page. The endpoint must include "Build a Website" plus the
    non-fleet templates we added to prove the service stands on its own.
    """
    resp = await client.get("/api/specs/templates")
    assert resp.status_code == 200
    data = resp.json()
    templates = data["templates"]
    ids = [t["id"] for t in templates]
    # Migrated fleet templates.
    assert "build-a-website" in ids
    assert "product-launch" in ids
    assert "research-report" in ids
    # At least two new templates that were never fleets.
    assert "weekly-review-writeup" in ids
    assert "launch-announcement" in ids
    # Each template carries the fields the Plans page grid needs.
    for t in templates:
        assert {"id", "name", "description", "icon"}.issubset(t.keys())
        assert isinstance(t.get("acceptance_criteria", []), list)
        assert isinstance(t.get("tasks", []), list)


@pytest.mark.asyncio
async def test_from_template_creates_ready_plan(
    client, tmp_path, monkeypatch
):
    """POST /specs/from-template drafts, appends, and auto-promotes.

    The resulting plan lands in ready state with the template's goal
    body and acceptance criteria checklist on disk. Decompose is NOT
    fired by this endpoint; it runs later inside the Build it flow.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path / "docs" / "spec")

    draft_file = tmp_path / "docs" / "draft" / "build-a-website.md"
    spec_file = tmp_path / "docs" / "spec" / "build-a-website.md"

    decompose_calls = {"count": 0}

    async def fake_run(*args, **kwargs):
        if args[:2] == ("doc", "draft"):
            draft_file.write_text(
                "---\ntitle: Build a Website\nstatus: draft\n---\n\n"
            )
            return str(draft_file.relative_to(tmp_path))
        if args[:2] == ("doc", "decompose"):
            decompose_calls["count"] += 1
            return ""
        raise AssertionError(f"unexpected ostk call: {args}")

    monkeypatch.setattr(ostk_module.ostk, "_run", fake_run)

    resp = await client.post(
        "/api/specs/from-template",
        json={"template_id": "build-a-website", "kind": "spec"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["promoted_path"] is not None
    assert data["template_id"] == "build-a-website"

    # File moved draft -> spec, and the body contains the template's
    # goal plus the acceptance criteria checklist.
    assert spec_file.exists()
    assert not draft_file.exists()
    body = spec_file.read_text()
    assert "What we want" in body
    # Checklist items are written as unchecked boxes so the ready plan
    # displays them in the acceptance criteria section.
    assert "- [ ]" in body

    # Decompose is NOT fired by this endpoint. It runs later when the
    # user clicks Build it, which matches the Wave 2 create_draft path.
    assert decompose_calls["count"] == 0


@pytest.mark.asyncio
async def test_from_template_unknown_template_id_returns_404(client):
    """Unknown template ids return 404 with a helpful detail message."""
    resp = await client.post(
        "/api/specs/from-template",
        json={"template_id": "does-not-exist"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_from_roadmap_line_creates_ready_plan(
    client, tmp_path, monkeypatch
):
    """Happy path: a valid roadmap path and an initiative string produce a ready plan.

    The endpoint must draft a new plan, append acceptance criteria from
    the AI call, and auto promote the draft to ready. The response
    carries the title and the promoted path so the frontend can route
    the user to the new spec.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path / "docs" / "spec")

    roadmap_path = tmp_path / "roadmap.md"
    roadmap_path.write_text(
        "---\nkind: roadmap\n---\n\n# Roadmap\n\n- Ship guided onboarding for solo PMs\n"
    )

    draft_file = tmp_path / "docs" / "draft" / "ship-guided-onboarding-for-solo-pms.md"
    spec_file = tmp_path / "docs" / "spec" / "ship-guided-onboarding-for-solo-pms.md"

    async def fake_run(*args, **kwargs):
        if args[:2] == ("doc", "draft"):
            draft_file.write_text(
                "---\ntitle: Ship guided onboarding for solo PMs\nstatus: draft\n---\n\n"
            )
            return str(draft_file.relative_to(tmp_path))
        raise AssertionError(f"unexpected ostk call: {args}")

    monkeypatch.setattr(ostk_module.ostk, "_run", fake_run)

    class FakeMessages:
        async def create(self, **_kw):
            class Content:
                text = (
                    "## What we want\n"
                    "Guide new users through their first plan.\n\n"
                    "## Acceptance criteria\n"
                    "- [ ] Onboarding wizard shows 3 steps\n"
                    "- [ ] Dismiss button works\n"
                    "- [ ] Completion tracked\n"
                )

            class Resp:
                content = [Content()]

            return Resp()

    class FakeClient:
        def __init__(self, **_kw):
            self.messages = FakeMessages()

    monkeypatch.setattr(
        "services.chat_providers._resolve_api_key",
        AsyncMock(return_value="fake-key"),
    )
    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)

    resp = await client.post(
        "/api/specs/from-roadmap-line",
        json={
            "roadmap_path": str(roadmap_path),
            "initiative_text": "Ship guided onboarding for solo PMs",
            "kind": "spec",
        },
    )
    # The endpoint now returns as soon as the draft file exists and the
    # AC generation is scheduled. The response status is "draft" (not
    # "ready") because the AC + auto-promote runs in a background task,
    # which is the whole point of the speedup: Tori's "Generating
    # acceptance criteria..." spinner used to block on the Anthropic
    # round-trip (2-8 s). Now it resolves in under 500 ms and the AC
    # shows up on the next Specs-page poll.
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "draft"
    assert data["promoted_path"] is None
    assert data["title"].startswith("Ship guided onboarding")

    # Wait for the background AC task to land. Poll for up to ~2 s for
    # the spec file (post-promote destination). The fake Anthropic
    # returns immediately so one or two event-loop ticks is enough in
    # practice; the loop just guards against scheduler jitter in CI.
    import asyncio as _asyncio
    for _ in range(40):
        if spec_file.exists():
            break
        await _asyncio.sleep(0.05)
    assert spec_file.exists(), "background AC task did not finish in time"
    body = spec_file.read_text()
    assert "From roadmap" in body
    assert "roadmap.md" in body
    assert "Ship guided onboarding for solo PMs" in body
    assert "- [ ]" in body


@pytest.mark.asyncio
async def test_from_roadmap_line_404_on_missing_roadmap(client):
    """A roadmap path that does not exist returns 404 with a plain message.

    Frontend cannot recover from a missing roadmap, so the endpoint
    rejects the call before touching ostk.
    """
    resp = await client.post(
        "/api/specs/from-roadmap-line",
        json={
            "roadmap_path": "/tmp/does-not-exist-roadmap-xyz.md",
            "initiative_text": "Some initiative",
        },
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()
    assert "does-not-exist" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_verify_fires_spec_complete_notification_when_all_ac_met(
    client, tmp_path, monkeypatch
):
    """Regression: when verify determines all acceptance criteria are met,
    the backend must fire a single bell notification ("Spec done") so the
    user knows the spec hit complete without having to refresh the Specs
    page. Dedup target prevents duplicate bells on repeat verifies.
    """
    from services import ostk as ostk_module

    monkeypatch.setattr(
        ostk_module.ostk,
        "spec_verify",
        AsyncMock(return_value={
            "all_met": True,
            "results": [{"text": "AC 1", "met": True}],
            "task_summary": {"closed": 3, "open": 0},
        }),
    )

    notif_calls: list[dict] = []

    class _FakeNotif:
        def add(self, **kwargs):
            notif_calls.append(kwargs)
            return None

    with patch(
        "services.notifications.notifications_service", _FakeNotif()
    ):
        resp = await client.post("/api/specs/docs/spec/my-plan.md/verify")

    assert resp.status_code == 200
    assert resp.json()["all_met"] is True
    assert len(notif_calls) == 1, (
        f"verify must fire exactly one notification when all AC met, "
        f"got {len(notif_calls)}"
    )
    call = notif_calls[0]
    assert call["type"] == "spec_complete"
    # The title switched from "Spec done" to "Your feature is live" so
    # the TopBar toast reads as a celebration of the feature rather than
    # a status update on a doc. The string also matches the release
    # notes modal's header, so the three surfaces (toast, modal, bell)
    # all say the same thing.
    assert call["title"] == "Your feature is live"
    assert call["target"] == "spec_complete:docs/spec/my-plan.md"
    assert "docs/spec/my-plan.md" in call.get("action_url", "")


@pytest.mark.asyncio
async def test_verify_does_not_notify_when_ac_not_all_met(
    client, tmp_path, monkeypatch
):
    """Counter-test: if verify determines ANY acceptance criteria is
    unmet, no notification fires. Otherwise the bell would ring on every
    intermediate verify during a build in progress.
    """
    from services import ostk as ostk_module

    monkeypatch.setattr(
        ostk_module.ostk,
        "spec_verify",
        AsyncMock(return_value={
            "all_met": False,
            "results": [
                {"text": "AC 1", "met": True},
                {"text": "AC 2", "met": False},
            ],
            "task_summary": {"closed": 1, "open": 2},
        }),
    )

    notif_calls: list[dict] = []

    class _FakeNotif:
        def add(self, **kwargs):
            notif_calls.append(kwargs)
            return None

    with patch(
        "services.notifications.notifications_service", _FakeNotif()
    ):
        resp = await client.post("/api/specs/docs/spec/my-plan.md/verify")

    assert resp.status_code == 200
    assert resp.json()["all_met"] is False
    assert notif_calls == [], (
        f"verify must NOT fire notifications when AC unmet, got {notif_calls}"
    )


@pytest.mark.asyncio
async def test_auto_advancer_fires_spec_complete_notification_on_last_task_close(
    tmp_path, monkeypatch
):
    """Regression: when the LAST builder task for a spec closes and the
    auto-advancer flips the spec's frontmatter to ``status: complete``,
    the backend must also emit the ``spec_complete`` persistent
    notification. Without this emit, a clean Build-it run that never
    calls ``/verify`` lands silently: the frontend toast only fires off
    a persistent notification row, the release-notes modal watcher can
    be defeated if the spec file gets cleaned up before the watcher's
    next 2s poll, and Tori ends up with a feature that shipped without
    her knowing. This test exercises the exact code path that was
    silent on the 2026-04-21 demo run.
    """
    from routers import specs as specs_router
    from services import ostk as ostk_module

    # Arrange: a spec file that already lives on disk and one builder
    # task recorded in _spec_task_origin. The task is the one being
    # flipped to closed; list_tasks returns it as closed so the
    # advancer considers every sibling done and proceeds to flip the
    # spec's frontmatter.
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    spec_path = "docs/spec/silent-landing.md"
    spec_file = tmp_path / spec_path
    spec_file.write_text(
        "---\n"
        "title: Silent landing\n"
        "status: in-progress\n"
        "linked_tasks: [\"7777\"]\n"
        "---\n\n"
        "## Acceptance criteria\n\n"
        "- [ ] Feature ships\n"
    )
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    # Populate the per-process map the advancer reads. In production
    # this is seeded by /specs/{path}/build when the builder is spawned.
    specs_router._spec_task_origin.clear()
    specs_router._spec_task_origin["7777"] = spec_path
    monkeypatch.setattr(
        ostk_module.ostk,
        "list_tasks",
        AsyncMock(return_value=[{"id": "7777", "status": "closed"}]),
    )

    notif_calls: list[dict] = []

    class _FakeNotif:
        def add(self, **kwargs):
            notif_calls.append(kwargs)
            return None

    # Act: run the advancer exactly as tasks.close_task does at the
    # end of a successful Build-it.
    with patch(
        "services.notifications.notifications_service", _FakeNotif()
    ):
        result = await specs_router._advance_spec_status_if_all_builder_tasks_closed_async(
            "7777"
        )

    # Assert: status flipped AND the feature-live notification fired.
    assert result == spec_path, (
        "advancer must return the spec path when it successfully flips "
        "status so callers can chain further side effects"
    )
    assert "status: complete" in spec_file.read_text()
    assert len(notif_calls) == 1, (
        f"auto-advancer must fire exactly one spec_complete notification "
        f"when the final builder task closes, got {len(notif_calls)}"
    )
    call = notif_calls[0]
    assert call["type"] == "spec_complete"
    assert call["title"] == "Your feature is live"
    assert call["target"] == f"spec_complete:{spec_path}"
    assert spec_path in call.get("action_url", "")


@pytest.mark.asyncio
async def test_auto_advancer_does_not_notify_when_some_tasks_still_open(
    tmp_path, monkeypatch
):
    """Counter-test: the auto-advancer must NOT fire a notification while
    any sibling builder task is still open. Otherwise partway through a
    build (two of three tasks closed) Tori would see a spurious "Your
    feature is live" toast.
    """
    from routers import specs as specs_router
    from services import ostk as ostk_module

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    spec_path = "docs/spec/partial.md"
    spec_file = tmp_path / spec_path
    spec_file.write_text(
        "---\ntitle: Partial\nstatus: in-progress\n"
        "linked_tasks: [\"8001\", \"8002\"]\n---\n"
    )
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    specs_router._spec_task_origin.clear()
    specs_router._spec_task_origin["8001"] = spec_path
    specs_router._spec_task_origin["8002"] = spec_path
    monkeypatch.setattr(
        ostk_module.ostk,
        "list_tasks",
        AsyncMock(return_value=[
            {"id": "8001", "status": "closed"},
            {"id": "8002", "status": "open"},
        ]),
    )

    notif_calls: list[dict] = []

    class _FakeNotif:
        def add(self, **kwargs):
            notif_calls.append(kwargs)
            return None

    with patch(
        "services.notifications.notifications_service", _FakeNotif()
    ):
        result = await specs_router._advance_spec_status_if_all_builder_tasks_closed_async(
            "8001"
        )

    assert result is None, "advancer must not return a flipped path when any sibling task is still open"
    assert "status: in-progress" in spec_file.read_text()
    assert notif_calls == [], (
        "advancer must not fire spec_complete while siblings are still open"
    )


@pytest.mark.asyncio
async def test_auto_advancer_is_noop_when_spec_already_complete(
    tmp_path, monkeypatch
):
    """Regression for the random 'Your feature is live' modal.

    If a builder agent's /complete fires twice (reconnect, retry) or the
    advancer is triggered after the spec is already marked complete,
    it must NOT rewrite the file (which would bump mtime and re-arm the
    60s grace window) and must NOT fire a second spec_complete notification
    (which would create a new bell row once the user reads the first one,
    re-triggering the modal).
    """
    from routers import specs as specs_router
    from services import ostk as ostk_module

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    spec_path = "docs/spec/already-done.md"
    spec_file = tmp_path / spec_path
    original_text = (
        "---\n"
        "title: Already done\n"
        "status: complete\n"
        "linked_tasks: [\"9001\"]\n"
        "---\n\n"
        "## Acceptance criteria\n\n"
        "- [x] Feature shipped\n"
    )
    spec_file.write_text(original_text)
    original_mtime = spec_file.stat().st_mtime

    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    specs_router._spec_task_origin.clear()
    specs_router._spec_task_origin["9001"] = spec_path
    monkeypatch.setattr(
        ostk_module.ostk,
        "list_tasks",
        AsyncMock(return_value=[{"id": "9001", "status": "closed"}]),
    )

    notif_calls: list[dict] = []

    class _FakeNotif:
        def add(self, **kwargs):
            notif_calls.append(kwargs)
            return None

    with patch("services.notifications.notifications_service", _FakeNotif()):
        result = await specs_router._advance_spec_status_if_all_builder_tasks_closed_async(
            "9001"
        )

    assert result is None, (
        "advancer must return None (no-op) when the spec is already complete"
    )
    assert spec_file.stat().st_mtime == original_mtime, (
        "advancer must not touch the spec file when it is already complete "
        "(file mtime bump re-arms the 60s grace window and causes spurious modals)"
    )
    assert notif_calls == [], (
        "advancer must not fire a second spec_complete notification when the "
        "spec is already complete (would create a new bell row once the user "
        "reads the first one, re-triggering the modal)"
    )
    assert spec_file.read_text() == original_text, (
        "advancer must not modify file contents when spec is already complete"
    )


@pytest.mark.asyncio
async def test_compute_spec_status_in_progress_when_frontmatter_complete_but_task_open():
    """Regression for the 3-tasks-1-open bug.

    Scenario: a spec has three builder tasks. Two close cleanly, but a
    third gets stranded in the ``open`` state because concurrent
    ``issues.jsonl`` rewrites clobbered its close. The auto-advancer
    still ran at an instant when list_tasks briefly showed all three
    closed and wrote ``status: complete`` to the frontmatter.

    If ``compute_spec_status`` trusts the frontmatter unconditionally,
    the UI banner keeps saying "Done. Every task closed and the feature
    is live." with a still-open task sitting right above it. The fixed
    predicate must re-verify against the live task-status map and return
    ``in-progress`` whenever at least one linked task is open, even when
    the frontmatter says ``complete``.
    """
    from services.ostk import OstkService

    # Three tasks, one still open. Frontmatter on disk lies: it says
    # complete because a past auto-advancer fired on a stale view.
    status = OstkService.compute_spec_status(
        "complete",
        ["832", "833", "834"],
        {"832": "open", "833": "closed", "834": "closed"},
    )
    assert status == "in-progress", (
        "A spec whose frontmatter says complete but has one open task "
        "must compute as in-progress so the UI banner does not claim "
        "Done with a still-open task above it."
    )

    # Same idea for the legacy ``done`` vocabulary.
    status_done = OstkService.compute_spec_status(
        "done",
        ["832", "833", "834"],
        {"832": "open", "833": "closed", "834": "closed"},
    )
    assert status_done == "in-progress"

    # Sanity: when every task IS closed, frontmatter=complete still
    # yields complete, so the happy path did not regress.
    happy = OstkService.compute_spec_status(
        "complete",
        ["832", "833", "834"],
        {"832": "closed", "833": "closed", "834": "closed"},
    )
    assert happy == "complete"


@pytest.mark.asyncio
async def test_close_task_concurrent_calls_do_not_lose_any_close(tmp_path):
    """Regression: three parallel close_task calls must all land.

    Before the lock was added, three concurrent spec-builder completions
    each kicked off an ``ostk work close`` subprocess AND a Python-side
    rewrite of ``issues.jsonl``. The overlapping read-modify-writes on
    the jsonl clobbered each other and at least one task survived as
    ``open`` even though its builder ran to completion. This test seeds
    three open tasks, fires three ``close_task`` calls via
    ``asyncio.gather``, and asserts all three land closed.
    """
    import asyncio as _asyncio
    from services.ostk import OstkService

    # Build a minimal ostk-shaped workspace. close_task writes
    # ``closed_reason`` via direct jsonl rewrite, and shells out to
    # ``ostk work close <id>`` for the actual status flip. We stub the
    # shell step with a fake ``_run`` that does the same jsonl mutation
    # the real CLI would (status -> closed), so the test exercises the
    # EXACT overlap that used to lose writes.
    needles = tmp_path / ".ostk" / "needles"
    needles.mkdir(parents=True)
    issues = needles / "issues.jsonl"
    issues.write_text(
        '{"id": "→832", "status": "open"}\n'
        '{"id": "→833", "status": "open"}\n'
        '{"id": "→834", "status": "open"}\n'
    )

    svc = OstkService(cwd=str(tmp_path))

    async def _fake_run(*args, **_kwargs):
        # Mirror what ``ostk work close <id>`` does on disk: flip the
        # matching row's status to closed with a slow read-modify-write
        # so three parallel calls can race without the lock. The sleep
        # inside the critical section guarantees the race is reliable
        # in the unlocked version of the code.
        if args[:2] == ("work", "close"):
            target = svc._normalize_task_id(args[2])
            text = issues.read_text()
            # Simulate the small amount of compute the real CLI does
            # between read and write. Gives the other coroutines a
            # chance to read the stale copy and clobber us.
            await _asyncio.sleep(0.01)
            out_lines: list[str] = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                import json as _json
                entry = _json.loads(line)
                if svc._normalize_task_id(entry.get("id")) == target:
                    entry["status"] = "closed"
                out_lines.append(_json.dumps(entry, ensure_ascii=False))
            issues.write_text("\n".join(out_lines) + "\n")
            return "closed"
        raise AssertionError(f"unexpected ostk call: {args}")

    svc._run = _fake_run  # type: ignore[method-assign]

    # Act: close all three tasks in parallel, same code path as three
    # spec-builder agents finishing at once.
    await _asyncio.gather(
        svc.close_task("832", closed_reason="completed"),
        svc.close_task("833", closed_reason="completed"),
        svc.close_task("834", closed_reason="completed"),
    )

    # Assert: every row is closed AND every row has closed_reason set.
    # The Python-side rewrite must also find arrow-prefixed ids when
    # the caller passes a bare id.
    import json as _json
    rows = [
        _json.loads(line)
        for line in issues.read_text().splitlines()
        if line.strip()
    ]
    by_id = {r["id"]: r for r in rows}
    for tid in ("→832", "→833", "→834"):
        assert by_id[tid]["status"] == "closed", (
            f"{tid} must close even under concurrent close_task calls"
        )
        assert by_id[tid].get("closed_reason") == "completed", (
            f"{tid} must have closed_reason recorded even when the "
            f"stored id is arrow-prefixed and the caller passed a bare id"
        )


# ---------------------------------------------------------------------------
# AC-generation prompt guardrails
# ---------------------------------------------------------------------------


def test_ac_prompt_lists_already_shipped_features():
    """Regression: the AC-generation prompt MUST surface what myOS already
    ships so the LLM does not propose acceptance criteria for features
    that already exist (like 'users can connect their own LLM API keys'
    for a chat-integration spec). Grounding the prompt stops duplicate
    proposals at the source.
    """
    from routers.specs import _ac_generation_prompt

    prompt = _ac_generation_prompt("Improve direct LLM integration with myOS")

    # The ships block must name the obvious candidates the LLM kept
    # proposing as net-new: multi-model chat, API key management, the
    # core integrations list.
    assert "ALREADY SHIPS" in prompt
    assert "Multi-model chat" in prompt
    assert "API-key management" in prompt
    assert "Gmail" in prompt and "Calendar" in prompt


def test_ac_prompt_caps_at_three_criteria():
    """The prompt must cap AC output at 3 criteria. More than that and a
    live-demo Build cannot finish inside 90 seconds. The prior prompt said
    '4-6 criteria total' which drove sprawl.
    """
    from routers.specs import _ac_generation_prompt

    prompt = _ac_generation_prompt("Some feature title")

    assert "Exactly 3 criteria" in prompt
    # Must not ask for more than 3.
    assert "4-6" not in prompt
    assert "4 to 6" not in prompt


def test_ac_prompt_excludes_openai_and_chatgpt():
    """Regression: Tori only ships Claude + Gemini in chat. The prompt
    must explicitly tell the model not to propose OpenAI/ChatGPT
    additions. Without this, the LLM proposes 'integration works with
    OpenAI, Anthropic, Google' for any chat-adjacent initiative.
    """
    from routers.specs import _ac_generation_prompt

    prompt = _ac_generation_prompt("Improve direct LLM integration with myOS")

    # The exclusion clause must be explicit.
    assert "Do NOT propose adding" in prompt
    assert "OpenAI" in prompt
    assert "ChatGPT" in prompt
    # And it must name the two in-scope providers.
    assert "Claude" in prompt and "Gemini" in prompt


def test_ac_prompt_from_roadmap_reframes_subject():
    """The from_roadmap variant should call the subject an 'initiative
    from a roadmap', so the LLM knows the input was roadmap-scoped (not
    a user-typed spec title) and stays more incremental.
    """
    from routers.specs import _ac_generation_prompt

    prompt = _ac_generation_prompt(
        "Roadmap initiative text", from_roadmap=True
    )

    assert "initiative from a roadmap" in prompt
    # Still carries the shared grounding.
    assert "ALREADY SHIPS" in prompt
    assert "Exactly 3 criteria" in prompt


@pytest.mark.asyncio
async def test_spec_counts_returns_unfinished_and_total(
    client, tmp_path, monkeypatch
):
    """GET /api/specs/counts returns unfinished = ready + in_progress only.

    Anchors the Sidebar badge semantics for the →1561 3-stage model:
    ready and in_progress count as unfinished; draft does not. Specs that
    meet the auto-archive condition (formerly "complete") are moved off the
    board before list_docs returns them, so they do not affect either count.
    If this ever drifts the Sidebar badge will diverge from the Specs page.
    """
    from services import ostk as ostk_module

    async def fake_list_docs():
        return [
            {"path": "docs/draft/a.md", "status": "draft"},
            {"path": "docs/spec/b.md", "status": "ready"},
            {"path": "docs/spec/c.md", "status": "in-progress"},
        ]

    monkeypatch.setattr(ostk_module.ostk, "list_docs", fake_list_docs)

    res = await client.get("/api/specs/counts")
    assert res.status_code == 200
    body = res.json()
    # →1561: unfinished = ready + in_progress only; draft not counted
    assert body["total"] == 3
    assert body["unfinished"] == 2  # ready + in_progress; draft excluded
    assert "by_stage" in body


@pytest.mark.asyncio
async def test_spec_counts_zero_when_no_specs(
    client, tmp_path, monkeypatch
):
    """An empty workspace returns zero for both counts (badge hides)."""
    from services import ostk as ostk_module

    async def fake_list_docs():
        return []

    monkeypatch.setattr(ostk_module.ostk, "list_docs", fake_list_docs)

    res = await client.get("/api/specs/counts")
    assert res.status_code == 200
    body = res.json()
    assert body["unfinished"] == 0
    assert body["total"] == 0
    assert "by_stage" in body


# ---------------------------------------------------------------------------
# Latency regression: AC-drafting model + route orchestration budget
# ---------------------------------------------------------------------------
#
# Backstory: the live demo measured POST /specs/from-roadmap-line at
# 9 to 12 seconds for roadmap bullets with longer or more ambiguous
# text (for example "Improve direct LLM integration with myOS"),
# versus 4.8 s for the snappy baseline. Raw-call timing pinned the
# blame on the Sonnet 4.5 model used to draft acceptance criteria:
# p50 ~4.5 s per call, with a tail that blew past 10 s on the demo's
# slowest subject. Switching the AC-drafting step to Haiku 4.5 cut
# p50 to ~2.3 s and p95 to well under 5 s while producing equivalent
# output for the 3-item checklist shape we ask for.
#
# Two tests here, both isolated from the real Anthropic API:
#   1. Assert AC_DRAFT_MODEL is a Haiku model. Guards against a drift
#      back to Sonnet for this specific drafting step.
#   2. Run from-roadmap-line three times back to back with a mocked
#      LLM and assert the p95 route-orchestration time is under 1.5 s.
#      That covers every non-LLM cost: ostk subprocess draft + promote,
#      FastAPI dispatch, pydantic, file I/O, prompt template build.
#      Serial back-to-back calls + fast mock preserves any accidental
#      O(N) overhead from a future regression (a per-call file scan,
#      a repeat settings reload, a sync DB write, etc.).


def test_ac_draft_model_is_haiku_for_demo_latency():
    """Guard: AC-drafting must use Haiku so p95 stays under 5 s.

    Sonnet-4.5 measured 4 to 5 s per AC call with a tail that pushed
    total route time to 12 s on one demo bullet. Haiku-4.5 produces
    the same 3-item checklist shape in ~2.3 s. Anyone who flips this
    back to Sonnet for "quality" needs to re-measure the full route
    first or the demo will drift back over the 5 s target.
    """
    from routers import specs as specs_router

    assert "haiku" in specs_router.AC_DRAFT_MODEL.lower(), (
        f"AC_DRAFT_MODEL={specs_router.AC_DRAFT_MODEL!r} must be a Haiku "
        "variant to keep the spec-from-roadmap path under the 5 s demo "
        "target. Do not switch this back to Sonnet without re-running "
        "the e2e latency probe."
    )


@pytest.mark.asyncio
async def test_from_roadmap_line_p95_under_demo_budget(
    client, tmp_path, monkeypatch
):
    """Regression: three back-to-back from-roadmap-line calls must land
    the p95 under 1.5 s of non-LLM orchestration.

    The real demo threshold is p95 < 5 s end to end. That budget covers
    the Haiku LLM call (measured ~2.3 s p50, under 3 s at p95) plus the
    route's orchestration overhead. This test mocks the Anthropic client
    so the measurement isolates orchestration cost: any regression that
    adds per-call file scanning, a sync DB write, or a cold SDK import
    will push this test's p95 above 1.5 s even though the mock returns
    instantly.

    The three subjects match the e2e demo probe ("Improve direct LLM
    integration with myOS", "Recent specs widget on the dashboard",
    "Multi model side by side chat answers") so the length-dependent
    paths that surfaced the original bug stay covered.
    """
    import asyncio
    import time

    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path / "docs" / "spec")

    roadmap_path = tmp_path / "roadmap.md"
    roadmap_path.write_text(
        "---\nkind: roadmap\n---\n\n# Roadmap\n\n"
        "- Improve direct LLM integration with myOS\n"
        "- Recent specs widget on the dashboard\n"
        "- Multi model side by side chat answers\n"
    )

    # Counter so we can hand back a unique draft file per call and
    # simulate ostk's filename-deduping without actually shelling out.
    call_counter = {"n": 0}

    async def fake_run(*args, **kwargs):
        if args[:2] == ("doc", "draft"):
            call_counter["n"] += 1
            name = f"latency-probe-{call_counter['n']}.md"
            path = tmp_path / "docs" / "draft" / name
            path.write_text("---\ntitle: latency probe\nstatus: draft\n---\n\n")
            return str(path.relative_to(tmp_path))
        raise AssertionError(f"unexpected ostk call: {args}")

    monkeypatch.setattr(ostk_module.ostk, "_run", fake_run)

    class FakeMessages:
        async def create(self, **_kw):
            class Content:
                text = (
                    "## What we want\n"
                    "Short plan body.\n\n"
                    "## Acceptance criteria\n"
                    "- [ ] Criterion one\n"
                    "- [ ] Criterion two\n"
                    "- [ ] Criterion three\n"
                )

            class Resp:
                content = [Content()]

            return Resp()

    class FakeClient:
        def __init__(self, **_kw):
            self.messages = FakeMessages()

    monkeypatch.setattr(
        "services.chat_providers._resolve_api_key",
        AsyncMock(return_value="fake-key"),
    )
    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)

    subjects = [
        "Improve direct LLM integration with myOS",
        "Recent specs widget on the dashboard",
        "Multi model side by side chat answers",
    ]

    durations: list[float] = []
    for subject in subjects:
        t0 = time.perf_counter()
        resp = await client.post(
            "/api/specs/from-roadmap-line",
            json={
                "roadmap_path": str(roadmap_path),
                "initiative_text": subject,
                "kind": "spec",
            },
        )
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200, resp.text
        # Endpoint now returns status="draft" immediately and promotes
        # to "spec" in a background task. This is the speedup the user
        # feels: the POST resolves in sub-200 ms instead of blocking
        # 2-8 s on the Anthropic call.
        assert resp.json()["status"] == "draft"
        durations.append(elapsed)

    # p95 of three samples is the max. Tighter budget now that the
    # Anthropic call is out of the request path: orchestration is
    # just doc_draft + file header write + create_task, well under
    # 500 ms even under load.
    p95 = max(durations)
    p50 = sorted(durations)[1]
    assert p95 < 1.5, (
        f"spec-from-roadmap-line orchestration regressed: p50={p50*1000:.0f}ms "
        f"p95={p95*1000:.0f}ms samples={[f'{d*1000:.0f}ms' for d in durations]}. "
        "Budget is 1.5 s. With AC generation now running as a background "
        "task, the synchronous path is just doc_draft + file write + "
        "task scheduling. Investigate any new per-call file scan, "
        "settings reload, or sync write before raising this threshold."
    )


@pytest.mark.asyncio
async def test_delete_spec_sweeps_builder_tasks_it_spawned(
    client, tmp_path, monkeypatch
):
    """Deleting a spec deletes every builder task that spec spawned.

    Regression for the demo-residue bug: after a Build it run, the 2 or
    more builder agent tasks (one per acceptance criterion) stayed on
    the Tasks page even after the spec file itself was deleted. The
    cleanup pass only swept the spec file, never the task rows, so every
    demo ended with 6 orphan tasks (three specs x two tasks). Fix:
    delete_spec now sweeps _spec_task_origin for any task id tied to the
    spec being deleted and calls ostk.delete_task on each.

    The test exercises the full path: build the spec (records the map),
    then delete the spec (drains the map + fires delete_task). Asserts
    on both the response payload and the observed delete_task calls so
    a future refactor that drops either side of the mapping fails loudly.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_rel = "docs/spec/cleanup-residue.md"
    spec_file = tmp_path / spec_rel
    spec_file.write_text(
        "---\ntitle: cleanup residue\nstatus: spec\n---\n\n"
        "- [ ] Alpha\n- [ ] Beta\n"
    )

    async def fake_spec_build(path):
        return {
            "agents": [
                {
                    "name": "spec-cleanup-901",
                    "task_id": "\u2192901",
                    "task_title": "Alpha",
                    "prompt": "Build alpha",
                },
                {
                    "name": "spec-cleanup-902",
                    "task_id": "\u2192902",
                    "task_title": "Beta",
                    "prompt": "Build beta",
                },
            ]
        }

    async def fake_doc_decompose(path, auto=False):
        return {"result": "ok", "task_ids": ["901", "902"]}

    monkeypatch.setattr(ostk_module.ostk, "spec_build", fake_spec_build)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", fake_doc_decompose)

    # Record every task_id the delete path hands to ostk.
    deleted_task_ids: list[str] = []

    async def fake_delete_task(task_id):
        deleted_task_ids.append(task_id)
        return "deleted"

    monkeypatch.setattr(ostk_module.ostk, "delete_task", fake_delete_task)

    # Stub the spawn call. We do NOT care what the builder does; we just
    # need build_spec to populate _spec_task_origin before we delete.
    async def fake_spawn_agent(body):
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    # Clear the module-level maps so a previous test cannot leak origin
    # rows into this one and produce a false pass.
    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    build = await client.post(f"/api/specs/{spec_rel}/build")
    assert build.status_code == 200, build.text
    assert build.json()["agents"] == ["spec-cleanup-901", "spec-cleanup-902"]
    # Both tasks were recorded against this spec path.
    assert specs_router._spec_task_origin == {
        "901": spec_rel,
        "902": spec_rel,
    }

    # Now delete the spec. This must sweep BOTH builder rows.
    delete = await client.request(
        "DELETE", f"/api/specs/{spec_rel}"
    )
    assert delete.status_code == 200, delete.text
    payload = delete.json()
    assert payload["result"] == "deleted"
    # Response echoes which builder tasks got swept so the caller can log it.
    assert sorted(payload["cleaned_builder_tasks"]) == ["901", "902"]
    # And the ostk service actually received the delete calls.
    assert sorted(deleted_task_ids) == ["901", "902"]
    # After the sweep both maps are empty: the spec and its tasks are gone
    # together so a fresh Build it on the same path starts clean.
    assert specs_router._spec_task_origin == {}
    assert "901" not in specs_router._task_assignments
    assert "902" not in specs_router._task_assignments


@pytest.mark.asyncio
async def test_build_it_falls_back_to_ac_parsing_when_ostk_returns_empty(
    client, tmp_path, monkeypatch
):
    """When ostk.spec_build AND doc_decompose both return empty, Build
    it should parse the spec's unchecked AC bullets and create one
    builder task per bullet instead of surfacing the "no open tasks"
    message.

    This regression guards the user-reported bug: a Ready spec whose
    tasks have all been closed (or whose decompose silently produces
    nothing) used to hit the empty-message branch, leaving Tori with a
    dead Build it button. The fallback restores Build it as a reliable
    one click regardless of prior state.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/ac-fallback.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\n"
        "title: ac fallback\n"
        "status: spec\n"
        "---\n\n"
        "## What we want\nTest the fallback.\n\n"
        "## Acceptance criteria\n"
        "- [ ] First criterion\n"
        "- [ ] Second criterion\n"
        "- [ ] Third criterion\n"
    )

    async def empty_spec_build(path):
        return {"agents": []}

    async def empty_doc_decompose(path, auto=False):
        return {"result": "nothing created", "task_ids": []}

    monkeypatch.setattr(ostk_module.ostk, "spec_build", empty_spec_build)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", empty_doc_decompose)

    add_task_calls: list[dict] = []
    next_id = {"n": 7000}

    async def fake_add_task(title, priority="P1", description="", ac=""):
        next_id["n"] += 1
        add_task_calls.append({
            "title": title, "priority": priority,
            "description": description, "ac": ac,
        })
        return f"\u2192{next_id['n']} {title}"

    monkeypatch.setattr(ostk_module.ostk, "add_task", fake_add_task)

    spawned: list[str] = []

    async def fake_spawn_agent(body):
        spawned.append(body.name)
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    # Clean state so assertions below only see this run's work.
    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post(f"/api/specs/{spec_path_rel}/build")
    assert resp.status_code == 200
    data = resp.json()

    # Three AC bullets -> three add_task calls -> three spawned builders.
    assert len(add_task_calls) == 3
    assert [c["title"] for c in add_task_calls] == [
        "First criterion", "Second criterion", "Third criterion",
    ]
    assert len(spawned) == 3
    assert len(data["agents"]) == 3
    # Assignments are recorded for every created task.
    assert len(specs_router._spec_task_origin) == 3
    for tid, sp in specs_router._spec_task_origin.items():
        assert sp == spec_path_rel
        assert tid in specs_router._task_assignments

    # The spec's frontmatter was updated with the new task ids so
    # subsequent Build it clicks do not double-create.
    updated_text = spec_file.read_text()
    assert "tasks:" in updated_text
    assert '"7001"' in updated_text
    assert '"7002"' in updated_text
    assert '"7003"' in updated_text


@pytest.mark.asyncio
async def test_build_it_ac_fallback_parses_real_ostk_work_add_output(
    client, tmp_path, monkeypatch
):
    """Real ostk emits ``added →NNN: title`` with the arrow mid-line.
    The old AC-fallback parser used re.match (anchored at line start) and
    silently dropped every id, so the cascade returned zero configs and
    the handler lied with "no unchecked acceptance criteria" even though
    the spec had three. This regression pins the live-CLI output shape so
    the parser must use re.search (or equivalent) to recover the id.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/real-ostk-output.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\ntitle: real ostk output\nstatus: spec\n---\n\n"
        "## Acceptance criteria\n"
        "- [ ] Bullet one\n"
        "- [ ] Bullet two\n"
    )

    async def empty_spec_build(path):
        return {"agents": []}

    async def empty_doc_decompose(path, auto=False):
        return {"result": "", "task_ids": []}

    monkeypatch.setattr(ostk_module.ostk, "spec_build", empty_spec_build)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", empty_doc_decompose)

    next_id = {"n": 8000}

    async def fake_add_task(title, priority="P1", description="", ac=""):
        next_id["n"] += 1
        # Match real CLI output exactly: "added →NNN: title [P1]"
        return f"added →{next_id['n']}: {title} [{priority}]"

    monkeypatch.setattr(ostk_module.ostk, "add_task", fake_add_task)

    async def fake_spawn_agent(body):
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post(f"/api/specs/{spec_path_rel}/build")
    assert resp.status_code == 200
    data = resp.json()

    # The bug: handler returned has_unchecked_acs=False with the
    # "add at least one" message even though two bullets exist.
    assert data.get("has_unchecked_acs") is not False, (
        "AC fallback failed to parse task ids from real ostk output. "
        f"Response: {data}"
    )
    assert len(data["agents"]) == 2
    assert '"8001"' in spec_file.read_text()
    assert '"8002"' in spec_file.read_text()


@pytest.mark.asyncio
async def test_build_it_happy_path_still_uses_ostk_spec_build(
    client, tmp_path, monkeypatch
):
    """The AC-parsing fallback must NOT fire when ostk.spec_build
    already returns agent configs. Otherwise we would double-create
    tasks every time the happy path runs.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/happy-path.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\n"
        "title: happy path\n"
        "status: spec\n"
        "tasks:\n"
        '  - "5001"\n'
        '  - "5002"\n'
        "---\n\n"
        "## Acceptance criteria\n"
        "- [ ] Alpha\n"
        "- [ ] Beta\n"
    )

    async def ok_spec_build(path):
        return {
            "agents": [
                {"name": "spec-happy-5001", "task_id": "5001",
                 "task_title": "Alpha", "prompt": "build alpha"},
                {"name": "spec-happy-5002", "task_id": "5002",
                 "task_title": "Beta", "prompt": "build beta"},
            ]
        }

    monkeypatch.setattr(ostk_module.ostk, "spec_build", ok_spec_build)

    add_task_fired = {"n": 0}

    async def fake_add_task(title, priority="P1", description="", ac=""):
        add_task_fired["n"] += 1
        return f"\u21929999 {title}"

    monkeypatch.setattr(ostk_module.ostk, "add_task", fake_add_task)

    async def fake_spawn_agent(body):
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post(f"/api/specs/{spec_path_rel}/build")
    assert resp.status_code == 200
    data = resp.json()

    # Happy path: two agents spawned from spec_build, no fallback.
    assert sorted(data["agents"]) == ["spec-happy-5001", "spec-happy-5002"]
    assert add_task_fired["n"] == 0, (
        "fallback fired but ostk.spec_build already produced agents; "
        "this would double-create tasks on every Build it click"
    )


@pytest.mark.asyncio
async def test_build_it_ignores_gemini_default_model_and_uses_sonnet(
    client, tmp_path, monkeypatch
):
    """The chat ``default_model`` setting (e.g. ``@gemini``) must NOT
    leak into spec builder spawns. Builders run as ``claude --print``
    subprocesses which only accept Claude models; passing ``@gemini``
    makes the subprocess exit immediately with "There's an issue with
    the selected model (@gemini)" and a 142-byte transcript, which is
    exactly how Build it silently produced zero file edits on demo
    runs where the user picked Gemini in onboarding.
    """
    from services import ostk as ostk_module
    from services.settings_store import settings_store
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/model-leak.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\ntitle: model leak\nstatus: spec\n---\n\n"
        "## Acceptance criteria\n- [ ] Do a thing\n"
    )

    # Pretend the user chose Gemini in onboarding.
    monkeypatch.setattr(
        settings_store, "get",
        lambda key, default=None: "@gemini" if key == "default_model" else default,
    )

    async def empty_spec_build(path):
        return {"agents": []}

    async def empty_doc_decompose(path, auto=False):
        return {"result": "", "task_ids": []}

    monkeypatch.setattr(ostk_module.ostk, "spec_build", empty_spec_build)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", empty_doc_decompose)

    async def fake_add_task(title, priority="P1", description="", ac=""):
        return "added →9500: bullet [P1]"

    monkeypatch.setattr(ostk_module.ostk, "add_task", fake_add_task)

    spawn_bodies: list[object] = []

    async def capture_spawn(body):
        spawn_bodies.append(body)
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", capture_spawn)

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post(f"/api/specs/{spec_path_rel}/build")
    assert resp.status_code == 200
    assert len(spawn_bodies) == 1
    # The critical assertion: even though default_model is "@gemini",
    # the builder must be spawned with a Claude model so the claude
    # subprocess does not error out on an unknown --model arg.
    chosen = spawn_bodies[0].model
    assert "@" not in chosen, (
        f"spec builder got chat preference '{chosen}' instead of a "
        "Claude model. This would make the claude subprocess exit "
        "immediately with a bad-model error and produce no file edits."
    )
    assert "gemini" not in chosen.lower()


@pytest.mark.asyncio
async def test_close_spec_builder_task_closes_task_on_agent_complete(
    tmp_path, monkeypatch
):
    """When a spec-spawned builder calls /complete, the matching task
    should close automatically. The builder prompt explicitly tells the
    agent NOT to run ``ostk work close`` itself (the spec router closes
    the task for you via HTTP when you finish), so this path is the only
    thing that turns /complete into a closed task. Without it the
    Specs page shows "in progress" forever even after all builders are
    done, and the auto-flip to ``done`` never fires.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/auto-close.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\n"
        "title: auto close\n"
        "status: spec\n"
        "tasks:\n"
        '  - "9001"\n'
        '  - "9002"\n'
        "---\n\n"
        "## Acceptance criteria\n"
        "- [ ] One\n"
        "- [ ] Two\n"
    )

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()
    specs_router._task_assignments["9001"] = "spec-auto-9001"
    specs_router._task_assignments["9002"] = "spec-auto-9002"
    specs_router._spec_task_origin["9001"] = spec_path_rel
    specs_router._spec_task_origin["9002"] = spec_path_rel

    closed_calls: list[dict] = []
    task_store = {
        "9001": {"id": "→9001", "status": "open"},
        "9002": {"id": "→9002", "status": "open"},
    }

    async def fake_close_task(tid, closed_reason=None):
        norm = str(tid).lstrip("→")
        closed_calls.append({"tid": norm, "reason": closed_reason})
        if norm in task_store:
            task_store[norm]["status"] = "closed"
        return "closed"

    async def fake_list_tasks():
        return list(task_store.values())

    monkeypatch.setattr(ostk_module.ostk, "close_task", fake_close_task)
    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    # Non-spec agent: returns None, no close fires.
    assert await specs_router.close_spec_builder_task("random-non-spec") is None
    assert closed_calls == []

    # First builder completes: its task closes with reason=completed.
    # Spec must NOT flip yet because the second task is still open.
    result1 = await specs_router.close_spec_builder_task("spec-auto-9001")
    assert result1 == "9001"
    assert closed_calls == [{"tid": "9001", "reason": "completed"}]
    assert "status: spec" in spec_file.read_text()
    assert "status: complete" not in spec_file.read_text()

    # Second builder completes: last task closes, spec flips to done.
    result2 = await specs_router.close_spec_builder_task("spec-auto-9002")
    assert result2 == "9002"
    assert {c["tid"] for c in closed_calls} == {"9001", "9002"}
    assert "status: complete" in spec_file.read_text()


@pytest.mark.asyncio
async def test_spec_status_flips_to_done_when_all_builder_tasks_close(
    client, tmp_path, monkeypatch
):
    """After every builder task recorded for a spec closes, the spec's
    frontmatter ``status:`` should advance to ``done`` automatically so
    the Specs page lands on the build-complete state without waiting
    for a manual Verify click.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router
    from routers import tasks as tasks_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/status-flip.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\n"
        "title: status flip\n"
        "status: spec\n"
        "tasks:\n"
        '  - "8001"\n'
        '  - "8002"\n'
        "---\n\n"
        "## Acceptance criteria\n"
        "- [ ] do thing one\n"
        "- [ ] do thing two\n"
    )

    # Record that both tasks came from this spec (as build_spec would have).
    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()
    specs_router._spec_task_origin["8001"] = spec_path_rel
    specs_router._spec_task_origin["8002"] = spec_path_rel
    specs_router._task_assignments["8001"] = "spec-status-8001"
    specs_router._task_assignments["8002"] = "spec-status-8002"

    # Simulated task store. close_task flips a task's status in-place.
    task_store = {
        "8001": {"id": "\u21928001", "title": "do thing one", "status": "open"},
        "8002": {"id": "\u21928002", "title": "do thing two", "status": "open"},
    }

    async def fake_list_tasks():
        return list(task_store.values())

    async def fake_close_task(tid, closed_reason=None):
        norm = str(tid).lstrip("\u2192")
        if norm in task_store:
            task_store[norm]["status"] = "closed"
        return "closed"

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)
    # _isolate_tasks_ostk (conftest autouse) replaces routers.tasks.ostk with a
    # fresh OstkService instance, so we must patch close_task on tasks_router.ostk
    # (the replaced instance) rather than the original ostk_module.ostk singleton.
    monkeypatch.setattr(tasks_router.ostk, "close_task", fake_close_task)

    # Reset the burst-guard so sequential closes in this test do not 429.
    tasks_router._recent_closes.clear()

    # Close the first task: spec status must stay as 'spec' since one
    # builder is still open.
    resp1 = await client.post("/api/tasks/8001/close")
    assert resp1.status_code == 200
    assert "status: spec" in spec_file.read_text()
    assert "status: complete" not in spec_file.read_text()

    # Close the second (and final) task: the spec now flips to 'done'.
    resp2 = await client.post("/api/tasks/8002/close")
    assert resp2.status_code == 200
    updated = spec_file.read_text()
    assert "status: complete" in updated
    assert "status: spec" not in updated


@pytest.mark.asyncio
async def test_build_it_always_creates_tasks_for_unchecked_acs(
    client, tmp_path, monkeypatch
):
    """Build it must always create one task per unchecked AC, even when
    ostk.spec_build raises OstkError (ostk CLI missing or broken).

    Reproduces the user-reported regression where the red "no open
    tasks" banner fired whenever the ostk CLI was not installed on the
    machine. The AC fallback exists exactly for that environment, and
    the handler must not short-circuit to 404 before the fallback can
    run.
    """
    from services import ostk as ostk_module
    from services.ostk import OstkError
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/ostk-missing.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\n"
        "title: ostk missing\n"
        "status: spec\n"
        "---\n\n"
        "## Acceptance criteria\n"
        "- [ ] alpha\n"
        "- [ ] beta\n"
    )

    async def broken_spec_build(path):
        raise OstkError("ostk: command not found")

    async def broken_doc_decompose(path, auto=False):
        raise OstkError("ostk: command not found")

    monkeypatch.setattr(ostk_module.ostk, "spec_build", broken_spec_build)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", broken_doc_decompose)

    created: list[str] = []

    async def fake_add_task(title, priority="P1", description="", ac=""):
        created.append(title)
        return f"\u21924{len(created):03d} {title}"

    monkeypatch.setattr(ostk_module.ostk, "add_task", fake_add_task)

    spawned: list[str] = []

    async def fake_spawn_agent(body):
        spawned.append(body.name)
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post(f"/api/specs/{spec_path_rel}/build")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Even though ostk is unreachable, the AC fallback produced tasks.
    assert created == ["alpha", "beta"]
    assert len(data["agents"]) == 2
    assert len(spawned) == 2
    # Spec status flips to building so the UI stops rendering Ready.
    assert "status: building" in spec_file.read_text()


@pytest.mark.asyncio
async def test_build_it_spawns_one_builder_per_task(
    client, tmp_path, monkeypatch
):
    """Each created task must get its own builder subagent spawn.

    The user intent is "saa to build this": a spec with N unchecked
    ACs yields N tasks and N builder subagents, one per task. The
    agent-to-task map records each assignment so the Specs page can
    render a spinner per row.
    """
    from services import ostk as ostk_module
    from services.ostk import OstkError
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/one-per-ac.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\n"
        "title: one per ac\n"
        "status: spec\n"
        "---\n\n"
        "## Acceptance criteria\n"
        "- [ ] first\n"
        "- [ ] second\n"
        "- [ ] third\n"
        "- [ ] fourth\n"
    )

    async def broken_spec_build(path):
        raise OstkError("unreachable")

    async def broken_doc_decompose(path, auto=False):
        raise OstkError("unreachable")

    monkeypatch.setattr(ostk_module.ostk, "spec_build", broken_spec_build)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", broken_doc_decompose)

    next_id = {"n": 5500}

    async def fake_add_task(title, priority="P1", description="", ac=""):
        next_id["n"] += 1
        return f"\u2192{next_id['n']} {title}"

    monkeypatch.setattr(ostk_module.ostk, "add_task", fake_add_task)

    spawned: list[str] = []

    async def fake_spawn_agent(body):
        spawned.append(body.name)
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post(f"/api/specs/{spec_path_rel}/build")
    assert resp.status_code == 200
    data = resp.json()

    # Four AC bullets, four builders, four agent-to-task assignments,
    # four spec-origin records. No double-counting, no drops.
    assert len(data["agents"]) == 4
    assert len(spawned) == 4
    assert len(specs_router._task_assignments) == 4
    assert len(specs_router._spec_task_origin) == 4
    # Every assigned task traces back to this spec.
    for sp in specs_router._spec_task_origin.values():
        assert sp == spec_path_rel


@pytest.mark.asyncio
async def test_build_it_rebuild_creates_fresh_round_when_prior_closed(
    client, tmp_path, monkeypatch
):
    """After every prior builder task is closed, Build it must start a
    fresh round and spawn new builders, not surface the "no open tasks"
    banner.

    A Ready spec with tasks already recorded in the frontmatter whose
    status is closed used to fall into the empty-response branch. The
    AC-parsing fallback handles that state by creating fresh tasks for
    every bullet that is still unchecked, so Build it acts as a
    rebuild trigger just like the user expects.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/rebuild.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\n"
        "title: rebuild\n"
        "status: spec\n"
        "tasks:\n"
        '  - "3001"\n'
        '  - "3002"\n'
        "---\n\n"
        "## Acceptance criteria\n"
        "- [ ] one more time\n"
        "- [ ] and again\n"
    )

    # ostk.spec_build returns empty (all linked tasks already closed)
    # and ostk.doc_decompose noops because the spec was decomposed
    # before.
    async def empty_spec_build(path):
        return {"agents": []}

    async def noop_doc_decompose(path, auto=False):
        return {"result": "", "task_ids": []}

    monkeypatch.setattr(ostk_module.ostk, "spec_build", empty_spec_build)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", noop_doc_decompose)

    created: list[str] = []
    next_id = {"n": 9100}

    async def fake_add_task(title, priority="P1", description="", ac=""):
        next_id["n"] += 1
        created.append(title)
        return f"\u2192{next_id['n']} {title}"

    monkeypatch.setattr(ostk_module.ostk, "add_task", fake_add_task)

    spawned: list[str] = []

    async def fake_spawn_agent(body):
        spawned.append(body.name)
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post(f"/api/specs/{spec_path_rel}/build")
    assert resp.status_code == 200
    data = resp.json()

    # Rebuild created fresh tasks for every unchecked AC bullet.
    assert created == ["one more time", "and again"]
    assert len(data["agents"]) == 2
    assert len(spawned) == 2
    # The brand new task ids sit alongside the prior closed ones in
    # the frontmatter, not replacing them (history preserved).
    updated = spec_file.read_text()
    assert '"3001"' in updated
    assert '"3002"' in updated
    assert '"9101"' in updated
    assert '"9102"' in updated
    # And the spec status flipped to building so the UI knows builders
    # are running again.
    assert "status: building" in updated


@pytest.mark.asyncio
async def test_builder_spawns_with_live_model(
    client, tmp_path, monkeypatch
):
    """Build it must spawn builders on Sonnet (or the user default), not Haiku.

    The AgentSpawn body carries no demo or haiku override. The model
    falls back to the user's default_model (or "sonnet"). This test
    captures each spawned body and asserts the model is not Haiku.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "live-builder.md"
    spec_file.write_text(
        "---\ntitle: live builder\nstatus: spec\n---\n\n- [ ] one\n"
    )

    async def fake_spec_build(path):
        return {"agents": [
            {"name": "spec-live-a", "task_id": "7001",
             "task_title": "A", "prompt": "build A"},
        ]}

    monkeypatch.setattr(ostk_module.ostk, "spec_build", fake_spec_build)

    captured_bodies: list = []

    async def fake_spawn(body):
        captured_bodies.append(body)
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn)

    # Stub settings_store.get so the test does not depend on any
    # on-disk settings file. Return None so the code falls through to
    # its own default (sonnet model).
    from services import settings_store as settings_store_module

    def fake_get(key, default=None):
        return default

    monkeypatch.setattr(
        settings_store_module.settings_store, "get", fake_get
    )

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post("/api/specs/docs/spec/live-builder.md/build")
    assert resp.status_code == 200
    assert len(captured_bodies) == 1
    body = captured_bodies[0]

    # Model never silently downgrades to Haiku when no cfg override is
    # present and the user has not picked a default.
    assert body.model != "haiku"
    assert "haiku" not in str(body.model).lower()


# --------------------------------------------------------------------------
# Unified Build it cascade (stage 2 of moonlit-foraging-panda plan)
#
# These tests guard the single-path resolver:
#   step 1: ostk.spec_build
#   step 2: ostk.doc_decompose + retry
#   step 3: AC-checklist fallback
#
# The handler must always land on (a) non-empty agents list, or (b)
# has_unchecked_acs=False. The old middle "Could not create builder
# tasks for this plan" branch is deleted.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_with_indented_ac_checkbox(client, tmp_path, monkeypatch):
    """Indented ``  - [ ]`` bullets must still become builder tasks.

    The old regex required ``- [ ]`` to be the very first non-space on
    the line. Users writing nested lists in their Acceptance Criteria
    section hit that edge case and ended up with missing tasks. The
    broadened regex is anchored to ``^\\s*[-*]`` so any leading
    whitespace passes through.
    """
    from services import ostk as ostk_module
    from services.ostk import OstkError
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/indented-ac.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\n"
        "title: indented ac\n"
        "status: spec\n"
        "---\n\n"
        "## Acceptance criteria\n"
        "  - [ ] foo\n"
        "- [ ] bar\n"
    )

    async def broken(*args, **kwargs):
        raise OstkError("unreachable")

    monkeypatch.setattr(ostk_module.ostk, "spec_build", broken)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", broken)

    created: list[str] = []

    async def fake_add_task(title, priority="P1", description="", ac=""):
        created.append(title)
        return f"\u21925{len(created):03d} {title}"

    monkeypatch.setattr(ostk_module.ostk, "add_task", fake_add_task)

    async def fake_spawn_agent(body):
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post(f"/api/specs/{spec_path_rel}/build")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Both the indented and flush bullets must land in the task list.
    assert created == ["foo", "bar"]
    assert len(data["agents"]) == 2


@pytest.mark.asyncio
async def test_build_with_asterisk_ac_checkbox(client, tmp_path, monkeypatch):
    """``* [ ]`` bullets must also become builder tasks.

    Markdown lets bullets start with ``-`` or ``*``. Specs authored
    from templates that use the ``*`` flavor used to silently drop
    every AC. The broadened regex accepts either marker.
    """
    from services import ostk as ostk_module
    from services.ostk import OstkError
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/asterisk-ac.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\n"
        "title: asterisk ac\n"
        "status: spec\n"
        "---\n\n"
        "## Acceptance criteria\n"
        "* [ ] foo\n"
    )

    async def broken(*args, **kwargs):
        raise OstkError("unreachable")

    monkeypatch.setattr(ostk_module.ostk, "spec_build", broken)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", broken)

    created: list[str] = []

    async def fake_add_task(title, priority="P1", description="", ac=""):
        created.append(title)
        return f"\u21926{len(created):03d} {title}"

    monkeypatch.setattr(ostk_module.ostk, "add_task", fake_add_task)

    async def fake_spawn_agent(body):
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post(f"/api/specs/{spec_path_rel}/build")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert created == ["foo"]
    assert len(data["agents"]) == 1


@pytest.mark.asyncio
async def test_build_never_returns_could_not_create_when_acs_exist(
    client, tmp_path, monkeypatch
):
    """The middle "Could not create builder tasks" branch is deleted.

    With all three cascade steps stubbed to produce zero configs in
    turn, the handler must fall through cleanly to the AC fallback.
    When the spec DOES have unchecked ACs, the AC fallback creates
    tasks. When the spec has NONE, the response reports
    ``has_unchecked_acs=False``. Never the old middle state.
    """
    from services import ostk as ostk_module
    from services.ostk import OstkError
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/no-middle-state.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\n"
        "title: no middle state\n"
        "status: spec\n"
        "---\n\n"
        "## Acceptance criteria\n"
        "- [ ] real ac\n"
    )

    # Step 1 and 2 both come back empty.
    async def empty_spec_build(path):
        return {"agents": []}

    async def empty_doc_decompose(path, auto=False):
        return {"result": "empty", "task_ids": []}

    monkeypatch.setattr(ostk_module.ostk, "spec_build", empty_spec_build)
    monkeypatch.setattr(
        ostk_module.ostk, "doc_decompose", empty_doc_decompose
    )

    created: list[str] = []

    async def fake_add_task(title, priority="P1", description="", ac=""):
        created.append(title)
        return f"\u21927{len(created):03d} {title}"

    monkeypatch.setattr(ostk_module.ostk, "add_task", fake_add_task)

    async def fake_spawn_agent(body):
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post(f"/api/specs/{spec_path_rel}/build")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # The message must never be the old middle-state string.
    assert "Could not create builder tasks" not in (data.get("message") or "")

    # The response is either agents>0 (AC fallback fired) OR
    # has_unchecked_acs=False (no ACs). Never the middle.
    agents_created = len(data.get("agents", []))
    has_unchecked = data.get("has_unchecked_acs", None)
    if agents_created > 0:
        assert created == ["real ac"]
    else:
        assert has_unchecked is False, (
            "build response hit the middle 'gave up' state: "
            f"data={data}"
        )

    # Repeat with a spec that has zero ACs to confirm the
    # has_unchecked_acs=False branch.
    empty_spec_rel = "docs/spec/no-acs.md"
    (tmp_path / empty_spec_rel).write_text(
        "---\ntitle: no acs\nstatus: spec\n---\n\n"
        "## What we want\nJust prose, no criteria.\n"
    )
    resp2 = await client.post(f"/api/specs/{empty_spec_rel}/build")
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    assert data2["agents"] == []
    assert data2["has_unchecked_acs"] is False
    assert "Could not create builder tasks" not in data2.get("message", "")


@pytest.mark.asyncio
async def test_build_scopes_to_acceptance_criteria_heading(
    client, tmp_path, monkeypatch
):
    """Prose bullets above ``## Acceptance Criteria`` must not become tasks.

    A common spec layout includes a "What we want" section with its own
    bullets; those are narrative, not work items. The parser must scope
    itself to the Acceptance Criteria section when that heading is
    present, so only real ACs become builder tasks.
    """
    from services import ostk as ostk_module
    from services.ostk import OstkError
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_path_rel = "docs/spec/heading-scope.md"
    spec_file = tmp_path / spec_path_rel
    spec_file.write_text(
        "---\n"
        "title: heading scope\n"
        "status: spec\n"
        "---\n\n"
        "## What we want\n"
        "- [ ] prose_bullet\n\n"
        "## Acceptance Criteria\n"
        "- [ ] real_ac\n"
    )

    async def broken(*args, **kwargs):
        raise OstkError("unreachable")

    monkeypatch.setattr(ostk_module.ostk, "spec_build", broken)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", broken)

    created: list[str] = []

    async def fake_add_task(title, priority="P1", description="", ac=""):
        created.append(title)
        return f"\u21928{len(created):03d} {title}"

    monkeypatch.setattr(ostk_module.ostk, "add_task", fake_add_task)

    async def fake_spawn_agent(body):
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post(f"/api/specs/{spec_path_rel}/build")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Only the AC-section bullet must become a task.
    assert created == ["real_ac"]
    assert "prose_bullet" not in created
    assert len(data["agents"]) == 1


# ---------------------------------------------------------------------------
# Regression: sidebar badge / Specs page count mismatch (→badge-count bug)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spec_counts_excludes_plan_transcripts(client, monkeypatch):
    """spec_counts must not count plan transcript files as unfinished specs.

    ostk.list_docs() returns plan transcripts (status="plan") alongside real
    specs. Before the fix, including them caused the sidebar to show N while
    the Specs page showed 0 (no real specs). The fix filters status="plan"
    docs from the count so badge and page agree.

    Isolation: list_docs is mocked directly so no real project docs/ or
    ~/.myos/specs files can leak into the count regardless of the machine
    running the tests.  The previous approach (patching ostk.cwd +
    USER_SPECS_DIR) was fragile — any new code path in list_docs that reads
    outside those two roots would break isolation silently.
    """
    from services import ostk as ostk_module

    async def fake_list_docs():
        return [
            # Two plan transcript files — must NOT be counted.
            {
                "path": "transcripts/plan-100.md",
                "title": "Plan for →100",
                "status": "plan",
                "task_ids": [],
                "acceptance_criteria": [],
                "task_summary": {"total": 0, "open": 0, "closed": 0},
            },
            {
                "path": "transcripts/plan-200.md",
                "title": "Plan for →200",
                "status": "plan",
                "task_ids": [],
                "acceptance_criteria": [],
                "task_summary": {"total": 0, "open": 0, "closed": 0},
            },
            # One real spec in ready state — must be counted as unfinished.
            {
                "path": "docs/spec/my-spec.md",
                "title": "My Spec",
                "status": "spec",
                "task_ids": [],
                "acceptance_criteria": [{"text": "Do the thing", "checked": False}],
                "task_summary": {"total": 0, "open": 0, "closed": 0},
            },
        ]

    monkeypatch.setattr(ostk_module.ostk, "list_docs", fake_list_docs)

    res = await client.get("/api/specs/counts")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1, f"expected 1 real spec, got {data['total']}"
    assert data["unfinished"] == 1


@pytest.mark.asyncio
async def test_list_specs_excludes_plan_transcripts(client, tmp_path, monkeypatch):
    """GET /specs must not return plan transcript files.

    Plan transcripts surface in /specs/recent for the Recent Documents widget
    but must not appear on the Specs page. Before the fix they showed up as
    Draft specs, inflating the page count.
    """
    from services import ostk as ostk_module

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))

    # A plan transcript that must NOT appear in /specs.
    (transcripts_dir / "plan-300.md").write_text("Plan for needle 300\n")

    # A real spec that must appear.
    (tmp_path / "docs" / "spec" / "real-spec.md").write_text(
        "---\ntitle: Real Spec\nstatus: spec\n---\n\n- [ ] Do the thing\n"
    )

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", AsyncMock(return_value=[]))

    res = await client.get("/api/specs")
    assert res.status_code == 200
    docs = res.json()["docs"]
    paths = [d["path"] for d in docs]

    assert not any("transcripts/" in p for p in paths), (
        f"Plan transcript leaked into /specs: {paths}"
    )
    assert any("docs/spec/" in p for p in paths), (
        f"Real spec missing from /specs: {paths}"
    )


@pytest.mark.asyncio
async def test_list_specs_umbrella_spec_has_is_umbrella_true(
    client, tmp_path, monkeypatch
):
    """GET /api/specs sets is_umbrella=True on specs with umbrella: true frontmatter.

    An umbrella spec consolidates multiple leaf specs under one entry so the
    Backlog board can render them grouped. The API must surface the flag so
    the frontend knows which rows are umbrella roots.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path / "no-user-specs")

    spec_file = tmp_path / "docs" / "spec" / "big-feature-umbrella.md"
    spec_file.write_text(
        "---\ntitle: Big Feature\nstatus: spec\numbrella: true\n---\n\n- [ ] Ship it\n"
    )

    res = await client.get("/api/specs")
    assert res.status_code == 200
    docs = res.json()["docs"]
    umbrella = next((d for d in docs if "big-feature-umbrella" in d["path"]), None)
    assert umbrella is not None, "umbrella spec not found in /api/specs response"
    assert umbrella["is_umbrella"] is True
    assert umbrella["parent_slug"] is None


@pytest.mark.asyncio
async def test_list_specs_leaf_spec_has_parent_slug(client, tmp_path, monkeypatch):
    """GET /api/specs sets parent_slug on specs with parent: <slug> frontmatter.

    A leaf spec declares which umbrella it belongs to via parent: <slug>. The
    API must surface parent_slug so the frontend can group leaves under their
    umbrella row without a second network call.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path / "no-user-specs")

    (tmp_path / "docs" / "spec" / "big-feature-umbrella.md").write_text(
        "---\ntitle: Big Feature\nstatus: spec\numbrella: true\n---\n\n- [ ] Ship it\n"
    )
    leaf_file = tmp_path / "docs" / "spec" / "big-feature-leaf-one.md"
    leaf_file.write_text(
        "---\ntitle: Leaf One\nstatus: spec\nparent: big-feature-umbrella\n---\n\n- [ ] Do part one\n"
    )

    res = await client.get("/api/specs")
    assert res.status_code == 200
    docs = res.json()["docs"]
    leaf = next((d for d in docs if "leaf-one" in d["path"]), None)
    assert leaf is not None, "leaf spec not found in /api/specs response"
    assert leaf["is_umbrella"] is False
    assert leaf["parent_slug"] == "big-feature-umbrella"


@pytest.mark.asyncio
async def test_list_specs_standalone_spec_has_no_umbrella_fields(
    client, tmp_path, monkeypatch
):
    """GET /api/specs sets is_umbrella=False and parent_slug=None on ordinary specs.

    Specs without umbrella or parent frontmatter are standalone. They must
    still appear in the response and must not have stale hierarchy values.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path / "no-user-specs")

    spec_file = tmp_path / "docs" / "spec" / "standalone-spec.md"
    spec_file.write_text(
        "---\ntitle: Standalone\nstatus: spec\n---\n\n- [ ] Just one thing\n"
    )

    res = await client.get("/api/specs")
    assert res.status_code == 200
    docs = res.json()["docs"]
    standalone = next((d for d in docs if "standalone-spec" in d["path"]), None)
    assert standalone is not None, "standalone spec not found in /api/specs response"
    assert standalone["is_umbrella"] is False
    assert standalone["parent_slug"] is None


def test_user_specs_dir_honors_myos_user_specs_dir_env_var(tmp_path):
    """USER_SPECS_DIR must read MYOS_USER_SPECS_DIR at module load.

    Smoke tests need to redirect spec writes to a tmpdir so the live
    ~/.myos/specs/ never accumulates artifacts. The contract: set
    MYOS_USER_SPECS_DIR in the environment before importing services.ostk,
    and the resolved USER_SPECS_DIR equals that path. Fallback when unset
    is ~/.myos/specs.

    Fixes →1411 root cause #1 (per
    ~/.claude/plans/review-our-open-specs-glittery-hejlsberg.md).
    """
    import subprocess
    import sys

    custom = tmp_path / "isolated-specs"
    api_dir = str(Path(__file__).resolve().parents[1])

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from services.ostk import USER_SPECS_DIR; print(str(USER_SPECS_DIR))",
        ],
        env={
            "MYOS_USER_SPECS_DIR": str(custom),
            "PYTHONPATH": api_dir,
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"import failed: {result.stderr}"
    assert result.stdout.strip() == str(custom), (
        f"USER_SPECS_DIR ignored MYOS_USER_SPECS_DIR env: got {result.stdout.strip()!r}"
    )


# ---------------------------------------------------------------------------
# →1470  POST /api/specs/{slug}/backfill  and  POST /api/specs/{slug}/archive
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS = [
    "Problem",
    "Goals",
    "Non-goals",
    "Solution",
    "Edge cases",
    "Success criteria",
    "Acceptance criteria",
    "Verification",
    "USER FEEDBACK",
    "DECISION",
]

_PARTIAL_SPEC = """\
---
title: partial spec
status: spec
---

## Problem
_What's broken and who's affected._

## Goals
_What success looks like._

## Non-goals
_What is explicitly out of scope._
"""


@pytest.mark.asyncio
async def test_backfill_adds_missing_sections(client, tmp_path, monkeypatch):
    """backfill on a 3/10 spec appends the 7 missing sections so the spec has all 10."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", specs_dir)
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    slug = "partial-spec"
    spec_file = specs_dir / f"{slug}.md"
    spec_file.write_text(_PARTIAL_SPEC)

    resp = await client.post(f"/api/specs/{slug}/backfill")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    updated = spec_file.read_text()
    for section in REQUIRED_SECTIONS:
        assert f"## {section}" in updated, f"Missing section after backfill: {section}"

    assert "content" in body
    assert "path" in body


@pytest.mark.asyncio
async def test_backfill_returns_404_for_missing_spec(client, tmp_path, monkeypatch):
    """backfill on a non-existent slug returns 404."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", specs_dir)
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    resp = await client.post("/api/specs/does-not-exist/backfill")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_archive_moves_spec_file(client, tmp_path, monkeypatch):
    """archive moves the spec to archive/ and returns the new path."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", specs_dir)
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    slug = "to-archive"
    spec_file = specs_dir / f"{slug}.md"
    spec_file.write_text("---\ntitle: to archive\nstatus: spec\n---\n\n# content\n")

    resp = await client.post(f"/api/specs/{slug}/archive")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert not spec_file.exists(), "Original spec file should have been moved"
    new_path = body.get("path") or body.get("new_path")
    assert new_path, f"Response missing path: {body}"
    assert slug in new_path
    assert "archive" in new_path

    archive_dir = specs_dir / "archive"
    archived_files = list(archive_dir.glob(f"*{slug}*"))
    assert len(archived_files) == 1, f"Expected exactly one archived file, got: {archived_files}"


@pytest.mark.asyncio
async def test_archive_returns_404_for_missing_spec(client, tmp_path, monkeypatch):
    """archive on a non-existent slug returns 404."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", specs_dir)
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    resp = await client.post("/api/specs/ghost-slug/archive")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_archive_returns_409_if_already_archived(client, tmp_path, monkeypatch):
    """archive on a slug that is already in archive/ returns 409 (→1512)."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)
    archive_dir = specs_dir / "archive"
    archive_dir.mkdir(parents=True)
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", specs_dir)
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    slug = "already-done"
    # Spec exists at user-local path
    (specs_dir / f"{slug}.md").write_text("---\ntitle: done\n---\n")
    # Already archived with a timestamp prefix
    (archive_dir / f"20260101T000000Z-{slug}.md").write_text("---\ntitle: done\n---\n")

    resp = await client.post(f"/api/specs/{slug}/archive")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_spec_counts_by_stage_breakdown(client, monkeypatch):
    """spec_counts returns by_stage dict with correct counts (→1512)."""
    from services import ostk as ostk_module

    async def fake_list_docs():
        # →1561: 3-stage model — draft/ready/in_progress only; no shipped/building
        return [
            {"path": "docs/draft/a.md", "status": "draft", "stage": "draft"},
            {"path": "~/.myos/specs/b.md", "status": "spec", "stage": "ready"},
            {"path": "~/.myos/specs/c.md", "status": "in-progress", "stage": "in_progress"},
        ]

    monkeypatch.setattr(ostk_module.ostk, "list_docs", fake_list_docs)

    res = await client.get("/api/specs/counts")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert body["unfinished"] == 2  # ready + in_progress
    assert body["by_stage"]["draft"] == 1
    assert body["by_stage"]["ready"] == 1
    assert body["by_stage"]["in_progress"] == 1
    assert "shipped" not in body["by_stage"]


@pytest.mark.asyncio
async def test_validate_write_doc_path_rejects_docs_spec(client):
    """POST to docs/spec/ path is rejected with 400 (→1512 FR-007)."""
    from routers.specs import _validate_write_doc_path
    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as exc_info:
        _validate_write_doc_path("docs/spec/foo.md")
    assert exc_info.value.status_code == 400
    assert "docs/spec/" in exc_info.value.detail


@pytest.mark.asyncio
async def test_validate_write_doc_path_allows_docs_draft(client):
    """docs/draft/ paths are accepted by the write validator (→1512 FR-007)."""
    from routers.specs import _validate_write_doc_path
    _validate_write_doc_path("docs/draft/foo.md")  # must not raise


# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doc_draft_refuses_hooks_shaped_titles():
    """ostk.doc_draft must refuse titles that look like hooks reviews.

    Per ~/.claude/projects/-Users-torimeyer-claude-torios/memory/
    feedback_hooks_at_user_scope.md: hooks reviews live in ~/.myos/hooks/,
    never under docs/draft/. When a user (or model) calls doc_draft with
    a title containing 'hook', refuse with a message redirecting to the
    correct location instead of silently creating a misplaced draft.

    Fixes →1455.
    """
    from services import ostk as ostk_module

    hookish_titles = [
        "Hooks review 2026-05-15",
        "Hook system audit",
        "hooks-review-followup",
        "Audit of our pre-tool hooks",
    ]
    for title in hookish_titles:
        with pytest.raises(ostk_module.OstkError) as exc_info:
            await ostk_module.ostk.doc_draft(title)
        assert "hooks" in str(exc_info.value).lower(), (
            f"Error for {title!r} should mention 'hooks': {exc_info.value}"
        )
        assert "~/.myos/hooks" in str(exc_info.value), (
            f"Error for {title!r} should point to ~/.myos/hooks/: {exc_info.value}"
        )

    # Sanity: non-hook titles should NOT be refused (we don't actually
    # write the draft here; we just verify the validator lets it through
    # without raising OstkError). The underlying ostk binary may still
    # raise for other reasons in a real run, but those are not OstkError.
    safe_titles = ["Pattern watcher v2", "User memory store improvements"]
    for title in safe_titles:
        try:
            await ostk_module.ostk.doc_draft(title)
        except ostk_module.OstkError as e:
            # Only assert that the validator-level message isn't fired.
            assert "hooks" not in str(e).lower() or "~/.myos/hooks" not in str(e)
        except Exception:
            # Other errors (e.g. ostk binary missing in test env) are fine.
            pass


# ---------------------------------------------------------------------------
# →1467: build_spec model param
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_spec_with_gemini_model(client, tmp_path, monkeypatch):
    """POST /api/specs/{path}/build?model=gemini routes the spawn payload
    with model='gemini'. The model is injected into every cfg dict before
    _spawn_one picks it up, so AgentSpawn.model ends up as 'gemini'."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "gemini-build-test.md"
    spec_file.write_text(
        "---\ntitle: gemini build test\nstatus: spec\n---\n\n- [ ] implement foo\n"
    )

    agent_configs = [
        {
            "name": "build-gemini-test-101",
            "task_id": "101",
            "task_title": "Implement foo",
            "prompt": "Build task 101",
        }
    ]

    async def fake_spec_build(path):
        return {"agents": agent_configs}

    monkeypatch.setattr(ostk_module.ostk, "spec_build", fake_spec_build)

    spawned: list[dict] = []

    async def fake_spawn_agent(body):
        spawned.append({"name": body.name, "model": body.model})
        return {"agent": body.name}

    import routers.agents as agents_router
    monkeypatch.setattr(agents_router, "spawn_agent", fake_spawn_agent)

    resp = await client.post(
        "/api/specs/docs/spec/gemini-build-test.md/build?model=gemini"
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["agents"] == ["build-gemini-test-101"]
    assert len(spawned) == 1
    assert spawned[0]["model"] == "gemini", (
        f"Expected model='gemini', got {spawned[0]['model']!r}"
    )


@pytest.mark.asyncio
async def test_build_spec_invalid_model_rejected(client, tmp_path, monkeypatch):
    """POST /api/specs/{path}/build?model=unknown_model returns 422."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "model-reject-test.md"
    spec_file.write_text("---\ntitle: test\nstatus: spec\n---\n- [ ] foo\n")

    resp = await client.post(
        "/api/specs/docs/spec/model-reject-test.md/build?model=badmodel"
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# →1547: needs_clarity flow fixes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promotion_blocked_when_needs_clarity(client, tmp_path, monkeypatch):
    """POST /specs/promote returns 422 when draft is missing acceptance criteria.

    A draft with no '- [ ]' checkbox lines fails the readiness check and
    must not be promoted. The response body carries error='needs_clarity'
    and a failing_checks list so the frontend knows which checks failed.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    draft_file = tmp_path / "docs" / "draft" / "no-ac.md"
    draft_file.write_text(
        "---\ntitle: No AC draft\nstatus: draft\n---\n\n"
        "This draft has no acceptance criteria checkboxes.\n"
        "It references api/routers/specs.py which exists.\n"
    )

    resp = await client.post(
        "/api/specs/promote",
        json={"path": "docs/draft/no-ac.md"},
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    detail = body.get("detail", {})
    assert detail.get("error") == "needs_clarity"
    failing_names = [c["name"] for c in detail.get("failing_checks", [])]
    assert "has_ac_checkboxes" in failing_names


@pytest.mark.asyncio
async def test_auto_archived_specs_not_surfaced(client, tmp_path, monkeypatch):
    """GET /specs silently auto-archives specs that meet the shipped condition.

    In the →1561 3-stage model there is no "shipped" stage — specs that have
    all referenced files present and all linked needles closed are silently
    moved to archive/ and excluded from the list. This test verifies that a
    spec meeting those conditions (no file refs, no open needles, file exists)
    does not appear in the docs list returned by GET /api/specs.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    # A real file on disk with no file refs and no needle refs — compute_shipped
    # will return is_shipped=True, causing the endpoint to auto-archive it.
    spec_file = tmp_path / "done-spec.md"
    spec_file.write_text(
        "---\ntitle: Done spec\nstatus: ready\n---\n\n"
        "All acceptance criteria met. No remaining work.\n"
    )

    async def fake_list_docs():
        return [
            {
                "path": str(spec_file),  # absolute path bypasses PROJECT_ROOT join
                "title": "Done spec",
                "status": "ready",
                "task_ids": [],
                "acceptance_criteria": [],
                "task_summary": {"total": 0, "open": 0, "closed": 0},
            }
        ]

    monkeypatch.setattr(ostk_module.ostk, "list_docs", fake_list_docs)

    resp = await client.get("/api/specs")
    assert resp.status_code == 200
    docs = resp.json()["docs"]
    # The spec meets auto-archive condition and must not appear in the list
    assert len(docs) == 0, (
        f"auto-archived spec should not appear in docs list, got: {docs}"
    )


@pytest.mark.asyncio
async def test_clarity_patch_appends_and_reruns_readiness(client, tmp_path, monkeypatch):
    """PATCH /specs/{path}/clarity appends fix text and returns updated checks.

    The endpoint should:
    - Append the provided fix text under the right markdown section
    - Re-run compute_spec_readiness on the updated file
    - Return checks (list) and ready (bool) in the response
    """
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_file = tmp_path / "docs" / "draft" / "clarity-target.md"
    spec_file.write_text(
        "---\ntitle: Clarity target\nstatus: draft\n---\n\n"
        "# Clarity target\n\nNo acceptance criteria yet.\n"
    )

    resp = await client.patch(
        "api/specs/docs/draft/clarity-target.md/clarity",
        json={"check": "has_ac_checkboxes", "fix": "- [ ] add login flow\n- [ ] add logout flow\n- [ ] handle errors\n"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "checks" in body, f"expected checks in response, got: {body}"
    assert "ready" in body
    assert isinstance(body["checks"], list)
    assert len(body["checks"]) == 9  # all 9 checks always returned

    # Fix text should have been written to the file
    updated_text = spec_file.read_text()
    assert "add login flow" in updated_text
