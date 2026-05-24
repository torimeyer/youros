"""Tests for the Tasks audit feature.

No real LLM traffic ever runs. The classifier boundary
``services.task_audit._classify_with_claude`` is patched on every test
so the verdict is deterministic and the Anthropic SDK never initializes.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services import task_audit


def _make_task(
    id: str = "t-1",
    title: str = "Build something",
    description: str = "",
    created_at: str = "2024-01-01T00:00:00Z",
    updated_at: str = "",
) -> dict:
    task = {
        "id": id,
        "title": title,
        "priority": "P1",
        "status": "open",
        "description": description,
        "created_at": created_at,
    }
    if updated_at:
        task["updated_at"] = updated_at
    return task


def _old_ts() -> str:
    return "2024-01-01T00:00:00+00:00"


def _fresh_ts() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()


@pytest.fixture(autouse=True)
def _reset_jobs():
    """Ensure each test starts with a clean job dict."""
    task_audit._reset_state_for_tests()
    yield
    task_audit._reset_state_for_tests()


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_verdict_goes_to_review_list():
    """A classifier verdict of completed should park the task in review.

    The service never auto closes, so even a confident ``completed``
    lands in the review list with plain language so the user can approve
    it.
    """
    tasks = [_make_task(id="t-100", title="Add dark mode toggle", created_at=_old_ts())]
    verdict_json = (
        '{"verdict": "completed", "evidence": "exists in app/src/theme.tsx", '
        '"duplicate_of": null}'
    )
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit,
            "_classify_with_claude",
            AsyncMock(return_value=verdict_json),
        ) as mock_classify:
            job_id = task_audit.start_audit_job()
            # Run the background coroutine to completion synchronously.
            await task_audit.run_audit_job(job_id)

    state = task_audit.get_audit_status(job_id)
    assert state is not None
    assert state["status"] == "done"
    assert state["results"]["skipped_irl"] == 0
    review = state["results"]["review"]
    assert len(review) == 1
    entry = review[0]
    assert entry["task_id"] == "t-100"
    assert entry["reason_guess"] == "completed"
    assert entry["reason_label"] == "This looks already done"
    assert "app/src/theme.tsx" in entry["evidence"]
    mock_classify.assert_awaited()


@pytest.mark.asyncio
async def test_recently_touched_task_is_skipped_entirely_no_close_suggestion():
    """A recently touched task must never appear in the review list.

    Recency is never grounds for a close suggestion. The old behavior
    auto-parked fresh work in review with a "you just touched this"
    label, which made no sense to the user. The audit now skips
    recently touched tasks entirely: the classifier is never called,
    and the task does not appear in any bucket.
    """
    tasks = [_make_task(id="t-200", title="Write docs", created_at=_fresh_ts())]
    classify_mock = AsyncMock(return_value='{"verdict": "completed", "evidence": "x", "duplicate_of": null}')
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(task_audit, "_classify_with_claude", classify_mock):
            job_id = task_audit.start_audit_job()
            await task_audit.run_audit_job(job_id)

    state = task_audit.get_audit_status(job_id)
    assert state["status"] == "done"
    # Recently touched: no review entry, no close suggestion, no classifier call.
    assert state["results"]["review"] == []
    assert state["results"]["closed"] == []
    assert state["results"]["skipped_irl"] == 0
    classify_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_skipped_irl_verdict_increments_counter_and_never_closes():
    """Real-life chores bump the counter and stay off the review list."""
    tasks = [_make_task(id="t-300", title="Buy groceries", created_at=_old_ts())]
    verdict_json = (
        '{"verdict": "skipped_irl", "evidence": "errand not code work", '
        '"duplicate_of": null}'
    )
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit, "_classify_with_claude", AsyncMock(return_value=verdict_json)
        ):
            job_id = task_audit.start_audit_job()
            await task_audit.run_audit_job(job_id)

    state = task_audit.get_audit_status(job_id)
    assert state["results"]["skipped_irl"] == 1
    assert state["results"]["review"] == []
    assert state["results"]["closed"] == []


@pytest.mark.asyncio
async def test_malformed_classifier_response_goes_to_review_with_plain_evidence():
    """If the LLM returns garbage, default to review with a plain note."""
    tasks = [_make_task(id="t-400", title="Refactor chat panel", created_at=_old_ts())]
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit,
            "_classify_with_claude",
            AsyncMock(return_value="this is not json at all"),
        ):
            job_id = task_audit.start_audit_job()
            await task_audit.run_audit_job(job_id)

    state = task_audit.get_audit_status(job_id)
    review = state["results"]["review"]
    assert len(review) == 1
    assert review[0]["reason_guess"] == "review"
    assert review[0]["evidence"] == "classifier output could not be parsed"


@pytest.mark.asyncio
async def test_regression_test_evidence_flows_into_classifier_prompt():
    """A matching regression test class should be passed to the classifier.

    Reproduces the thoroughness gap caught by needle 302: needle 241 had
    a passing ``TestGeminiContentBlobRegression`` class that the first
    audit pass missed because the classifier prompt never saw test file
    evidence. This test stubs ``_gather_test_evidence`` to pretend that
    test already exists, captures what the classifier receives, and
    asserts both the evidence block and the regression flag make it to
    the prompt. The classifier itself is mocked to return ``completed``
    so we can also assert the task lands in the review list as
    ``completed`` (never auto closed).
    """
    tasks = [
        _make_task(
            id="t-241",
            title="Gemini Content proto Blob error in chat follow-up after GIF history",
            created_at=_old_ts(),
        )
    ]
    fake_test_block = (
        "# test grep 'gemini' in api/tests\n"
        "api/tests/test_chat_providers.py:42:class TestGeminiContentBlobRegression:"
    )

    captured: dict[str, object] = {}

    async def fake_classify(
        task,
        evidence,
        test_evidence,
        has_regression_class,
        other_titles,
    ):
        captured["test_evidence"] = test_evidence
        captured["has_regression_class"] = has_regression_class
        captured["task_id"] = task.get("id")
        return (
            '{"verdict": "completed", "evidence": '
            '"regression test class TestGeminiContentBlobRegression exists", '
            '"duplicate_of": null}'
        )

    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit,
            "_gather_test_evidence",
            return_value=(fake_test_block, True),
        ):
            with patch.object(
                task_audit, "_classify_with_claude", side_effect=fake_classify
            ):
                job_id = task_audit.start_audit_job()
                await task_audit.run_audit_job(job_id)

    # Classifier must have received the test evidence and the flag.
    assert captured.get("task_id") == "t-241"
    assert "TestGeminiContentBlobRegression" in captured["test_evidence"]
    assert captured["has_regression_class"] is True

    # Verdict lands in review (never auto closed) with completed bucket.
    state = task_audit.get_audit_status(job_id)
    assert state is not None
    assert state["results"]["closed"] == []
    review = state["results"]["review"]
    assert len(review) == 1
    entry = review[0]
    assert entry["task_id"] == "t-241"
    assert entry["reason_guess"] == "completed"
    assert "regression test" in entry["evidence"].lower()


@pytest.mark.asyncio
async def test_build_prompt_mentions_regression_signal_and_review_framing():
    """System prompt must call out regression tests and review framing.

    Belt-and-suspenders check on the copy change: Tori wants the LLM to
    know its verdict is a recommendation, and to treat a regression test
    class as the strongest completion signal. Both strings must be in
    the prompt so we cannot silently drift off that standard.
    """
    task = {"id": "t-1", "title": "example", "description": ""}
    system, user = task_audit._build_prompt(
        task,
        evidence="(none)",
        test_evidence="(none)",
        has_regression_class=False,
        other_titles=[],
    )
    assert "RECOMMENDATION" in system
    assert "regression test" in system.lower()
    assert "strongest completion signal" in system.lower()
    # User prompt includes the regression note either way.
    assert "Regression signal" in user


def test_review_only_invariant_constant_exists():
    """The service must expose the invariant string for readers to find."""
    assert hasattr(task_audit, "AUDIT_REVIEW_ONLY_INVARIANT")
    text = task_audit.AUDIT_REVIEW_ONLY_INVARIANT
    assert "never auto closes" in text
    assert "approve" in text.lower()


@pytest.mark.asyncio
async def test_duplicate_verdict_exposes_duplicate_of_field():
    """Duplicate close suggestion must reference another OPEN task."""
    tasks = [
        _make_task(id="t-500", title="Add search bar", created_at=_old_ts()),
        _make_task(id="t-12", title="Search bar", created_at=_old_ts()),
    ]

    async def fake_classify(task, *args, **kwargs):
        if task["id"] == "t-500":
            return (
                '{"verdict": "duplicate", "evidence": "same as task t-12", '
                '"duplicate_of": "t-12"}'
            )
        return '{"verdict": "review", "evidence": "unsure", "duplicate_of": null}'

    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit, "_classify_with_claude", side_effect=fake_classify
        ):
            job_id = task_audit.start_audit_job()
            await task_audit.run_audit_job(job_id)

    state = task_audit.get_audit_status(job_id)
    review = state["results"]["review"]
    entry = next(e for e in review if e["task_id"] == "t-500")
    assert entry["reason_guess"] == "duplicate"
    assert entry["duplicate_of"] == "t-12"
    assert "t-12" in entry["reason_label"]


@pytest.mark.asyncio
async def test_closed_tasks_are_filtered_before_audit_runs():
    """Any row whose status is not 'open' must be dropped before classify.

    Defensive filter for the scope bug: ostk.list_tasks(status="open") is
    supposed to only return open tasks but the audit saw 26 rows when
    only 17 were open. The audit now filters to status == "open" before
    running so closed rows never reach the classifier regardless of how
    ostk framed the query.
    """
    tasks = [
        _make_task(id="t-open-1", title="Open task one", created_at=_old_ts()),
        {
            "id": "t-closed-1",
            "title": "Already closed",
            "priority": "P1",
            "status": "closed",
            "description": "",
            "created_at": _old_ts(),
        },
        {
            "id": "t-abandoned-1",
            "title": "Abandoned",
            "priority": "P1",
            "status": "abandoned",
            "description": "",
            "created_at": _old_ts(),
        },
        _make_task(id="t-open-2", title="Open task two", created_at=_old_ts()),
    ]

    classify_mock = AsyncMock(
        return_value='{"verdict": "review", "evidence": "unsure", "duplicate_of": null}'
    )
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(task_audit, "_classify_with_claude", classify_mock):
            job_id = task_audit.start_audit_job()
            await task_audit.run_audit_job(job_id)

    state = task_audit.get_audit_status(job_id)
    # total reflects only the open tasks, not the closed/abandoned rows.
    assert state["total"] == 2
    # Classifier was called exactly twice, once per open task.
    assert classify_mock.await_count == 2
    # Closed/abandoned tasks never appear anywhere in results.
    seen_ids = {
        e.get("task_id")
        for e in state["results"]["review"]
        + state["results"]["closed"]
    }
    assert "t-closed-1" not in seen_ids
    assert "t-abandoned-1" not in seen_ids


@pytest.mark.asyncio
async def test_audit_scope_matches_frontend_open_count_no_sessions_no_e2e():
    """Audit must only process tasks the user sees in the Tasks page.

    User-visible regression: user had 17 open tasks on screen but the
    audit ran over 26. Root cause: the Tasks page hides session-lifecycle
    rows auto-filed by the SessionStart hook and e2e smoke-test
    leftovers, but the audit called ``ostk.list_tasks(status="open")``
    raw and saw every row. The audit now shares the same visibility
    contract as the /api/tasks endpoint so the counts line up.

    This test mocks ``ostk.list_tasks`` with a mix of statuses plus a
    session row and an e2e leftover and asserts only the real open
    rows reach the classifier, and that the total the UI will read
    equals the user-visible open count.
    """
    tasks = [
        _make_task(id="t-real-1", title="Real user task one", created_at=_old_ts()),
        _make_task(id="t-real-2", title="Real user task two", created_at=_old_ts()),
        # Status-based noise that must never be audited.
        {
            "id": "t-closed-1",
            "title": "Already closed",
            "priority": "P1",
            "status": "closed",
            "description": "",
            "created_at": _old_ts(),
        },
        {
            "id": "t-inprog-1",
            "title": "In progress not open",
            "priority": "P1",
            "status": "in_progress",
            "description": "",
            "created_at": _old_ts(),
        },
        {
            "id": "t-review-1",
            "title": "Review not open",
            "priority": "P1",
            "status": "review",
            "description": "",
            "created_at": _old_ts(),
        },
        {
            "id": "t-nostatus-1",
            "title": "Missing status",
            "priority": "P1",
            "status": None,
            "description": "",
            "created_at": _old_ts(),
        },
        # Session-lifecycle row. Open, but auto-filed and hidden from UI.
        {
            "id": "t-session-1",
            "title": "Claude Code session claude-code-abc",
            "priority": "P1",
            "status": "open",
            "description": "Auto-filed by SessionStart hook",
            "created_at": _old_ts(),
        },
        # e2e smoke-test leftover. Open, but prefix-filtered in UI.
        {
            "id": "t-e2e-1",
            "title": "e2e-smoke probe run",
            "priority": "P1",
            "status": "open",
            "description": "",
            "created_at": _old_ts(),
        },
    ]

    classify_mock = AsyncMock(
        return_value='{"verdict": "review", "evidence": "unsure", "duplicate_of": null}'
    )
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(task_audit, "_classify_with_claude", classify_mock):
            job_id = task_audit.start_audit_job()
            await task_audit.run_audit_job(job_id)

    state = task_audit.get_audit_status(job_id)
    # Only the two real open tasks reach the classifier. Session, e2e,
    # closed, in_progress, review, and missing-status rows are dropped.
    assert state["total"] == 2, (
        f"audit scope should equal the user-visible open count, got {state['total']}"
    )
    assert classify_mock.await_count == 2

    # No noise rows appear in any result bucket.
    seen_ids = {
        e.get("task_id")
        for e in state["results"]["review"]
        + state["results"]["closed"]
    }
    for noise_id in (
        "t-closed-1",
        "t-inprog-1",
        "t-review-1",
        "t-nostatus-1",
        "t-session-1",
        "t-e2e-1",
    ):
        assert noise_id not in seen_ids, f"{noise_id} must never be audited"


@pytest.mark.asyncio
async def test_duplicate_verdict_only_suggests_close_when_other_task_is_open():
    """A duplicate close suggestion must reference an actually-open task.

    If the classifier says "duplicate" but names a duplicate_of that is
    not in the open tasks list, we fall through to the neutral review
    bucket instead of suggesting a close. This prevents the audit from
    inventing close suggestions based on similarity alone.
    """
    tasks = [
        _make_task(id="t-dup-src", title="Add search bar", created_at=_old_ts()),
        _make_task(id="t-dup-target", title="Search bar widget", created_at=_old_ts()),
    ]

    async def fake_classify(task, *args, **kwargs):
        if task["id"] == "t-dup-src":
            return (
                '{"verdict": "duplicate", "evidence": "same as t-dup-target", '
                '"duplicate_of": "t-dup-target"}'
            )
        return '{"verdict": "review", "evidence": "unsure", "duplicate_of": null}'

    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(task_audit, "_classify_with_claude", side_effect=fake_classify):
            job_id = task_audit.start_audit_job()
            await task_audit.run_audit_job(job_id)

    state = task_audit.get_audit_status(job_id)
    review = state["results"]["review"]
    src_entry = next(e for e in review if e["task_id"] == "t-dup-src")
    assert src_entry["reason_guess"] == "duplicate"
    assert src_entry["duplicate_of"] == "t-dup-target"


@pytest.mark.asyncio
async def test_already_implemented_task_gets_close_suggestion():
    """A completed verdict with code evidence produces a close suggestion."""
    tasks = [
        _make_task(
            id="t-done",
            title="Add dark mode toggle",
            created_at=_old_ts(),
        ),
    ]
    verdict_json = (
        '{"verdict": "completed", "evidence": '
        '"toggle implemented in app/src/theme.tsx line 42", '
        '"duplicate_of": null}'
    )
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit, "_classify_with_claude", AsyncMock(return_value=verdict_json)
        ):
            job_id = task_audit.start_audit_job()
            await task_audit.run_audit_job(job_id)

    state = task_audit.get_audit_status(job_id)
    review = state["results"]["review"]
    assert len(review) == 1
    entry = review[0]
    assert entry["task_id"] == "t-done"
    assert entry["reason_guess"] == "completed"
    assert entry["reason_label"] == "This looks already done"
    assert "theme.tsx" in entry["evidence"]


@pytest.mark.asyncio
async def test_archived_verdict_does_not_produce_close_suggestion():
    """Archived / no-longer-relevant verdicts must NOT suggest closing.

    The only grounds for a close suggestion are duplicate or already
    implemented. A task that sounds like old thinking stays open.
    """
    tasks = [
        _make_task(
            id="t-old-thinking",
            title="Port v1 widget to v2",
            created_at=_old_ts(),
        ),
    ]
    verdict_json = (
        '{"verdict": "archived", "evidence": "v1 widget subsystem was removed", '
        '"duplicate_of": null}'
    )
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit, "_classify_with_claude", AsyncMock(return_value=verdict_json)
        ):
            job_id = task_audit.start_audit_job()
            await task_audit.run_audit_job(job_id)

    state = task_audit.get_audit_status(job_id)
    review = state["results"]["review"]
    assert len(review) == 1
    entry = review[0]
    # Must NOT be a close suggestion. Neutral review bucket only.
    assert entry["reason_guess"] == "review"
    assert entry["reason_label"] == "Unsure, please confirm"


@pytest.mark.asyncio
async def test_duplicate_verdict_without_matching_open_task_degrades_to_review():
    """If duplicate_of points at no open task, suppress the close suggestion."""
    tasks = [
        _make_task(id="t-lonely", title="Add X", created_at=_old_ts()),
    ]
    verdict_json = (
        '{"verdict": "duplicate", "evidence": "same as t-ghost", '
        '"duplicate_of": "t-ghost"}'
    )
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit, "_classify_with_claude", AsyncMock(return_value=verdict_json)
        ):
            job_id = task_audit.start_audit_job()
            await task_audit.run_audit_job(job_id)

    state = task_audit.get_audit_status(job_id)
    review = state["results"]["review"]
    assert len(review) == 1
    entry = review[0]
    assert entry["reason_guess"] == "review"
    assert entry["reason_label"] == "Unsure, please confirm"


# ---------------------------------------------------------------------------
# Router-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_audit_endpoint_returns_job_id(client):
    tasks = [_make_task(id="t-600", title="Nothing to do", created_at=_old_ts())]
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit,
            "_classify_with_claude",
            AsyncMock(return_value='{"verdict": "review", "evidence": "unclear", "duplicate_of": null}'),
        ):
            resp = await client.post("/api/tasks/audit")

    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert isinstance(body["job_id"], str) and body["job_id"]


@pytest.mark.asyncio
async def test_audit_status_endpoint_404s_for_unknown_job(client):
    resp = await client.get("/api/tasks/audit/not-a-real-job")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_audit_status_endpoint_returns_state(client):
    tasks = [_make_task(id="t-700", title="Old task", created_at=_old_ts())]
    verdict_json = '{"verdict": "completed", "evidence": "done in api/services/x.py", "duplicate_of": null}'
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit, "_classify_with_claude", AsyncMock(return_value=verdict_json)
        ):
            start = await client.post("/api/tasks/audit")
            job_id = start.json()["job_id"]
            # Drive the background task to completion for a deterministic
            # snapshot.
            await task_audit.run_audit_job(job_id)
            resp = await client.get(f"/api/tasks/audit/{job_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in {"running", "done"}
    assert "results" in data
    assert isinstance(data["results"]["review"], list)


@pytest.mark.asyncio
async def test_approve_endpoint_closes_task_with_reason(client):
    """Approve must pass the reason through to ostk.close_task."""
    tasks = [_make_task(id="t-800", title="Old one", created_at=_old_ts())]
    verdict_json = (
        '{"verdict": "completed", "evidence": "api/services/y.py", "duplicate_of": null}'
    )
    close_mock = AsyncMock(return_value="closed t-800")
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit, "_classify_with_claude", AsyncMock(return_value=verdict_json)
        ):
            start = await client.post("/api/tasks/audit")
            job_id = start.json()["job_id"]
            await task_audit.run_audit_job(job_id)

            with patch("routers.task_audit.ostk") as mock_router_ostk:
                mock_router_ostk.close_task = close_mock
                resp = await client.post(
                    f"/api/tasks/audit/{job_id}/approve",
                    json={"task_id": "t-800", "reason": "completed"},
                )

    assert resp.status_code == 200
    assert resp.json()["closed"] == "t-800"
    close_mock.assert_awaited_once_with("t-800", closed_reason="completed")

    # The approved entry moves from review to closed on the next poll.
    state = task_audit.get_audit_status(job_id)
    assert state is not None
    assert all(e["task_id"] != "t-800" for e in state["results"]["review"])
    assert any(e["task_id"] == "t-800" for e in state["results"]["closed"])


@pytest.mark.asyncio
async def test_approve_endpoint_rejects_invalid_reason(client):
    tasks = [_make_task(id="t-810", title="Old one", created_at=_old_ts())]
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit,
            "_classify_with_claude",
            AsyncMock(return_value='{"verdict": "review", "evidence": "x", "duplicate_of": null}'),
        ):
            start = await client.post("/api/tasks/audit")
            job_id = start.json()["job_id"]
            await task_audit.run_audit_job(job_id)
            resp = await client.post(
                f"/api/tasks/audit/{job_id}/approve",
                json={"task_id": "t-810", "reason": "banana"},
            )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_reject_endpoint_removes_review_entry_without_touching_task(client):
    tasks = [_make_task(id="t-900", title="Another one", created_at=_old_ts())]
    verdict_json = (
        '{"verdict": "completed", "evidence": "file.py", "duplicate_of": null}'
    )
    close_mock = AsyncMock(return_value="closed t-900")
    with patch.object(task_audit.ostk, "list_tasks", AsyncMock(return_value=tasks)):
        with patch.object(
            task_audit, "_classify_with_claude", AsyncMock(return_value=verdict_json)
        ):
            start = await client.post("/api/tasks/audit")
            job_id = start.json()["job_id"]
            await task_audit.run_audit_job(job_id)

            with patch("routers.task_audit.ostk") as mock_router_ostk:
                mock_router_ostk.close_task = close_mock
                resp = await client.post(
                    f"/api/tasks/audit/{job_id}/reject",
                    json={"task_id": "t-900"},
                )

    assert resp.status_code == 200
    # Task must still be open in the store (close_task never called).
    close_mock.assert_not_awaited()
    # And removed from the review list.
    state = task_audit.get_audit_status(job_id)
    assert state is not None
    assert all(e["task_id"] != "t-900" for e in state["results"]["review"])
