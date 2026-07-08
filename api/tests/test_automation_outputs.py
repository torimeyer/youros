"""Tests for services.automation_outputs.

Every automation run (fleet, solo agent, workflow) must produce a
persistent .md file in ``~/.youros/files/`` so it shows up on the Files
tab, and must parse any next-step bullets into real tasks via the
shared ``schedule_auto_labels`` hook. These tests lock that contract.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from httpx import ASGITransport, AsyncClient

from main import app


# ---------------------------------------------------------------------------
# Next-step parsing
# ---------------------------------------------------------------------------


def test_parse_next_steps_extracts_todo_lines():
    """TODO: lines become next-step items anywhere in the body."""
    from services.automation_outputs import parse_next_steps

    content = (
        "Here is what we did today.\n"
        "\n"
        "- TODO: Ship the landing page.\n"
        "TODO: Update the onboarding copy.\n"
        "- Something unrelated\n"
    )
    items = parse_next_steps(content)
    assert "Ship the landing page" in items
    assert "Update the onboarding copy" in items
    assert len(items) == 2


def test_parse_next_steps_extracts_unchecked_checkboxes():
    """- [ ] and * [ ] bullets become next-step items."""
    from services.automation_outputs import parse_next_steps

    content = (
        "## Outcome\n\n"
        "- [ ] Email the team\n"
        "* [ ] Close the open needles\n"
        "- [x] Already done, skip me\n"
    )
    items = parse_next_steps(content)
    assert "Email the team" in items
    assert "Close the open needles" in items
    assert "Already done, skip me" not in items


def test_parse_next_steps_reads_dedicated_section():
    """Bullets inside a Next steps / Follow-ups section are captured too."""
    from services.automation_outputs import parse_next_steps

    content = (
        "# Summary\n"
        "\n"
        "Some prose here.\n"
        "\n"
        "## Next steps\n"
        "\n"
        "- Send the recap to stakeholders\n"
        "- Book a follow-up meeting\n"
        "1. Publish the blog post\n"
        "\n"
        "## Unrelated heading\n"
        "- This bullet should not be captured.\n"
    )
    items = parse_next_steps(content)
    assert "Send the recap to stakeholders" in items
    assert "Book a follow-up meeting" in items
    assert "Publish the blog post" in items
    assert "This bullet should not be captured" not in items


def test_parse_next_steps_dedupes_case_insensitive():
    """Duplicate items are collapsed, preserving first-seen order."""
    from services.automation_outputs import parse_next_steps

    content = (
        "TODO: Do the thing\n"
        "## Next steps\n"
        "- Do the thing\n"
        "- Another thing\n"
    )
    items = parse_next_steps(content)
    assert items == ["Do the thing", "Another thing"]


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------


def test_write_output_creates_markdown_file(tmp_path: Path):
    from services.automation_outputs import write_output

    files_dir = tmp_path / "files"
    # No embedded "## Next steps" section here; the writer should add one
    # from the extracted TODO bullets.
    content = (
        "Walked the product through five onboarding screens. Findings "
        "include copy drift on the welcome card and a broken link on the "
        "tour step. Recommended follow-ups below.\n\n"
        "TODO: Fix the welcome card copy.\n"
        "TODO: Repair the broken tour link.\n"
    )
    target = write_output(
        automation_kind="agent",
        name="ia-review-session-9",
        content=content,
        files_dir=files_dir,
    )
    assert target is not None
    assert target.parent == files_dir
    assert target.suffix == ".md"
    body = target.read_text()
    assert "source: ia-review-session-9" in body
    assert "kind: agent-output" in body
    assert "## Next steps" in body
    assert "- [ ] Fix the welcome card copy" in body


def test_write_output_skips_short_summary(tmp_path: Path):
    from services.automation_outputs import write_output

    files_dir = tmp_path / "files"
    target = write_output(
        automation_kind="agent",
        name="quick-check",
        content="Done.",
        files_dir=files_dir,
    )
    assert target is None


def test_write_output_same_minute_reruns_do_not_collide(tmp_path: Path):
    """Two runs of the same automation in the same minute both survive."""
    from services.automation_outputs import write_output

    files_dir = tmp_path / "files"
    content = (
        "Generated landing page for the Build a Website fleet. Five "
        "sections, inline CSS, stock images. Ready for review."
    )
    metadata = {"fleet_id": "fleet-build-website"}
    first = write_output(
        "fleet", "fleet-build-website", content,
        metadata=metadata, files_dir=files_dir,
    )
    second = write_output(
        "fleet", "fleet-build-website", content,
        metadata=metadata, files_dir=files_dir,
    )
    assert first is not None and second is not None
    assert first != second
    assert first.exists() and second.exists()


def test_write_output_allows_fleet_member_through_infra_filter(tmp_path: Path):
    """Fleet members whose names look like infra prefixes still land.

    Fleet agent names like ``fleet-build-website-product-manager`` match
    the shared infra-noise regex. Real fleet members set ``fleet_id``
    on their metadata so the writer lets them through. Regression guard
    for the Website Builder "fleet finished but Files tab empty" bug.
    """
    from services.automation_outputs import write_output

    files_dir = tmp_path / "files"
    content = (
        "Produced requirements.md with 10 bullet PRD: audience, 3 key "
        "pages, 3 must-have features, 3 success criteria. Plain-language."
    )
    target = write_output(
        automation_kind="fleet",
        name="fleet-build-website-product-manager",
        content=content,
        metadata={"fleet_id": "fleet-build-website", "fleet_name": "Build a Website"},
        files_dir=files_dir,
    )
    assert target is not None
    body = target.read_text()
    assert "fleet_id: fleet-build-website" in body
    assert "fleet_name: Build a Website" in body


# ---------------------------------------------------------------------------
# Task auto-creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_create_tasks_creates_one_per_item_with_labels():
    """Each next-step item becomes a task with a plain-language description,
    and schedule_auto_labels fires for each so auto-labels land.
    """
    from services import automation_outputs

    next_steps = [
        "Send the recap to stakeholders",
        "Publish the blog post",
    ]

    with patch("services.ostk.ostk.add_task", new=AsyncMock(side_effect=[
        "added →501: Send the recap to stakeholders [P2]\n",
        "added →502: Publish the blog post [P2]\n",
    ])) as mock_add, patch(
        "services.ostk.ostk.list_tasks", new=AsyncMock(return_value=[])
    ), patch(
        "services.task_labeling.schedule_auto_labels"
    ) as mock_schedule:
        created = await automation_outputs.auto_create_tasks(
            next_steps,
            source_name="weekly-review",
            automation_kind="workflow",
        )

    assert len(created) == 2
    assert created[0]["title"] == "Send the recap to stakeholders"
    assert created[1]["id"] == "→502"
    # Description is plain-language and ties back to the source run.
    for task in created:
        assert "weekly-review" in task["description"]
        assert "next step" in task["description"].lower()
    # schedule_auto_labels was called for each task.
    assert mock_schedule.call_count == 2
    called_titles = [call.args[1] for call in mock_schedule.call_args_list]
    assert "Send the recap to stakeholders" in called_titles
    assert "Publish the blog post" in called_titles
    # ostk.add_task received both titles.
    assert mock_add.call_count == 2


@pytest.mark.asyncio
async def test_auto_create_tasks_swallows_single_item_failure():
    """One failing add_task does not abort the rest of the list."""
    from services import automation_outputs
    from services.ostk import OstkError

    with patch(
        "services.ostk.ostk.add_task",
        new=AsyncMock(side_effect=[
            OstkError("task was deleted recently"),
            "added →503: Second item [P2]\n",
        ]),
    ), patch(
        "services.ostk.ostk.list_tasks", new=AsyncMock(return_value=[])
    ), patch("services.task_labeling.schedule_auto_labels"):
        created = await automation_outputs.auto_create_tasks(
            ["First item", "Second item"],
            source_name="ia-review",
            automation_kind="agent",
        )
    assert len(created) == 1
    assert created[0]["id"] == "→503"


# ---------------------------------------------------------------------------
# Integration: fleet completion writes artifact + creates tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fleet_member_complete_writes_md_file(tmp_path: Path):
    """A fleet member calling /complete writes an .md file in
    ~/.youros/files/ even though its name matches the infra-noise regex.
    """
    from routers import agents as agents_module
    from routers.agents import agent_metadata

    agent_name = "fleet-build-website-product-manager"
    agent_metadata[agent_name] = {
        "spawned_at": "2026-04-16T10:00:00+00:00",
        "budget": "0.25",
        "model": "claude-haiku-4-20250514",
        "source": "claude-code",
        "template": "Build a Website",
        "fleet_id": "fleet-build-website",
        "fleet_name": "Build a Website",
        "role": "Product Manager",
        "status": "running",
    }

    fake_home = tmp_path / "home"
    files_dir = fake_home / ".youros" / "files"

    summary = (
        "Wrote the PRD for the Build a Website fleet. Audience: internal "
        "product managers. Three key pages: Home, Browse, Detail. Three "
        "must-have features: search, comments, approvals. Three success "
        "criteria: 90 percent approval within 24 hours, under 2 seconds "
        "load, zero crashes in the first week.\n\n"
        "TODO: Review the PRD with the engineering lead.\n"
        "TODO: Confirm the approval workflow with the CTO.\n"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch.object(agents_module, "MYOS_FILES_DIR", files_dir), \
                 patch("routers.agents._save_agent_state"), \
                 patch("routers.agents.ostk") as mock_ostk, \
                 patch("services.ostk.ostk.add_task", new=AsyncMock(
                     side_effect=[
                         "added →801: Review the PRD with the engineering lead [P2]\n",
                         "added →802: Confirm the approval workflow with the CTO [P2]\n",
                     ])) as mock_add, \
                 patch("services.ostk.ostk.list_tasks", new=AsyncMock(return_value=[])), \
                 patch("services.task_labeling.schedule_auto_labels") as mock_schedule:
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "", "daemon_running": False, "agents": [],
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk.append_nudge_reply = AsyncMock(return_value={
                    "message": "ok", "timestamp": "2026-04-16T10:01:00+00:00",
                })

                resp = await client.post(
                    f"/api/agents/{agent_name}/complete",
                    json={"summary": summary},
                )
                assert resp.status_code == 200

                # File written.
                artifacts = list(files_dir.glob(f"{agent_name}-*.md"))
                assert len(artifacts) == 1, f"expected one artifact, got {artifacts}"
                body = artifacts[0].read_text()
                assert "source: fleet-build-website-product-manager" in body
                assert "fleet_id: fleet-build-website" in body
                assert "fleet_name: Build a Website" in body
                assert "## Next steps" in body
                assert "- [ ] Review the PRD with the engineering lead" in body

                # Tasks created with plain-language descriptions. The auto-create
                # pass is fire-and-forget; give the event loop enough ticks for
                # the background task to start and hit the mock. Must stay
                # INSIDE the patch context so the mock is still active when the
                # task actually runs.
                import asyncio as _asyncio
                for _ in range(20):
                    await _asyncio.sleep(0)
                    if mock_add.call_count >= 2:
                        break
                assert mock_add.call_count >= 2
                call_titles = [c.args[0] for c in mock_add.call_args_list]
                assert any("Review the PRD" in t for t in call_titles)
                assert any("approval workflow" in t for t in call_titles)
                call_descs = [
                    c.kwargs.get("description", "")
                    for c in mock_add.call_args_list
                ]
                assert all(
                    "fleet-build-website-product-manager" in d for d in call_descs
                )
                # schedule_auto_labels called for each task.
                assert mock_schedule.call_count >= 2
        finally:
            agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_recent_docs_includes_fleet_artifact(tmp_path: Path):
    """After a fleet member writes its .md via /complete, /docs/recent
    must return it on the very next call.
    """
    from routers import agents as agents_module
    from routers import projects as projects_module
    from routers.agents import agent_metadata

    agent_name = "fleet-build-website-frontend-developer"
    agent_metadata[agent_name] = {
        "spawned_at": "2026-04-16T10:00:00+00:00",
        "budget": "0.25",
        "model": "claude-haiku-4-20250514",
        "source": "claude-code",
        "fleet_id": "fleet-build-website",
        "fleet_name": "Build a Website",
        "role": "Frontend Developer",
        "status": "running",
    }

    fake_home = tmp_path / "home"
    files_dir = fake_home / ".youros" / "files"

    summary = (
        "Produced index.html for the Build a Website fleet. Single-file "
        "landing page with three cards labeled Review, Comment, Approve. "
        "Inline CSS, under one hundred lines total. Ready for review."
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch.object(agents_module, "MYOS_FILES_DIR", files_dir), \
                 patch("routers.agents._save_agent_state"), \
                 patch("routers.agents.ostk") as mock_ostk, \
                 patch.object(
                     projects_module,
                     "Path",
                     side_effect=lambda *a, **k: Path(*a, **k),
                 ):
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "", "daemon_running": False, "agents": [],
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk.append_nudge_reply = AsyncMock(return_value={
                    "message": "ok", "timestamp": "2026-04-16T10:01:00+00:00",
                })

                resp = await client.post(
                    f"/api/agents/{agent_name}/complete",
                    json={"summary": summary},
                )
            assert resp.status_code == 200

            # Point YOUROS_HOME at the fake home so /docs/recent scans
            # the same files_dir we just wrote to.
            # /docs/recent uses youros_home() which reads YOUROS_HOME, not Path.home().
            import os as _os
            with patch.dict(_os.environ, {"YOUROS_HOME": str(fake_home / ".youros")}):
                resp2 = await client.get("/api/docs/recent")
            assert resp2.status_code == 200
            files = resp2.json().get("files", [])
            names = [f["name"] for f in files]
            matching = [n for n in names if n.startswith(agent_name)]
            assert matching, (
                f"fleet artifact missing from /docs/recent; got {names}"
            )
        finally:
            agent_metadata.pop(agent_name, None)


# ---------------------------------------------------------------------------
# Integration: workflow completion writes artifact + creates tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_run_writes_rollup_artifact_and_tasks(tmp_path: Path):
    """run_workflow's terminal branch must write a rollup .md file to
    ~/.youros/files/ and create a task per aggregated next-step bullet.
    """
    from services import workflows, automation_outputs

    fake_home = tmp_path / "home"
    files_dir = fake_home / ".youros" / "files"

    # Simulate a completed workflow by invoking the writer helper
    # directly. The full run_workflow path also fires this helper at
    # terminal state, and test_workflows.py already covers that path;
    # we only need to prove the artifact + task side-effects here.
    wf = {
        "id": "weekly-review",
        "name": "Weekly Review",
        "status": workflows.WF_DONE,
        "steps": [
            {
                "id": "step-1",
                "agent_name": "gather-closed-tasks",
                "status": workflows.STEP_DONE,
                "started_at": "2026-04-16T10:00:00+00:00",
                "finished_at": "2026-04-16T10:00:30+00:00",
            },
        ],
    }
    run_record = {
        "run_id": "abc12345",
        "status": workflows.WF_DONE,
        "duration_seconds": 30.0,
    }

    # Stub the step agent's summary so the writer has body text and
    # next-step bullets to work with.
    fake_metadata = {
        "gather-closed-tasks": {
            "summary": (
                "Closed tasks this week: onboarding polish, nav redesign, "
                "brief cleanup. Quality is stable. Two items deferred.\n\n"
                "## Next steps\n"
                "- Publish the weekly recap.\n"
                "- Schedule next week's planning session.\n"
            )
        }
    }

    with patch.object(automation_outputs, "MYOS_FILES_DIR", files_dir), \
         patch("routers.agents.agent_metadata", fake_metadata), \
         patch("services.ostk.ostk.add_task", new=AsyncMock(
             side_effect=[
                 "added →901: Publish the weekly recap [P2]\n",
                 "added →902: Schedule next week's planning session [P2]\n",
             ])) as mock_add, \
         patch("services.ostk.ostk.list_tasks", new=AsyncMock(return_value=[])), \
         patch("services.task_labeling.schedule_auto_labels") as mock_schedule:
        await workflows._write_workflow_artifact(wf, run_record)

    artifacts = list(files_dir.glob("weekly-review-*.md"))
    assert len(artifacts) == 1, f"expected rollup artifact, got {artifacts}"
    body = artifacts[0].read_text()
    assert "Workflow 'Weekly Review' finished with status: done" in body
    assert "gather-closed-tasks" in body
    assert "Publish the weekly recap" in body
    assert "## Next steps" in body

    # Two tasks created with plain-language descriptions.
    assert mock_add.call_count == 2
    descs = [c.kwargs.get("description", "") for c in mock_add.call_args_list]
    assert all("Weekly Review" in d for d in descs)
    # schedule_auto_labels fired per task.
    assert mock_schedule.call_count == 2


# ---------------------------------------------------------------------------
# Dedup: within-run, across-run, fleet-wide
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_create_tasks_dedupes_within_run():
    """Identical next-step titles in one call create one task, not N.

    Guards against fleet placeholders (or any artifact) that emit the
    same "Re-run Agent" bullet multiple times turning into multiple
    tasks.
    """
    from services import automation_outputs

    next_steps = [
        "Re-run Agent with a larger time budget",
        "Re-run Agent with a larger time budget",
        "Re-run Agent with a larger time budget.",  # trailing period variant
        "Re-run Agent With A Larger Time Budget",  # case variant
        "Review the partial output",
    ]

    with patch("services.ostk.ostk.add_task", new=AsyncMock(side_effect=[
        "added →701: Re-run Agent with a larger time budget [P2]\n",
        "added →702: Review the partial output [P2]\n",
    ])) as mock_add, patch(
        "services.ostk.ostk.list_tasks", new=AsyncMock(return_value=[])
    ), patch("services.task_labeling.schedule_auto_labels"):
        created = await automation_outputs.auto_create_tasks(
            next_steps,
            source_name="fleet-demo-timeout",
            automation_kind="fleet",
        )

    assert len(created) == 2, f"expected 2 unique tasks, got {created}"
    assert mock_add.call_count == 2
    titles = [c.args[0] for c in mock_add.call_args_list]
    assert titles[0] == "Re-run Agent with a larger time budget"
    assert titles[1] == "Review the partial output"


@pytest.mark.asyncio
async def test_auto_create_tasks_dedupes_against_existing_open_tasks():
    """If an open task with the same normalized title already exists,
    skip creation so the Tasks list never grows duplicates across runs.
    """
    from services import automation_outputs

    existing_open = [
        {"id": "→400", "title": "Re-run Agent with a larger time budget"},
        {"id": "→401", "title": "Some other unrelated open task"},
    ]

    with patch("services.ostk.ostk.add_task", new=AsyncMock(side_effect=[
        "added →702: Brand new item [P2]\n",
    ])) as mock_add, patch(
        "services.ostk.ostk.list_tasks",
        new=AsyncMock(return_value=existing_open),
    ), patch("services.task_labeling.schedule_auto_labels"):
        created = await automation_outputs.auto_create_tasks(
            [
                "re-run agent with a larger time budget",  # lower + no period
                "Brand new item",
            ],
            source_name="fleet-demo-timeout",
            automation_kind="fleet",
        )

    assert len(created) == 1, f"expected 1 new task, got {created}"
    assert mock_add.call_count == 1
    assert mock_add.call_args_list[0].args[0] == "Brand new item"


@pytest.mark.asyncio
async def test_auto_create_tasks_fleet_five_roles_same_bullet_one_task():
    """Simulate a fleet of five roles all surfacing the same next-step.

    The first role creates the task. Each subsequent role lists the
    open tasks, sees the already-created title, and skips creating a
    duplicate. Net result: one task for the whole fleet run, not five.
    """
    from services import automation_outputs

    # Shared mutable store standing in for the ostk open-task list. The
    # mocked add_task appends into it so subsequent list_tasks calls
    # observe the freshly-created task, matching real ostk semantics.
    open_store: list[dict[str, str]] = []

    async def fake_list_tasks(*args, **kwargs):
        return list(open_store)

    async def fake_add_task(title, priority="P2", description="", ac=""):
        task_id = f"→9{len(open_store) + 1:02d}"
        open_store.append({"id": task_id, "title": title})
        return f"added {task_id}: {title} [{priority}]\n"

    bullet = "Re-run Agent with a larger time budget to get a full output"

    with patch("services.ostk.ostk.add_task", new=AsyncMock(
        side_effect=fake_add_task
    )) as mock_add, patch(
        "services.ostk.ostk.list_tasks", new=AsyncMock(side_effect=fake_list_tasks)
    ), patch("services.task_labeling.schedule_auto_labels"):
        # Five fleet roles each fire their own auto-create pass. In prod
        # these run concurrently from fire-and-forget tasks; awaiting them
        # sequentially is still a valid test of the dedup gate because
        # the across-run check re-lists before each role's work.
        total_created: list[dict] = []
        for _ in range(5):
            total_created.extend(
                await automation_outputs.auto_create_tasks(
                    [bullet],
                    source_name="fleet-demo-website",
                    automation_kind="fleet",
                )
            )

    assert len(total_created) == 1, (
        f"expected one task across fleet, got {total_created}"
    )
    assert mock_add.call_count == 1
    assert len(open_store) == 1

