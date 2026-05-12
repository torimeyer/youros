import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- Helpers ---

def _make_llm_response(data: dict) -> MagicMock:
    """Build a mock Anthropic message response containing JSON text."""
    text_block = SimpleNamespace(type="text", text=json.dumps(data))
    response = MagicMock()
    response.content = [text_block]
    response.usage = SimpleNamespace(input_tokens=100, output_tokens=200)
    return response


VALID_LLM_DATA = {
    "goal": {
        "title": "Get taxes filed",
        "description": "File your taxes without stress or penalties",
    },
    "tasks": [
        {"title": "Gather W-2s and 1099s from employers and banks", "priority": "P1"},
        {"title": "Pick a filing method", "priority": "P1"},
        {"title": "Fill out and review the return", "priority": "P1"},
        {"title": "Submit the return and save confirmation", "priority": "P2"},
    ],
}


# --- POST /api/onboarding/dream ---

@pytest.mark.asyncio
async def test_dream_returns_goal_and_tasks(client):
    """Happy path: LLM returns valid JSON, tasks are persisted."""
    mock_response = _make_llm_response(VALID_LLM_DATA)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=mock_response)
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
            "done_looks_like": "Taxes filed",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["goal"]["title"] == "Get taxes filed"
    assert len(data["tasks"]) == 4
    assert data["tasks"][0]["priority"] == "P1"

    # The dream endpoint returns the generated plan. Persistence is handled
    # by the frontend calling /api/tasks for each task, so add_task is not
    # called from inside this endpoint anymore.
    assert mock_ostk.add_task.call_count == 0


@pytest.mark.asyncio
async def test_dream_without_done_looks_like(client):
    """The done_looks_like field is optional."""
    mock_response = _make_llm_response(VALID_LLM_DATA)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=mock_response)
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to clean my garage",
        })

    assert resp.status_code == 200
    assert "goal" in resp.json()
    assert "tasks" in resp.json()


@pytest.mark.asyncio
async def test_dream_empty_dreading_returns_422(client):
    """An empty dreading string should be rejected."""
    resp = await client.post("/api/onboarding/dream", json={
        "dreading": "   ",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_dream_missing_dreading_returns_422(client):
    """Missing the required dreading field should return 422."""
    resp = await client.post("/api/onboarding/dream", json={})
    assert resp.status_code == 422


# --- Fallback behavior ---

@pytest.mark.asyncio
async def test_dream_fallback_when_no_api_key(client):
    """When no API key is available, return a sensible fallback plan."""
    with (
        patch("routers.onboarding._resolve_api_key", return_value=""),
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "goal" in data
    assert len(data["tasks"]) > 0
    # The endpoint returns the plan. Persistence happens on the frontend.
    assert mock_ostk.add_task.call_count == 0


@pytest.mark.asyncio
async def test_dream_fallback_when_llm_returns_bad_json(client):
    """When the LLM returns invalid JSON, fall back to a generic plan."""
    bad_response = MagicMock()
    bad_response.content = [SimpleNamespace(type="text", text="This is not JSON at all")]
    bad_response.usage = SimpleNamespace(input_tokens=50, output_tokens=50)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=bad_response)
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "goal" in data
    assert len(data["tasks"]) > 0


@pytest.mark.asyncio
async def test_dream_fallback_when_llm_api_error(client):
    """When the Anthropic API raises an error, fall back gracefully."""
    import anthropic as anth

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(
            side_effect=anth.APIError(
                message="overloaded",
                request=MagicMock(),
                body=None,
            )
        )
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "goal" in data
    assert len(data["tasks"]) > 0


@pytest.mark.asyncio
async def test_dream_fallback_when_llm_missing_fields(client):
    """When the LLM returns JSON but with missing required fields, fall back."""
    incomplete_data = {"goal": {"title": "Test"}}  # missing description and tasks
    mock_response = _make_llm_response(incomplete_data)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=mock_response)
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "goal" in data
    assert len(data["tasks"]) > 0


# --- Returned plan shape ---

@pytest.mark.asyncio
async def test_dream_returns_tasks_with_correct_priorities(client):
    """Each generated task should appear in the response with its priority.

    Note: the dream endpoint no longer persists tasks itself. The frontend
    reads the response and calls POST /api/tasks for each task. This test
    pins the returned ordering and priority so the frontend can rely on it.
    """
    mock_response = _make_llm_response(VALID_LLM_DATA)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=mock_response)
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert tasks[0]["title"] == "Gather W-2s and 1099s from employers and banks"
    assert tasks[0]["priority"] == "P1"
    assert tasks[1]["title"] == "Pick a filing method"
    assert tasks[1]["priority"] == "P1"
    assert tasks[2]["title"] == "Fill out and review the return"
    assert tasks[2]["priority"] == "P1"
    assert tasks[3]["title"] == "Submit the return and save confirmation"
    assert tasks[3]["priority"] == "P2"
    # The endpoint must not write to ostk directly; the frontend does that.
    assert mock_ostk.add_task.call_count == 0


@pytest.mark.asyncio
async def test_dream_still_returns_plan_when_task_persistence_fails(client):
    """If ostk fails to save a task, the endpoint should still return the plan."""
    from services.ostk import OstkError

    mock_response = _make_llm_response(VALID_LLM_DATA)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=mock_response)
        mock_ostk.add_task = AsyncMock(side_effect=OstkError("disk full"))

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    # The endpoint should still succeed with the generated plan
    assert resp.status_code == 200
    data = resp.json()
    assert data["goal"]["title"] == "Get taxes filed"
    assert len(data["tasks"]) == 4


# --- done_looks_like in fallback ---

@pytest.mark.asyncio
async def test_dream_fallback_uses_done_looks_like_in_description(client):
    """When falling back, the done_looks_like text should appear in the goal description."""
    with (
        patch("routers.onboarding._resolve_api_key", return_value=""),
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
            "done_looks_like": "Taxes filed and no penalties",
        })

    data = resp.json()
    assert data["goal"]["description"] == "Taxes filed and no penalties"


# --- Auto-labeling for the persist helper ---

@pytest.mark.asyncio
async def test_persist_tasks_schedules_auto_labels():
    """The onboarding _persist_tasks helper must run auto-labeling on each task.

    Even though the dream endpoint currently lets the frontend do persistence,
    the helper exists and must wire auto-labeling so any future caller stays
    consistent with POST /api/tasks.
    """
    from routers.onboarding import _persist_tasks, DreamResponse, GoalItem, TaskItem

    plan = DreamResponse(
        goal=GoalItem(title="Get taxes filed", description="File without stress"),
        tasks=[
            TaskItem(title="Gather W-2s", priority="P1"),
            TaskItem(title="Pick a filing method", priority="P1"),
        ],
    )

    with (
        patch("routers.onboarding.ostk") as mock_ostk,
        patch("routers.onboarding.schedule_auto_labels") as mock_schedule,
    ):
        mock_ostk.add_task = AsyncMock(side_effect=[
            "added 601: Gather W-2s [P1]",
            "added 602: Pick a filing method [P1]",
        ])
        await _persist_tasks(plan)

    assert mock_schedule.call_count == 2
    first_args, _ = mock_schedule.call_args_list[0]
    assert first_args == ("601", "Gather W-2s", "")
    second_args, _ = mock_schedule.call_args_list[1]
    assert second_args == ("602", "Pick a filing method", "")


@pytest.mark.asyncio
async def test_persist_tasks_skips_auto_labels_on_ostk_failure():
    """If ostk.add_task fails, do not schedule auto-labels for that task."""
    from routers.onboarding import _persist_tasks, DreamResponse, GoalItem, TaskItem
    from services.ostk import OstkError

    plan = DreamResponse(
        goal=GoalItem(title="Test goal", description="desc"),
        tasks=[
            TaskItem(title="task that fails", priority="P1"),
            TaskItem(title="task that succeeds", priority="P1"),
        ],
    )

    with (
        patch("routers.onboarding.ostk") as mock_ostk,
        patch("routers.onboarding.schedule_auto_labels") as mock_schedule,
    ):
        mock_ostk.add_task = AsyncMock(side_effect=[
            OstkError("disk full"),
            "added 701: task that succeeds [P1]",
        ])
        await _persist_tasks(plan)

    # Only the successful task gets auto-labeled.
    assert mock_schedule.call_count == 1
    args, _ = mock_schedule.call_args_list[0]
    assert args == ("701", "task that succeeds", "")


# --- POST /api/onboarding/intent ---

@pytest.mark.asyncio
async def test_intent_writing_returns_writing_starter_pack(client):
    """Writing intent returns writer agent templates."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "writing"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-writer-blog-post" in ids
    assert "builtin-writer-proofreader" in ids
    # Default-selected items are correctly flagged
    defaults = [item for item in pack if item["default_selected"]]
    assert len(defaults) > 0
    # Each item has the required fields
    for item in pack:
        assert item["kind"] == "agent"
        assert item["name"]
        assert item["description"]


@pytest.mark.asyncio
async def test_intent_coding_returns_coding_starter_pack(client):
    """Coding intent returns engineering agent templates."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "coding"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    ids = [item["id"] for item in pack]
    assert "builtin-builder" in ids
    assert "builtin-diagnose" in ids
    assert "builtin-eng-write-tests" in ids
    # Builder and Diagnose should be default-selected
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-builder"]["default_selected"] is True
    assert by_id["builtin-diagnose"]["default_selected"] is True


@pytest.mark.asyncio
async def test_intent_work_role_includes_role_in_request(client):
    """work_role intent with a role string still returns a valid pack."""
    resp = await client.post("/api/onboarding/intent", json={
        "intent": "work_role",
        "role": "Product manager",
    })
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-pm-prd" in ids


@pytest.mark.asyncio
async def test_intent_sales_returns_sales_starter_pack(client):
    """Sales intent returns sales agent templates, not PM templates."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "sales"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-sales-prospect-research" in ids
    assert "builtin-sales-cold-outreach" in ids
    assert "builtin-sales-call-prep" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-sales-prospect-research"]["default_selected"] is True
    assert by_id["builtin-sales-cold-outreach"]["default_selected"] is True
    assert "builtin-pm-prd" not in ids, "Sales intent must not include PM templates"


@pytest.mark.asyncio
async def test_intent_general_returns_universal_starter_pack(client):
    """General intent returns universal templates, not PM templates."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "general"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-builder" in ids
    assert "builtin-research" in ids
    assert "builtin-brainstorm" in ids
    assert "builtin-explain-plain" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-builder"]["default_selected"] is True
    assert "builtin-pm-prd" not in ids, "General intent must not include PM templates"


@pytest.mark.asyncio
async def test_intent_invalid_returns_422(client):
    """An unrecognised intent value should be rejected by Pydantic validation."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "gaming"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_intent_marketing_returns_campaign_brief(client):
    """Marketing intent returns Campaign Brief as the primary agent."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "marketing"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-marketing-campaign-brief" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-marketing-campaign-brief"]["default_selected"] is True


@pytest.mark.asyncio
async def test_intent_founder_returns_investor_update(client):
    """Founder intent returns Investor Update as the primary agent."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "founder"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-founder-investor-update" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-founder-investor-update"]["default_selected"] is True


@pytest.mark.asyncio
async def test_intent_support_returns_customer_reply(client):
    """Support intent returns Customer Reply as the primary agent."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "support"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-support-customer-reply" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-support-customer-reply"]["default_selected"] is True


@pytest.mark.asyncio
async def test_intent_designer_returns_design_critique(client):
    """Designer intent returns Design Critique as the primary agent."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "designer"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-designer-design-critique" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-designer-design-critique"]["default_selected"] is True


@pytest.mark.asyncio
async def test_intent_all_packs_resolve_to_non_empty_list(client):
    """Every intent in the mapping resolves to at least one valid template."""
    all_intents = [
        "writing", "personal", "coding", "research", "work_role",
        "sales", "general", "marketing", "founder", "support", "designer",
    ]
    for intent in all_intents:
        resp = await client.post("/api/onboarding/intent", json={"intent": intent})
        assert resp.status_code == 200, f"intent={intent} returned {resp.status_code}"
        pack = resp.json()["starter_pack"]
        assert len(pack) > 0, f"intent={intent} returned empty starter_pack"


@pytest.mark.asyncio
async def test_first_runs_writing_returns_three_hints(client):
    resp = await client.get("/api/onboarding/first-runs?intent=writing")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["hints"]) == 3
    for h in data["hints"]:
        assert "label" in h
        assert "seed" in h
        assert h["kind"] in ("chat", "task")


@pytest.mark.asyncio
async def test_first_runs_all_intents_return_three_hints(client):
    for intent in ["writing", "personal", "coding", "research", "work_role", "sales", "general"]:
        resp = await client.get(f"/api/onboarding/first-runs?intent={intent}")
        assert resp.status_code == 200
        hints = resp.json()["hints"]
        assert len(hints) == 3, f"{intent} returned {len(hints)} hints"


@pytest.mark.asyncio
async def test_first_runs_unknown_intent_defaults_to_writing(client):
    resp = await client.get("/api/onboarding/first-runs?intent=unknown_xyz")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["hints"]) == 3


@pytest.mark.asyncio
async def test_first_runs_default_intent_is_writing(client):
    resp = await client.get("/api/onboarding/first-runs")
    assert resp.status_code == 200
    hints = resp.json()["hints"]
    labels = [h["label"] for h in hints]
    assert any("blog" in lbl.lower() or "draft" in lbl.lower() or "headline" in lbl.lower() for lbl in labels)


@pytest.mark.asyncio
async def test_enable_myos_hooks_success(client):
    """enable-myos-hooks runs myos-track.sh and returns enabled:True on success."""
    with patch("routers.onboarding.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        resp = await client.post("/api/onboarding/enable-myos-hooks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["method"] == "track"


@pytest.mark.asyncio
async def test_enable_myos_hooks_scope_everywhere(client):
    """scope=everywhere passes --global to myos-track.sh."""
    with patch("routers.onboarding.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        resp = await client.post("/api/onboarding/enable-myos-hooks", json={"scope": "everywhere"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["method"] == "everywhere"
    args = mock_run.call_args[0][0]
    assert "--global" in args


@pytest.mark.asyncio
async def test_enable_myos_hooks_scope_repo_with_path(client):
    """scope=repo passes the provided path to myos-track.sh."""
    with patch("routers.onboarding.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        resp = await client.post(
            "/api/onboarding/enable-myos-hooks",
            json={"scope": "repo", "path": "/home/user/myproject"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["method"] == "repo"
    args = mock_run.call_args[0][0]
    assert "/home/user/myproject" in args


@pytest.mark.asyncio
async def test_enable_myos_hooks_scope_repo_missing_path(client):
    """scope=repo without path returns enabled:False."""
    resp = await client.post("/api/onboarding/enable-myos-hooks", json={"scope": "repo"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert "path" in data["error"]


@pytest.mark.asyncio
async def test_enable_myos_hooks_scope_myos_only(client):
    """scope=myos-only returns success without calling myos-track.sh."""
    with patch("routers.onboarding.subprocess.run") as mock_run:
        resp = await client.post("/api/onboarding/enable-myos-hooks", json={"scope": "myos-only"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["method"] == "myos-only"
    mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_intent_endpoint_does_not_break_dream(client):
    """The dream endpoint still works after the intent endpoint is added."""
    with (
        patch("routers.onboarding._resolve_api_key", return_value=""),
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        from unittest.mock import AsyncMock
        mock_ostk.add_task = AsyncMock(return_value="created t-1")
        resp = await client.post("/api/onboarding/dream", json={"dreading": "do my taxes"})
    assert resp.status_code == 200
    assert "goal" in resp.json()
    assert "tasks" in resp.json()
