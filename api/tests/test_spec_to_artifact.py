"""RED tests for spec-to-artifact chooser (→1531).

Tests the 'produces' field on wizard_create and build_spec routing.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call
from fastapi.testclient import TestClient

from config import PROJECT_ROOT
from main import app

client = TestClient(app)

_LEAKED_SPEC = Path(PROJECT_ROOT) / "docs" / "spec" / "code-spec.md"


@pytest.fixture(autouse=True)
def cleanup_code_spec():
    """Remove docs/spec/code-spec.md before and after every test in this module.

    _set_spec_status writes to the real filesystem when pathlib.Path.write_text is
    not patched.  This fixture ensures that even if a previous run leaked the file,
    it is gone before each test, and cleaned up afterwards (→1751).
    """
    _LEAKED_SPEC.unlink(missing_ok=True)
    yield
    _LEAKED_SPEC.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# wizard/create — produces field stored in frontmatter
# ---------------------------------------------------------------------------

def _make_create_request(produces: str) -> dict:
    return {
        "title": "Q3 marketing strategy",
        "problem": "Marketer needs a strategy doc grounded in brand library",
        "criteria": ["Quotes brand voice", "References ICP doc", "Has exec summary"],
        "kind": "spec",
        "produces": produces,
    }


def test_wizard_create_default_produces_code():
    """No produces field defaults to code; existing flow unchanged."""
    written = []
    with patch("services.ostk.ostk.doc_draft", new_callable=AsyncMock, return_value="docs/draft/d.md"), \
         patch("services.ostk.ostk.doc_promote", new_callable=AsyncMock, return_value="docs/spec/d.md"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_relative_to", return_value=True), \
         patch("pathlib.Path.read_text", return_value="---\nstatus: draft\n---\n"), \
         patch("pathlib.Path.write_text", side_effect=lambda c: written.append(c)):
        resp = client.post("/api/specs/wizard/create", json={
            "title": "Code spec",
            "problem": "Need code",
            "criteria": ["a", "b", "c"],
            "kind": "spec",
        })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"
    # default produces=code: no produces key in frontmatter (or produces: code)
    if written:
        assert "produces: agent" not in written[-1]
        assert "produces: document" not in written[-1]


def test_wizard_create_produces_document_stored():
    """produces=document is stored in spec frontmatter."""
    written = []
    with patch("services.ostk.ostk.doc_draft", new_callable=AsyncMock, return_value="docs/draft/d.md"), \
         patch("services.ostk.ostk.doc_promote", new_callable=AsyncMock, return_value="docs/spec/d.md"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_relative_to", return_value=True), \
         patch("pathlib.Path.read_text", return_value="---\nstatus: draft\ntitle: My Spec\n---\n"), \
         patch("pathlib.Path.write_text", side_effect=lambda c: written.append(c)):
        resp = client.post("/api/specs/wizard/create", json=_make_create_request("document"))
    assert resp.status_code == 200
    assert len(written) >= 1
    assert "produces: document" in written[-1]


def test_wizard_create_produces_agent_stored():
    """produces=agent is stored in spec frontmatter."""
    written = []
    with patch("services.ostk.ostk.doc_draft", new_callable=AsyncMock, return_value="docs/draft/d.md"), \
         patch("services.ostk.ostk.doc_promote", new_callable=AsyncMock, return_value="docs/spec/d.md"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_relative_to", return_value=True), \
         patch("pathlib.Path.read_text", return_value="---\nstatus: draft\ntitle: My Spec\n---\n"), \
         patch("pathlib.Path.write_text", side_effect=lambda c: written.append(c)):
        resp = client.post("/api/specs/wizard/create", json=_make_create_request("agent"))
    assert resp.status_code == 200
    assert len(written) >= 1
    assert "produces: agent" in written[-1]


@pytest.mark.parametrize("produces", ["slides", "diagram", "skill"])
def test_wizard_create_produces_variants_stored(produces):
    """produces=slides/diagram/skill each stored in frontmatter."""
    written = []
    with patch("services.ostk.ostk.doc_draft", new_callable=AsyncMock, return_value="docs/draft/d.md"), \
         patch("services.ostk.ostk.doc_promote", new_callable=AsyncMock, return_value="docs/spec/d.md"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_relative_to", return_value=True), \
         patch("pathlib.Path.read_text", return_value="---\nstatus: draft\ntitle: My Spec\n---\n"), \
         patch("pathlib.Path.write_text", side_effect=lambda c: written.append(c)):
        resp = client.post("/api/specs/wizard/create", json=_make_create_request(produces))
    assert resp.status_code == 200
    assert len(written) >= 1
    assert f"produces: {produces}" in written[-1]


# ---------------------------------------------------------------------------
# build_spec — produces routes to right template agent
# ---------------------------------------------------------------------------

SPEC_BODY = {
    "document": "---\nstatus: ready\ntitle: Q3 strategy\nproduces: document\n---\n## Acceptance criteria\n- [ ] Has exec summary\n- [ ] Quotes brand voice\n",
    "agent": "---\nstatus: ready\ntitle: Newsletter agent\nproduces: agent\n---\n## Acceptance criteria\n- [ ] Reads blog library\n- [ ] Produces weekly digest\n",
    "slides": "---\nstatus: ready\ntitle: Pitch deck\nproduces: slides\n---\n## Acceptance criteria\n- [ ] 10 slides\n",
    "diagram": "---\nstatus: ready\ntitle: Arch diagram\nproduces: diagram\n---\n## Acceptance criteria\n- [ ] Shows data flow\n",
    "skill": "---\nstatus: ready\ntitle: Brand voice skill\nproduces: skill\n---\n## Acceptance criteria\n- [ ] Enforces brand tone\n",
    "code": "---\nstatus: ready\ntitle: Code feature\n---\n## Acceptance criteria\n- [ ] Handles edge case\n",
}

PRODUCES_TO_TEMPLATE = {
    "document": "document-drafter",
    "agent": "builder-of-agents",
    "slides": "slides-drafter",
    "diagram": "diagram-drafter",
    "skill": "skill-builder",
}


@pytest.mark.parametrize("produces,expected_template", list(PRODUCES_TO_TEMPLATE.items()))
def test_build_spec_routes_to_template(produces, expected_template, tmp_path):
    """build_spec with produces=X spawns one agent using the right template; skips task decomposition."""
    spec_file = tmp_path / "my-spec.md"
    spec_file.write_text(SPEC_BODY[produces])

    spawned_bodies = []

    async def mock_spawn(body):
        spawned_bodies.append(body)

    with patch("routers.specs._validate_doc_path"), \
         patch("routers.specs.Path") as mock_path_cls, \
         patch("routers.specs._resolve_task_configs", new_callable=AsyncMock) as mock_resolve, \
         patch("routers.agents.spawn_agent", new_callable=AsyncMock, side_effect=mock_spawn):

        # Make Path(PROJECT_ROOT) / spec_path return our temp file
        mock_path_instance = MagicMock()
        mock_path_instance.__truediv__ = MagicMock(return_value=spec_file)
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance
        spec_file_mock = MagicMock()
        spec_file_mock.exists.return_value = True
        spec_file_mock.read_text.return_value = SPEC_BODY[produces]
        mock_path_instance.__truediv__.return_value = spec_file_mock
        mock_path_instance.__truediv__.return_value.__truediv__ = MagicMock(return_value=spec_file_mock)

        resp = client.post("/api/specs/docs/spec/my-spec.md/build")

    assert resp.status_code == 200
    # Non-code produce: exactly ONE artifact agent spawned, not the decompose path
    assert len(spawned_bodies) == 1
    assert spawned_bodies[0].template == expected_template
    mock_resolve.assert_not_called()


def test_build_spec_code_uses_existing_flow():
    """build_spec with no produces (code default) uses the existing decompose flow."""
    task_configs = [{"name": "spec-code-spec-1", "task_id": "1", "task_title": "Handles edge case",
                     "prompt": "Build task 1"}]
    spawned_bodies = []

    async def mock_spawn(body):
        spawned_bodies.append(body)

    with patch("routers.specs._validate_doc_path"), \
         patch("routers.specs._resolve_task_configs", new_callable=AsyncMock, return_value=task_configs) as mock_resolve, \
         patch("routers.agents.spawn_agent", new_callable=AsyncMock, side_effect=mock_spawn), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=SPEC_BODY["code"]), \
         patch("pathlib.Path.write_text"):
        resp = client.post("/api/specs/docs/spec/code-spec.md/build")

    # Code path must use _resolve_task_configs
    mock_resolve.assert_called_once()
