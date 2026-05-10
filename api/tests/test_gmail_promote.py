"""Tests for create_task_from_email — Gmail → needle promotion (wave 1c).

Verifies that promoting an email to a task:
  1. Calls task_source_store.set_source with source="email" and source_ref=message_id
  2. Includes a direct Gmail permalink in the task description
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


TASK_MSG = {
    "id": "msg_abc123",
    "thread_id": "thread_xyz",
    "subject": "Please update the CI pipeline",
    "from_name": "Bob Dev",
    "from_email": "bob@example.com",
    "snippet": "Can you update the CI pipeline to use Node 20?",
    "date": "2026-05-01T07:00:00",
    "is_unread": True,
    "category": "task_worthy",
    "task_title": "Update CI pipeline to Node 20",
    "task_description": "Update CI config to use Node.js 20",
}

GMAIL_LINK = "https://mail.google.com/mail/u/0/#inbox/msg_abc123"


# ---------------------------------------------------------------------------
# create_task_from_email: source provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_sets_email_source():
    """task_source_store.set_source must be called with source='email' and message_id."""
    mock_ostk = MagicMock()
    mock_ostk.add_task = AsyncMock(return_value="→1234")
    mock_source_store = MagicMock()

    with patch("services.ostk.ostk", mock_ostk), \
         patch("services.task_labeling.extract_task_id", return_value="1234"), \
         patch("services.task_labeling.schedule_auto_labels"), \
         patch("services.gmail_triage.task_source_store", mock_source_store):

        from services.gmail_triage import create_task_from_email
        result = await create_task_from_email(TASK_MSG)

    assert result["ok"] is True
    mock_source_store.set_source.assert_called_once_with("1234", "email", "msg_abc123")


@pytest.mark.asyncio
async def test_create_task_description_contains_gmail_link():
    """Description passed to ostk.add_task must contain the Gmail permalink."""
    mock_ostk = MagicMock()
    mock_ostk.add_task = AsyncMock(return_value="→1234")

    with patch("services.ostk.ostk", mock_ostk), \
         patch("services.task_labeling.extract_task_id", return_value="1234"), \
         patch("services.task_labeling.schedule_auto_labels"), \
         patch("services.gmail_triage.task_source_store"):

        from services.gmail_triage import create_task_from_email
        await create_task_from_email(TASK_MSG)

    _, kwargs = mock_ostk.add_task.call_args
    description = kwargs.get("description", "")
    assert GMAIL_LINK in description, f"Expected Gmail link in description, got: {description!r}"


@pytest.mark.asyncio
async def test_create_task_source_ref_is_message_id_not_thread_id():
    """source_ref should be the message id, not the thread_id."""
    mock_ostk = MagicMock()
    mock_ostk.add_task = AsyncMock(return_value="→1234")
    mock_source_store = MagicMock()

    with patch("services.ostk.ostk", mock_ostk), \
         patch("services.task_labeling.extract_task_id", return_value="1234"), \
         patch("services.task_labeling.schedule_auto_labels"), \
         patch("services.gmail_triage.task_source_store", mock_source_store):

        from services.gmail_triage import create_task_from_email
        await create_task_from_email(TASK_MSG)

    set_source_call = mock_source_store.set_source.call_args
    source_ref = set_source_call[0][2]
    assert source_ref == "msg_abc123"
    assert source_ref != "thread_xyz"


@pytest.mark.asyncio
async def test_create_task_no_source_set_on_ostk_error():
    """If ostk.add_task raises, set_source must NOT be called."""
    from services.ostk import OstkError

    mock_ostk = MagicMock()
    mock_ostk.add_task = AsyncMock(side_effect=OstkError("kernel offline"))
    mock_source_store = MagicMock()

    with patch("services.ostk.ostk", mock_ostk), \
         patch("services.gmail_triage.task_source_store", mock_source_store):

        from services.gmail_triage import create_task_from_email
        result = await create_task_from_email(TASK_MSG)

    assert result["ok"] is False
    mock_source_store.set_source.assert_not_called()
