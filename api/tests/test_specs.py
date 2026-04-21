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

    draft_file = tmp_path / "docs" / "draft" / "wave2-autopromote.md"
    spec_file = tmp_path / "docs" / "spec" / "wave2-autopromote.md"

    async def fake_run(*args, **kwargs):
        if args[:2] == ("doc", "draft"):
            draft_file.write_text(
                "---\ntitle: wave2 autopromote\nstatus: draft\n---\n\n"
            )
            return str(draft_file.relative_to(tmp_path))
        if args[:2] == ("doc", "promote"):
            text = draft_file.read_text().replace(
                "status: draft", "status: spec"
            )
            spec_file.write_text(text)
            draft_file.unlink()
            return str(spec_file.relative_to(tmp_path))
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
        "/api/specs/draft", json={"title": "wave2 autopromote"}
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

    draft_file = tmp_path / "docs" / "draft" / "wave2-no-ac.md"

    promote_called = False

    async def fake_run(*args, **kwargs):
        nonlocal promote_called
        if args[:2] == ("doc", "draft"):
            draft_file.write_text(
                "---\ntitle: wave2 no ac\nstatus: draft\n---\n\n"
            )
            return str(draft_file.relative_to(tmp_path))
        if args[:2] == ("doc", "promote"):
            promote_called = True
            raise AssertionError(
                "promote must not fire when no AC was written"
            )
        raise AssertionError(f"unexpected ostk call: {args}")

    monkeypatch.setattr(ostk_module.ostk, "_run", fake_run)

    # No API key -> the route skips the AI branch entirely.
    monkeypatch.setattr(
        "services.chat_providers._resolve_api_key",
        AsyncMock(return_value=None),
    )

    resp = await client.post(
        "/api/specs/draft", json={"title": "wave2 no ac"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "draft"
    assert data["promoted_path"] is None
    assert not promote_called
    # The file stays in draft/.
    assert draft_file.exists()


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

    async def fake_doc_decompose(path):
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

    draft_file = tmp_path / "docs" / "draft" / "build-a-website.md"
    spec_file = tmp_path / "docs" / "spec" / "build-a-website.md"

    decompose_calls = {"count": 0}

    async def fake_run(*args, **kwargs):
        if args[:2] == ("doc", "draft"):
            draft_file.write_text(
                "---\ntitle: Build a Website\nstatus: draft\n---\n\n"
            )
            return str(draft_file.relative_to(tmp_path))
        if args[:2] == ("doc", "promote"):
            text = draft_file.read_text().replace(
                "status: draft", "status: spec"
            )
            spec_file.write_text(text)
            draft_file.unlink()
            return str(spec_file.relative_to(tmp_path))
        if args[:2] == ("doc", "decompose"):
            decompose_calls["count"] += 1
            return ""
        raise AssertionError(f"unexpected ostk call: {args}")

    monkeypatch.setattr(ostk_module.ostk, "_run", fake_run)

    resp = await client.post(
        "/api/specs/from-template",
        json={"template_id": "build-a-website"},
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
        if args[:2] == ("doc", "promote"):
            text = draft_file.read_text().replace("status: draft", "status: spec")
            spec_file.write_text(text)
            draft_file.unlink()
            return str(spec_file.relative_to(tmp_path))
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
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ready"
    assert data["promoted_path"] is not None
    assert data["title"].startswith("Ship guided onboarding")
    assert spec_file.exists()
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
    assert call["title"] == "Spec done"
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
    """GET /api/specs/counts returns unfinished = non-complete specs.

    Anchors the Sidebar badge semantics: draft, ready, and in-progress
    all count as unfinished; only complete falls off the badge. If this
    ever drifts (for example, somebody excludes drafts or adds a new
    terminal state without updating the badge), the Sidebar count will
    diverge from the Specs page and the user will see a stale badge.
    """
    from services import ostk as ostk_module

    async def fake_list_docs():
        return [
            {"path": "docs/draft/a.md", "status": "draft"},
            {"path": "docs/spec/b.md", "status": "ready"},
            {"path": "docs/spec/c.md", "status": "in-progress"},
            {"path": "docs/spec/d.md", "status": "complete"},
            {"path": "docs/spec/e.md", "status": "complete"},
        ]

    monkeypatch.setattr(ostk_module.ostk, "list_docs", fake_list_docs)

    res = await client.get("/api/specs/counts")
    assert res.status_code == 200
    body = res.json()
    assert body == {"unfinished": 3, "total": 5}


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
    assert res.json() == {"unfinished": 0, "total": 0}


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
        if args[:2] == ("doc", "promote"):
            draft_rel = args[2]
            src = tmp_path / draft_rel
            dst = tmp_path / draft_rel.replace("docs/draft/", "docs/spec/", 1)
            dst.write_text(src.read_text().replace("status: draft", "status: spec"))
            src.unlink()
            return str(dst.relative_to(tmp_path))
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
            },
        )
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ready"
        durations.append(elapsed)

    # p95 of three samples is the max. Budget 1.5 s of orchestration.
    # Real demo adds ~2.3 s for Haiku on top, landing under 5 s total.
    p95 = max(durations)
    p50 = sorted(durations)[1]
    assert p95 < 1.5, (
        f"spec-from-roadmap-line orchestration regressed: p50={p50*1000:.0f}ms "
        f"p95={p95*1000:.0f}ms samples={[f'{d*1000:.0f}ms' for d in durations]}. "
        "Budget is 1.5 s; combined with Haiku's ~2.3 s p50 that keeps the "
        "end-to-end demo path under the 5 s target. Investigate any new "
        "per-call file scan, settings reload, or sync write before raising "
        "this threshold."
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

    async def fake_doc_decompose(path):
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

    async def empty_doc_decompose(path):
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
    monkeypatch.setattr(ostk_module.ostk, "close_task", fake_close_task)

    # Reset the burst-guard so sequential closes in this test do not 429.
    tasks_router._recent_closes.clear()

    # Close the first task: spec status must stay as 'spec' since one
    # builder is still open.
    resp1 = await client.post("/api/tasks/8001/close")
    assert resp1.status_code == 200
    assert "status: spec" in spec_file.read_text()
    assert "status: done" not in spec_file.read_text()

    # Close the second (and final) task: the spec now flips to 'done'.
    resp2 = await client.post("/api/tasks/8002/close")
    assert resp2.status_code == 200
    updated = spec_file.read_text()
    assert "status: done" in updated
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

    async def broken_doc_decompose(path):
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

    async def broken_doc_decompose(path):
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

    async def noop_doc_decompose(path):
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
async def test_builder_spawns_without_demo_mode_and_with_real_deadline(
    client, tmp_path, monkeypatch
):
    """Regression: Build it must NOT hardcode demo_mode=True on builder spawn.

    The old code forced demo_mode=True for every Build it run, which
    coerced the model to Haiku and capped each builder at 90 seconds.
    That hurt normal users: a 10 minute real task was getting force
    completed after 90 s with a Haiku-produced stub.

    The fix: the AgentSpawn body carries NO demo_mode flag, the model
    falls back to the user's default_model (or "sonnet"), and the
    deadline reads from the spec_build_deadline_s setting with a 10
    minute (600 s) default. This test captures each spawned body and
    asserts those three properties.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "no-demo-mode.md"
    spec_file.write_text(
        "---\ntitle: no demo mode\nstatus: spec\n---\n\n- [ ] one\n"
    )

    async def fake_spec_build(path):
        return {"agents": [
            {"name": "spec-no-demo-a", "task_id": "7001",
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
    # on-disk settings file. Return None for both keys so the code
    # falls through to its own defaults (sonnet model, 600 s deadline).
    from services import settings_store as settings_store_module

    def fake_get(key, default=None):
        return default

    monkeypatch.setattr(
        settings_store_module.settings_store, "get", fake_get
    )

    specs_router._task_assignments.clear()
    specs_router._spec_task_origin.clear()

    resp = await client.post("/api/specs/docs/spec/no-demo-mode.md/build")
    assert resp.status_code == 200
    assert len(captured_bodies) == 1
    body = captured_bodies[0]

    # Regression: demo_mode must not be set on the spawn body. The
    # Pydantic field is Optional so it can be None or absent; what
    # matters is that it is NOT True.
    assert getattr(body, "demo_mode", None) is not True, (
        "builder spawn still forces demo_mode=True; that coerces the "
        "model to Haiku and caps each builder at 90 seconds"
    )

    # Deadline defaults to 10 minutes, not the old 90 s demo cap.
    assert body.deadline_seconds == 600

    # Model never silently downgrades to Haiku when no cfg override is
    # present and the user has not picked a default.
    assert body.model != "haiku"
    assert "haiku" not in str(body.model).lower()
