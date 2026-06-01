"""Tests for →1938 (Slides scope) and →1939 (Docs update-text endpoint)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_presentations_scope_in_scopes():
    """presentations.readonly must be in SCOPES so the Slides API can be called."""
    from services.google_auth import SCOPES

    assert "https://www.googleapis.com/auth/presentations.readonly" in SCOPES


# ---------------------------------------------------------------------------
# POST /drive/docs/{doc_id}/update-text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_doc_text_happy_path(client):
    """Happy path: batchUpdate is called and endpoint returns ok."""
    mock_svc = MagicMock()
    mock_svc.documents.return_value.get.return_value.execute.return_value = {
        "body": {"content": [{"endIndex": 50}]},
    }
    mock_svc.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "documentId": "doc123",
    }

    with patch("routers.drive._build_docs_service", return_value=mock_svc):
        resp = await client.post(
            "/api/drive/docs/doc123/update-text",
            json={"text": "Hello world"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["doc_id"] == "doc123"

    # Verify batchUpdate was called
    mock_svc.documents.return_value.batchUpdate.assert_called_once()
    call_kwargs = mock_svc.documents.return_value.batchUpdate.call_args[1]
    assert call_kwargs["documentId"] == "doc123"
    requests_sent = call_kwargs["body"]["requests"]
    # Should have a deleteContentRange and an insertText
    types = {list(r.keys())[0] for r in requests_sent}
    assert "insertText" in types


@pytest.mark.asyncio
async def test_update_doc_text_api_error_returns_500(client):
    """When Docs API throws, endpoint returns 500 with detail."""
    mock_svc = MagicMock()
    mock_svc.documents.return_value.get.return_value.execute.side_effect = Exception(
        "token expired"
    )

    with patch("routers.drive._build_docs_service", return_value=mock_svc):
        resp = await client.post(
            "/api/drive/docs/doc456/update-text",
            json={"text": "Some text"},
        )

    assert resp.status_code == 500
    assert "token expired" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_doc_text_missing_text_field(client):
    """Body without text field is a validation error (422)."""
    mock_svc = MagicMock()
    with patch("routers.drive._build_docs_service", return_value=mock_svc):
        resp = await client.post(
            "/api/drive/docs/doc789/update-text",
            json={},
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /drive/docs/{doc_id}/export-text
#
# The Edit-in-app flow (→1939) seeds the editor textarea from this endpoint.
# Without it the textarea opens empty and a Save would wipe the doc, so the
# endpoint must return the doc's current plain text.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_doc_text_happy_path(client):
    """Returns the doc's current plain text so the editor can be pre-filled."""

    async def fake_export(file_id: str) -> str:
        assert file_id == "doc123"
        return "Hello from the doc"

    with patch("routers.drive._export_doc_text", side_effect=fake_export):
        resp = await client.get("/api/drive/docs/doc123/export-text")

    assert resp.status_code == 200
    assert resp.text == "Hello from the doc"
    assert resp.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_export_doc_text_api_error_returns_500(client):
    """When the export fails, the endpoint surfaces a 500 with detail."""

    async def boom(file_id: str) -> str:
        raise Exception("token expired")

    with patch("routers.drive._export_doc_text", side_effect=boom):
        resp = await client.get("/api/drive/docs/doc456/export-text")

    assert resp.status_code == 500
    assert "token expired" in resp.json()["detail"]
