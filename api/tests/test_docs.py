import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.ostk import OstkService, OstkError


# --- Service-level tests ---


class TestDocService:
    """Test the doc methods on OstkService."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.svc = OstkService(cwd=self.tmpdir)

    @pytest.mark.asyncio
    async def test_doc_draft_calls_cli(self):
        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "docs/draft/my-plan.md"
            result = await self.svc.doc_draft("my plan")

        mock_run.assert_called_once_with("doc", "draft", "my plan")
        assert result == "docs/draft/my-plan.md"

    @pytest.mark.asyncio
    async def test_doc_promote_calls_cli(self):
        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "docs/spec/my-plan.md"
            result = await self.svc.doc_promote("docs/draft/my-plan.md")

        mock_run.assert_called_once_with("doc", "promote", "docs/draft/my-plan.md")
        assert result == "docs/spec/my-plan.md"

    @pytest.mark.asyncio
    async def test_doc_decompose_calls_cli(self):
        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "->001 task A\n->002 task B"
            result = await self.svc.doc_decompose("docs/spec/my-plan.md")

        mock_run.assert_called_once_with(
            "doc", "decompose", "docs/spec/my-plan.md", "--auto"
        )
        assert "->001" in result

    @pytest.mark.asyncio
    async def test_list_docs_empty(self):
        """No docs directories means empty list."""
        result = await self.svc.list_docs()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_docs_finds_drafts_and_specs(self):
        docs_dir = Path(self.tmpdir) / "docs"
        draft_dir = docs_dir / "draft"
        spec_dir = docs_dir / "spec"
        draft_dir.mkdir(parents=True)
        spec_dir.mkdir(parents=True)

        draft_dir.joinpath("my-plan.md").write_text(
            "---\ntitle: my plan\nstatus: draft\ncreated_at: 2026-04-01T00:00:00Z\n---\n\nSome body text."
        )
        spec_dir.joinpath("other-spec.md").write_text(
            "---\ntitle: other spec\nstatus: spec\ncreated_at: 2026-04-02T00:00:00Z\npromoted_at: 2026-04-03T00:00:00Z\n---\n\n- [ ] criterion"
        )

        result = await self.svc.list_docs()
        assert len(result) == 2

        draft = next(d for d in result if d["status"] == "draft")
        assert draft["title"] == "my plan"
        assert draft["path"] == "docs/draft/my-plan.md"
        assert draft["created_at"] == "2026-04-01T00:00:00Z"
        assert "Some body text" in draft["body"]

        spec = next(d for d in result if d["status"] == "spec")
        assert spec["title"] == "other spec"
        assert spec["promoted_at"] == "2026-04-03T00:00:00Z"

    def test_parse_frontmatter_no_yaml(self):
        """File without front matter gets body as full text."""
        tmpfile = Path(self.tmpdir) / "plain.md"
        tmpfile.write_text("Just some text.\nMore lines.")

        doc = self.svc._parse_doc_frontmatter(tmpfile, "draft")
        assert doc["title"] == "plain"
        assert doc["status"] == "draft"
        assert doc["body"] == "Just some text.\nMore lines."

    def test_parse_frontmatter_with_yaml(self):
        tmpfile = Path(self.tmpdir) / "with-meta.md"
        tmpfile.write_text(
            "---\ntitle: My Document\nstatus: spec\ncreated_at: 2026-01-01\n---\n\nBody here."
        )

        doc = self.svc._parse_doc_frontmatter(tmpfile, "draft")
        assert doc["title"] == "My Document"
        assert doc["status"] == "spec"
        assert doc["body"] == "Body here."


# --- API endpoint tests ---


@pytest.mark.asyncio
async def test_list_docs_endpoint(client):
    mock_docs = [
        {"path": "docs/draft/plan.md", "title": "plan", "status": "draft",
         "filename": "plan.md", "created_at": "", "promoted_at": "", "body": ""},
    ]
    with patch("routers.docs.ostk") as mock_ostk:
        mock_ostk.list_docs = AsyncMock(return_value=mock_docs)
        resp = await client.get("/api/docs")

    assert resp.status_code == 200
    data = resp.json()
    assert "docs" in data
    assert len(data["docs"]) == 1
    assert data["docs"][0]["title"] == "plan"


@pytest.mark.asyncio
async def test_create_draft_endpoint(client):
    with patch("routers.docs.ostk") as mock_ostk:
        mock_ostk.doc_draft = AsyncMock(return_value="docs/draft/new-plan.md")
        resp = await client.post("/api/docs/draft", json={"title": "new plan"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "docs/draft/new-plan.md"
    mock_ostk.doc_draft.assert_called_once_with("new plan")


@pytest.mark.asyncio
async def test_create_draft_error(client):
    with patch("routers.docs.ostk") as mock_ostk:
        mock_ostk.doc_draft = AsyncMock(side_effect=OstkError("title is empty"))
        resp = await client.post("/api/docs/draft", json={"title": ""})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_promote_endpoint(client):
    with patch("routers.docs.ostk") as mock_ostk:
        mock_ostk.doc_promote = AsyncMock(return_value="docs/spec/plan.md")
        resp = await client.post("/api/docs/promote", json={"path": "docs/draft/plan.md"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "docs/spec/plan.md"
    mock_ostk.doc_promote.assert_called_once_with("docs/draft/plan.md")


@pytest.mark.asyncio
async def test_promote_error_no_criteria(client):
    with patch("routers.docs.ostk") as mock_ostk:
        mock_ostk.doc_promote = AsyncMock(
            side_effect=OstkError("Draft must contain at least one unchecked checkbox")
        )
        resp = await client.post("/api/docs/promote", json={"path": "docs/draft/plan.md"})

    assert resp.status_code == 400
    assert "checkbox" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_decompose_endpoint(client):
    with patch("routers.docs.ostk") as mock_ostk:
        mock_ostk.doc_decompose = AsyncMock(return_value="->001 task A\n->002 task B")
        resp = await client.post("/api/docs/decompose", json={"path": "docs/spec/plan.md"})

    assert resp.status_code == 200
    assert "->001" in resp.json()["result"]
    mock_ostk.doc_decompose.assert_called_once_with("docs/spec/plan.md")


@pytest.mark.asyncio
async def test_decompose_error(client):
    with patch("routers.docs.ostk") as mock_ostk:
        mock_ostk.doc_decompose = AsyncMock(side_effect=OstkError("spec not found"))
        resp = await client.post("/api/docs/decompose", json={"path": "docs/spec/nope.md"})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_docs_error(client):
    with patch("routers.docs.ostk") as mock_ostk:
        mock_ostk.list_docs = AsyncMock(side_effect=OstkError("disk error"))
        resp = await client.get("/api/docs")

    assert resp.status_code == 500
