"""Tests for the smart email triage service and endpoints.

All external calls (Gmail API, Anthropic, ostk, calendar) are mocked so these
tests run without real credentials or network access.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

HUMAN_MESSAGE = {
    "id": "msg_human",
    "thread_id": "thread1",
    "subject": "Can you review the proposal?",
    "from_name": "Alice Smith",
    "from_email": "alice@example.com",
    "snippet": "Hey, can you take a look at the proposal I sent? Let me know.",
    "date": "2026-05-01T10:00:00",
    "is_unread": True,
}

SHIPPING_MESSAGE = {
    "id": "msg_shipping",
    "thread_id": "thread2",
    "subject": "Your Amazon order has shipped",
    "from_name": "Amazon",
    "from_email": "shipment-tracking@amazon.com",
    "snippet": "Your order #123 has shipped and will arrive Friday.",
    "date": "2026-05-01T09:00:00",
    "is_unread": True,
}

FYI_MESSAGE = {
    "id": "msg_fyi",
    "thread_id": "thread3",
    "subject": "Q1 report available",
    "from_name": "Finance Team",
    "from_email": "finance@company.com",
    "snippet": "The Q1 report is now available for review in the shared drive.",
    "date": "2026-05-01T08:00:00",
    "is_unread": True,
}

TASK_MESSAGE = {
    "id": "msg_task",
    "thread_id": "thread4",
    "subject": "Please update the CI pipeline",
    "from_name": "Bob Dev",
    "from_email": "bob@example.com",
    "snippet": "Can you update the CI pipeline to use Node 20?",
    "date": "2026-05-01T07:00:00",
    "is_unread": True,
}

SAMPLE_MESSAGES = [HUMAN_MESSAGE, SHIPPING_MESSAGE, FYI_MESSAGE]

CLAUDE_CLASSIFICATIONS = [
    {"index": 0, "category": "action_needed", "reason": "Human asking for review"},
    {"index": 1, "category": "noise", "reason": "Automated shipping notification"},
    {"index": 2, "category": "informational", "reason": "FYI report, no reply needed"},
]

SAMPLE_TASKS = [{"id": "1", "title": "Review project proposal", "priority": "P1"}]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_authenticated():
    with patch("routers.gmail_triage.is_authenticated", return_value=True), \
         patch("services.google_auth.is_authenticated", return_value=True):
        yield


@pytest.fixture
def mock_unauthenticated():
    with patch("services.google_auth.is_authenticated", return_value=False):
        yield


@pytest.fixture
def mock_gmail_messages():
    with patch(
        "services.gmail.get_unread_summary",
        new_callable=AsyncMock,
        return_value=SAMPLE_MESSAGES,
    ):
        yield


@pytest.fixture
def mock_ostk_tasks():
    with patch(
        "services.gmail_triage._get_ostk_tasks",
        new_callable=AsyncMock,
        return_value=SAMPLE_TASKS,
    ):
        yield


@pytest.fixture
def mock_calendar_empty():
    with patch(
        "services.gmail_triage._get_calendar_events",
        new_callable=AsyncMock,
        return_value=[],
    ):
        yield


@pytest.fixture
def mock_claude_ok():
    with patch(
        "services.gmail_triage._call_claude_batch",
        new_callable=AsyncMock,
        return_value=CLAUDE_CLASSIFICATIONS,
    ) as m:
        yield m


@pytest.fixture
def client():
    from routers.gmail_triage import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Service: triage_inbox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triage_classifies_all_messages(
    mock_authenticated, mock_gmail_messages, mock_ostk_tasks, mock_calendar_empty, mock_claude_ok
):
    from services.gmail_triage import triage_inbox

    result = await triage_inbox()

    assert result["ok"] is True
    assert len(result["messages"]) == 3
    by_id = {m["id"]: m for m in result["messages"]}
    assert by_id["msg_human"]["category"] == "action_needed"
    assert by_id["msg_shipping"]["category"] == "noise"
    assert by_id["msg_fyi"]["category"] == "informational"


@pytest.mark.asyncio
async def test_triage_prefilter_downgrades_noreply(
    mock_authenticated, mock_ostk_tasks, mock_calendar_empty
):
    """is_actionable_for_reply should downgrade action_needed to informational
    for no-reply senders, overriding whatever Claude classified."""
    noreply_msg = {
        "id": "noreply1",
        "thread_id": "t1",
        "subject": "Your account update",
        "from_name": "noreply",
        "from_email": "noreply@company.com",
        "snippet": "Your account has been updated",
        "date": "2026-05-01T10:00:00",
        "is_unread": True,
    }
    claude_response = [
        {"index": 0, "category": "action_needed", "reason": "Looks important"}
    ]

    with patch(
        "services.gmail.get_unread_summary",
        new_callable=AsyncMock,
        return_value=[noreply_msg],
    ):
        with patch(
            "services.gmail_triage._call_claude_batch",
            new_callable=AsyncMock,
            return_value=claude_response,
        ):
            from services.gmail_triage import triage_inbox

            result = await triage_inbox()

    assert result["ok"] is True
    assert result["messages"][0]["category"] == "informational"


@pytest.mark.asyncio
async def test_triage_task_worthy_includes_task_fields(
    mock_authenticated, mock_ostk_tasks, mock_calendar_empty
):
    """task_worthy messages must carry task_title and task_description."""
    claude_response = [
        {
            "index": 0,
            "category": "task_worthy",
            "reason": "Contains a clear action item",
            "task_title": "Update CI pipeline to Node 20",
            "task_description": "Update CI pipeline configuration to use Node.js 20",
        }
    ]

    with patch(
        "services.gmail.get_unread_summary",
        new_callable=AsyncMock,
        return_value=[TASK_MESSAGE],
    ):
        with patch(
            "services.gmail_triage._call_claude_batch",
            new_callable=AsyncMock,
            return_value=claude_response,
        ):
            from services.gmail_triage import triage_inbox

            result = await triage_inbox()

    assert result["ok"] is True
    msg = result["messages"][0]
    assert msg["category"] == "task_worthy"
    assert msg["task_title"] == "Update CI pipeline to Node 20"
    assert "task_description" in msg


@pytest.mark.asyncio
async def test_triage_empty_inbox(mock_authenticated, mock_ostk_tasks, mock_calendar_empty):
    """Empty inbox returns ok with empty messages and zero counts."""
    with patch(
        "services.gmail.get_unread_summary",
        new_callable=AsyncMock,
        return_value=[],
    ):
        from services.gmail_triage import triage_inbox

        result = await triage_inbox()

    assert result["ok"] is True
    assert result["messages"] == []
    assert all(v == 0 for v in result["summary"].values())


@pytest.mark.asyncio
async def test_triage_unauthenticated():
    """Should return ok=False when Gmail is not connected."""
    with patch("services.google_auth.is_authenticated", return_value=False):
        from services.gmail_triage import triage_inbox

        result = await triage_inbox()

    assert result["ok"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_triage_result_shape(
    mock_authenticated, mock_gmail_messages, mock_ostk_tasks, mock_calendar_empty, mock_claude_ok
):
    """Result must contain ok, messages, summary (all 4 categories), triaged_at."""
    from services.gmail_triage import triage_inbox

    result = await triage_inbox()

    assert "ok" in result
    assert "messages" in result
    assert "summary" in result
    assert "triaged_at" in result

    for category in ("action_needed", "task_worthy", "informational", "noise"):
        assert category in result["summary"]


@pytest.mark.asyncio
async def test_triage_summary_counts_match_messages(
    mock_authenticated, mock_gmail_messages, mock_ostk_tasks, mock_calendar_empty, mock_claude_ok
):
    """Summary counts must add up to total message count."""
    from services.gmail_triage import triage_inbox

    result = await triage_inbox()
    total = sum(result["summary"].values())
    assert total == len(result["messages"])
    assert result["summary"]["action_needed"] == 1
    assert result["summary"]["noise"] == 1
    assert result["summary"]["informational"] == 1


@pytest.mark.asyncio
async def test_triage_claude_failure_graceful(
    mock_authenticated, mock_ostk_tasks, mock_calendar_empty
):
    """When Claude fails, messages still return with default categories."""
    with patch(
        "services.gmail.get_unread_summary",
        new_callable=AsyncMock,
        return_value=SAMPLE_MESSAGES,
    ):
        with patch(
            "services.gmail_triage._call_claude_batch",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from services.gmail_triage import triage_inbox

            result = await triage_inbox()

    assert result["ok"] is True
    assert len(result["messages"]) == 3
    for msg in result["messages"]:
        assert msg["category"] in ("action_needed", "task_worthy", "informational", "noise")


# ---------------------------------------------------------------------------
# Service: create_task_from_email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_from_email_ok():
    """create_task_from_email should call ostk and return task_id."""
    classified_msg = {
        **TASK_MESSAGE,
        "category": "task_worthy",
        "task_title": "Update CI pipeline to Node 20",
        "task_description": "Update CI config to Node.js 20",
    }

    mock_ostk_instance = MagicMock()
    mock_ostk_instance.add_task = AsyncMock(return_value="→1234")

    with patch("services.ostk.ostk", mock_ostk_instance):
        with patch("services.task_labeling.extract_task_id", return_value="1234"):
            with patch("services.task_labeling.schedule_auto_labels"):
                from services.gmail_triage import create_task_from_email

                result = await create_task_from_email(classified_msg)

    assert result["ok"] is True
    assert "task_id" in result
    assert result["title"] == "Update CI pipeline to Node 20"


# ---------------------------------------------------------------------------
# Router tests
# ---------------------------------------------------------------------------


def test_triage_endpoint_unauthenticated(client):
    with patch("routers.gmail_triage.is_authenticated", return_value=False):
        res = client.post("/api/gmail/triage")
    assert res.status_code == 401


def test_triage_last_no_cache(client):
    with patch("routers.gmail_triage.is_authenticated", return_value=True):
        with patch("services.gmail_triage._load_last_triage", return_value=None):
            res = client.get("/api/gmail/triage/last")
    assert res.status_code == 200
    data = res.json()
    assert data["messages"] == []
    assert data["triaged_at"] is None


def test_triage_last_with_cache(client):
    cached = {
        "ok": True,
        "messages": [{"id": "msg1", "category": "noise"}],
        "summary": {"noise": 1, "action_needed": 0, "task_worthy": 0, "informational": 0},
        "triaged_at": "2026-05-01T10:00:00+00:00",
    }
    with patch("routers.gmail_triage.is_authenticated", return_value=True):
        with patch("services.gmail_triage._load_last_triage", return_value=cached):
            res = client.get("/api/gmail/triage/last")
    assert res.status_code == 200
    assert res.json()["messages"][0]["category"] == "noise"


def test_triage_apply_skip(client):
    with patch("routers.gmail_triage.is_authenticated", return_value=True):
        res = client.post(
            "/api/gmail/triage/apply",
            json={"action": "skip", "message_id": "msg1"},
        )
    assert res.status_code == 200
    assert res.json()["skipped"] is True


def test_triage_apply_unknown_action(client):
    with patch("routers.gmail_triage.is_authenticated", return_value=True):
        res = client.post(
            "/api/gmail/triage/apply",
            json={"action": "teleport", "message_id": "msg1"},
        )
    assert res.status_code == 400


def test_triage_apply_unauthenticated(client):
    with patch("routers.gmail_triage.is_authenticated", return_value=False):
        res = client.post(
            "/api/gmail/triage/apply",
            json={"action": "skip", "message_id": "msg1"},
        )
    assert res.status_code == 401


def test_triage_apply_draft_reply_missing_thread_id(client):
    with patch("routers.gmail_triage.is_authenticated", return_value=True):
        res = client.post(
            "/api/gmail/triage/apply",
            json={"action": "draft_reply", "message_id": "msg1"},
        )
    assert res.status_code == 400


def test_triage_apply_create_task_no_cache(client):
    with patch("routers.gmail_triage.is_authenticated", return_value=True):
        with patch("services.gmail_triage._load_last_triage", return_value=None):
            res = client.post(
                "/api/gmail/triage/apply",
                json={"action": "create_task", "message_id": "msg1"},
            )
    assert res.status_code == 404
