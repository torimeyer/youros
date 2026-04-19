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
