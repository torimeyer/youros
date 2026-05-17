"""Tests for the SDD wizard endpoints."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


# --------------- wizard/suggest ---------------

def test_suggest_returns_shape():
    with patch("services.chat_providers._resolve_api_key", new_callable=AsyncMock, return_value=None):
        resp = client.post("/api/specs/wizard/suggest", json={
            "problem": "Users can't find their tasks after meetings"
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "criteria" in body
        assert "non_goals" in body
        assert "after_statement" in body
        assert isinstance(body["criteria"], list)
        assert isinstance(body["non_goals"], list)


def test_suggest_empty_problem_400():
    resp = client.post("/api/specs/wizard/suggest", json={"problem": ""})
    assert resp.status_code == 400


def test_suggest_with_ai():
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=(
        "AFTER: After this ships, PMs can see related tasks before each meeting "
        "instead of searching manually.\n\n"
        "CRITERIA:\n"
        "- Shows related tasks for upcoming meetings\n"
        "- Matches tasks by keyword and attendee\n"
        "- Nudges for follow-ups after meetings end\n\n"
        "NON_GOALS:\n"
        "- Does not auto-create tasks from meeting notes\n"
        "- Does not integrate with meeting transcripts"
    ))]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    with patch("services.chat_providers._resolve_api_key", new_callable=AsyncMock, return_value="sk-test"), \
         patch("anthropic.AsyncAnthropic", return_value=mock_client):
        resp = client.post("/api/specs/wizard/suggest", json={
            "problem": "Users forget which tasks relate to their upcoming meetings",
            "in_scope": ["Calendar page", "Task matching"],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["criteria"]) == 3
        assert len(body["non_goals"]) == 2
        assert "PMs can see related tasks" in body["after_statement"]


def test_suggest_ai_failure_returns_empty():
    with patch("services.chat_providers._resolve_api_key", new_callable=AsyncMock, side_effect=Exception("boom")):
        resp = client.post("/api/specs/wizard/suggest", json={
            "problem": "Something breaks"
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["criteria"] == []
        assert body["non_goals"] == []


def test_suggest_with_scope_hint():
    with patch("services.chat_providers._resolve_api_key", new_callable=AsyncMock, return_value=None):
        resp = client.post("/api/specs/wizard/suggest", json={
            "problem": "Need better search",
            "in_scope": ["Full-text search", "Fuzzy matching"],
        })
        assert resp.status_code == 200


# --------------- wizard/create ---------------

def test_create_minimal():
    with patch("services.ostk.ostk.doc_draft", new_callable=AsyncMock, return_value="docs/draft/test-wizard.md") as mock_draft, \
         patch("services.ostk.ostk.doc_promote", new_callable=AsyncMock, return_value="docs/spec/test-wizard.md"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_relative_to", return_value=True), \
         patch("pathlib.Path.read_text", return_value="---\ntitle: test\n---\n"), \
         patch("pathlib.Path.write_text"):
        resp = client.post("/api/specs/wizard/create", json={
            "title": "Test Wizard Spec",
            "problem": "Users need a better way to create specs",
            "criteria": ["Shows a multi-step wizard", "Generates AI suggestions"],
            "kind": "spec",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        mock_draft.assert_called_once_with("Test Wizard Spec")


def test_create_empty_title_400():
    resp = client.post("/api/specs/wizard/create", json={
        "title": "",
        "problem": "Something",
    })
    assert resp.status_code == 400


def test_create_empty_problem_400():
    # Empty problem only raises 400 when kind='spec' (needle doesn't need it)
    resp = client.post("/api/specs/wizard/create", json={
        "title": "Something",
        "problem": "",
        "kind": "spec",
    })
    assert resp.status_code == 400


def test_create_full_spec():
    written_content = []

    def capture_write(content):
        written_content.append(content)

    with patch("services.ostk.ostk.doc_draft", new_callable=AsyncMock, return_value="docs/draft/full-spec.md"), \
         patch("services.ostk.ostk.doc_promote", new_callable=AsyncMock, return_value="docs/spec/full-spec.md"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_relative_to", return_value=True), \
         patch("pathlib.Path.read_text", return_value="---\ntitle: Full Spec\n---\n"), \
         patch("pathlib.Path.write_text", side_effect=capture_write):
        resp = client.post("/api/specs/wizard/create", json={
            "title": "Full SDD Spec",
            "problem": "Specs are too thin for agents to build from",
            "in_scope": ["Multi-step wizard", "AI suggestions"],
            "out_of_scope": ["Template editing", "Spec versioning"],
            "non_goals": ["Replace existing simple spec flow"],
            "criteria": ["Wizard produces richer specs", "AI suggests criteria"],
            "technical_context": "Follow the pattern in specs.py create_draft",
            "api_contract": "POST /api/specs/wizard/create",
            "ui_requirements": "4-step wizard with back/next navigation",
            "kind": "spec",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"

        assert len(written_content) == 1
        content = written_content[0]
        assert "## Problem" in content
        assert "## Scope" in content
        assert "## Out of scope" in content
        assert "## Non-goals" in content
        assert "## Acceptance criteria" in content
        assert "## Technical context" in content
        assert "## API contract" in content
        assert "## UI requirements" in content
        assert "- [ ] Wizard produces richer specs" in content


def test_create_no_criteria_stays_draft():
    with patch("services.ostk.ostk.doc_draft", new_callable=AsyncMock, return_value="docs/draft/no-ac.md"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_relative_to", return_value=True), \
         patch("pathlib.Path.read_text", return_value="---\ntitle: No AC\n---\n"), \
         patch("pathlib.Path.write_text"):
        resp = client.post("/api/specs/wizard/create", json={
            "title": "No Criteria Spec",
            "problem": "Just a problem, no criteria yet",
            "kind": "spec",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "draft"


def test_create_promote_failure_stays_draft():
    from services.ostk import OstkError

    with patch("services.ostk.ostk.doc_draft", new_callable=AsyncMock, return_value="docs/draft/fail-promote.md"), \
         patch("services.ostk.ostk.doc_promote", new_callable=AsyncMock, side_effect=OstkError("promote failed")), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_relative_to", return_value=True), \
         patch("pathlib.Path.read_text", return_value="---\ntitle: Fail\n---\n"), \
         patch("pathlib.Path.write_text"):
        resp = client.post("/api/specs/wizard/create", json={
            "title": "Promote Fail Spec",
            "problem": "Testing promote failure",
            "criteria": ["This should work"],
            "kind": "spec",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "draft"


# --------------- kind=needle default path ---------------


def test_wizard_create_needle_default():
    """No kind supplied -> defaults to needle. No spec file is created."""
    with patch("services.ostk.ostk.add_task", new_callable=AsyncMock, return_value="→1999 Add login page") as mock_add:
        resp = client.post("/api/specs/wizard/create", json={
            "title": "Add login page",
            "problem": "Users cannot log in",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "needle"
        assert body["task_id"] == "1999"
        mock_add.assert_called_once()


def test_wizard_create_needle_explicit():
    """kind='needle' creates a task; problem is optional."""
    with patch("services.ostk.ostk.add_task", new_callable=AsyncMock, return_value="→2001 Fix dark mode"):
        resp = client.post("/api/specs/wizard/create", json={
            "title": "Fix dark mode",
            "kind": "needle",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "needle"


def test_draft_needle_default():
    """POST /specs/draft with no kind creates a needle, not a spec file."""
    with patch("services.ostk.ostk.add_task", new_callable=AsyncMock, return_value="→2002 My quick task"):
        resp = client.post("/api/specs/draft", json={"title": "My quick task"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "needle"
        assert body["task_id"] == "2002"


def test_draft_spec_opt_in():
    """POST /specs/draft with kind='spec' still follows the old spec pipeline."""
    with patch("services.ostk.ostk.doc_draft", new_callable=AsyncMock, return_value="docs/draft/opt-in.md"), \
         patch("services.chat_providers._resolve_api_key", new_callable=AsyncMock, return_value=None), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_relative_to", return_value=True), \
         patch("pathlib.Path.read_text", return_value="---\ntitle: opt-in\n---\n"), \
         patch("pathlib.Path.write_text"):
        resp = client.post("/api/specs/draft", json={"title": "Opt-in spec", "kind": "spec"})
        assert resp.status_code == 200
        body = resp.json()
        # No AI key -> stays draft, but it used the spec path (has 'status' key)
        assert "status" in body
