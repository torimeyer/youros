"""Tests for spec-kit YAML import and export.

Covers:
- parse_speckit: round-trip, missing fields, invalid YAML
- spec_to_speckit: export round-trip
- POST /api/specs/import: creates spec and tasks, 422 on invalid YAML
- GET /api/specs/{path}/export?format=speckit: exports YAML
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# --- Unit tests for services/speckit_import.py ---

from services.speckit_import import (
    parse_speckit,
    spec_to_speckit,
    speckit_to_spec,
    SpeckitParseError,
)

VALID_YAML = """\
name: "Feature name"
description: "What it does"
tasks:
  - title: "Task 1"
    description: "Do the thing"
    priority: P1
    acceptance_criteria:
      - "Criterion 1"
      - "Criterion 2"
  - title: "Task 2"
    priority: P2
"""


class TestParseSpeckit:
    def test_parse_valid(self):
        parsed = parse_speckit(VALID_YAML)
        assert parsed["name"] == "Feature name"
        assert parsed["description"] == "What it does"
        assert len(parsed["tasks"]) == 2

    def test_parse_task_fields(self):
        parsed = parse_speckit(VALID_YAML)
        t = parsed["tasks"][0]
        assert t["title"] == "Task 1"
        assert t["description"] == "Do the thing"
        assert t["priority"] == "P1"
        assert t["acceptance_criteria"] == ["Criterion 1", "Criterion 2"]

    def test_parse_task_without_description(self):
        parsed = parse_speckit(VALID_YAML)
        t = parsed["tasks"][1]
        assert t["title"] == "Task 2"
        assert t["description"] == ""
        assert t["acceptance_criteria"] == []

    def test_missing_name_raises(self):
        bad = "description: no name here\ntasks: []"
        with pytest.raises(SpeckitParseError, match="name"):
            parse_speckit(bad)

    def test_invalid_yaml_raises(self):
        with pytest.raises(SpeckitParseError, match="Invalid YAML"):
            parse_speckit(":\t bad\t yaml\n  - unclosed")

    def test_empty_input_raises(self):
        with pytest.raises(SpeckitParseError):
            parse_speckit("")

    def test_non_mapping_raises(self):
        with pytest.raises(SpeckitParseError, match="mapping"):
            parse_speckit("- item1\n- item2")

    def test_tasks_not_list_raises(self):
        with pytest.raises(SpeckitParseError, match="list"):
            parse_speckit("name: foo\ntasks: not-a-list")

    def test_task_missing_title_raises(self):
        yaml_str = "name: foo\ntasks:\n  - description: no title"
        with pytest.raises(SpeckitParseError, match="title"):
            parse_speckit(yaml_str)

    def test_default_priority(self):
        yaml_str = "name: foo\ntasks:\n  - title: bar"
        parsed = parse_speckit(yaml_str)
        assert parsed["tasks"][0]["priority"] == "P2"


class TestRoundTrip:
    def test_parse_then_export_preserves_name(self):
        parsed = parse_speckit(VALID_YAML)
        spec = speckit_to_spec(parsed)
        yaml_out = spec_to_speckit(spec)
        reparsed = parse_speckit(yaml_out)
        assert reparsed["name"] == parsed["name"]

    def test_parse_then_export_preserves_description(self):
        parsed = parse_speckit(VALID_YAML)
        spec = speckit_to_spec(parsed)
        yaml_out = spec_to_speckit(spec)
        reparsed = parse_speckit(yaml_out)
        assert reparsed["description"] == parsed["description"]

    def test_parse_then_export_preserves_task_titles(self):
        parsed = parse_speckit(VALID_YAML)
        spec = speckit_to_spec(parsed)
        yaml_out = spec_to_speckit(spec)
        reparsed = parse_speckit(yaml_out)
        titles_in = [t["title"] for t in parsed["tasks"]]
        titles_out = [t["title"] for t in reparsed["tasks"]]
        assert titles_out == titles_in

    def test_parse_then_export_preserves_acceptance_criteria(self):
        parsed = parse_speckit(VALID_YAML)
        spec = speckit_to_spec(parsed)
        yaml_out = spec_to_speckit(spec)
        reparsed = parse_speckit(yaml_out)
        ac_in = parsed["tasks"][0]["acceptance_criteria"]
        ac_out = reparsed["tasks"][0]["acceptance_criteria"]
        assert ac_out == ac_in

    def test_speckit_to_spec_shape(self):
        parsed = parse_speckit(VALID_YAML)
        spec = speckit_to_spec(parsed)
        assert "title" in spec
        assert "description" in spec
        assert "tasks" in spec
        assert spec["title"] == parsed["name"]

    def test_spec_to_speckit_output_is_valid_yaml(self):
        import yaml
        spec = {"title": "Test", "description": "desc", "tasks": [
            {"title": "T1", "description": "", "priority": "P1", "acceptance_criteria": ["AC1"]},
        ]}
        out = spec_to_speckit(spec)
        data = yaml.safe_load(out)
        assert data["name"] == "Test"

    def test_empty_tasks_roundtrip(self):
        yaml_str = "name: foo\ndescription: bar"
        parsed = parse_speckit(yaml_str)
        spec = speckit_to_spec(parsed)
        out = spec_to_speckit(spec)
        reparsed = parse_speckit(out)
        assert reparsed["name"] == "foo"
        assert reparsed["tasks"] == []


# --- HTTP endpoint tests ---

from main import app


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestImportEndpoint:
    @pytest.mark.asyncio
    async def test_invalid_yaml_returns_422(self, client):
        resp = await client.post("/api/specs/import", json={
            "yaml": ":\t bad",
            "format": "speckit",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_name_returns_422(self, client):
        resp = await client.post("/api/specs/import", json={
            "yaml": "description: no name",
            "format": "speckit",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unsupported_format_returns_400(self, client):
        resp = await client.post("/api/specs/import", json={
            "yaml": VALID_YAML,
            "format": "unknown",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_valid_import_creates_spec(self, client, tmp_path):
        docs_dir = tmp_path / "docs" / "draft"
        docs_dir.mkdir(parents=True, exist_ok=True)
        draft_file = docs_dir / "feature-name.md"
        draft_file.write_text(
            "---\ntitle: Feature name\nstatus: draft\n---\n\nFeature name\n"
        )
        draft_rel = "docs/draft/feature-name.md"
        promoted_rel = "docs/spec/feature-name.md"

        with (
            patch("routers.specs.ostk.doc_draft", new_callable=AsyncMock) as mock_draft,
            patch("routers.specs.ostk.doc_promote", new_callable=AsyncMock) as mock_promote,
            patch("routers.specs.ostk.add_task", new_callable=AsyncMock) as mock_add_task,
            patch("routers.specs.ostk.cwd", str(tmp_path)),
        ):
            mock_draft.return_value = draft_rel
            mock_promote.return_value = promoted_rel
            mock_add_task.return_value = "→123\nTask created"

            resp = await client.post("/api/specs/import", json={
                "yaml": VALID_YAML,
                "format": "speckit",
                "kind": "spec",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "path" in data or "id" in data
        assert data.get("title") == "Feature name"

    @pytest.mark.asyncio
    async def test_import_creates_tasks(self, client, tmp_path):
        docs_dir = tmp_path / "docs" / "draft"
        docs_dir.mkdir(parents=True, exist_ok=True)
        draft_file = docs_dir / "feature-name.md"
        draft_file.write_text("---\ntitle: Feature name\nstatus: draft\n---\n\n")
        draft_rel = "docs/draft/feature-name.md"

        add_task_calls = []

        async def fake_add_task(title, **kwargs):
            add_task_calls.append({"title": title, **kwargs})
            return "→99\nok"

        with (
            patch("routers.specs.ostk.doc_draft", new_callable=AsyncMock) as mock_draft,
            patch("routers.specs.ostk.doc_promote", new_callable=AsyncMock) as mock_promote,
            patch("routers.specs.ostk.add_task", side_effect=fake_add_task),
            patch("routers.specs.ostk.cwd", str(tmp_path)),
        ):
            mock_draft.return_value = draft_rel
            mock_promote.return_value = "docs/spec/feature-name.md"

            await client.post("/api/specs/import", json={
                "yaml": VALID_YAML,
                "format": "speckit",
                "kind": "spec",
            })
        assert len(add_task_calls) == 2
        titles = [c["title"] for c in add_task_calls]
        assert "Task 1" in titles
        assert "Task 2" in titles


class TestExportEndpoint:
    @pytest.mark.asyncio
    async def test_unsupported_format_returns_400(self, client, tmp_path):
        spec_dir = tmp_path / "docs" / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec_file = spec_dir / "foo.md"
        spec_file.write_text("---\ntitle: foo\nstatus: spec\n---\n\n## Acceptance criteria\n- [ ] do thing\n")
        with patch("routers.specs.PROJECT_ROOT", tmp_path):
            resp = await client.get(
                "/api/specs/docs/spec/foo.md/export?format=json"
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_spec_returns_404(self, client, tmp_path):
        spec_dir = tmp_path / "docs" / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        with patch("routers.specs.PROJECT_ROOT", tmp_path):
            resp = await client.get(
                "/api/specs/docs/spec/missing.md/export?format=speckit"
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_export_returns_yaml(self, client, tmp_path):
        spec_dir = tmp_path / "docs" / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec_file = spec_dir / "my-feature.md"
        spec_file.write_text(
            "---\ntitle: My feature\nstatus: spec\n---\n\n"
            "Great feature.\n\n"
            "## Acceptance criteria\n"
            "- [ ] users can do X\n"
            "- [ ] users can do Y\n"
        )
        with patch("routers.specs.PROJECT_ROOT", tmp_path):
            resp = await client.get(
                "/api/specs/docs/spec/my-feature.md/export?format=speckit"
            )
        assert resp.status_code == 200
        assert "yaml" in resp.headers.get("content-type", "")
        import yaml
        data = yaml.safe_load(resp.text)
        assert "name" in data
