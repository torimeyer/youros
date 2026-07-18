"""Tests: doc_promote must NOT call doc_decompose (→2938).

Spec progress is derived from file checkboxes; the task ledger is not
populated at promote time.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.ostk import OstkService


@pytest.fixture
def svc(tmp_path):
    import services.ostk as _mod
    with patch.object(_mod, "USER_SPECS_DIR", tmp_path / "myos" / "specs"):
        yield OstkService(cwd=str(tmp_path))


def _make_draft(tmp_path: Path) -> Path:
    draft_dir = tmp_path / "docs" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    f = draft_dir / "my-spec.md"
    f.write_text(
        "---\ntitle: my spec\nstatus: draft\n---\n\n- [ ] criterion A\n"
    )
    return f


@pytest.mark.asyncio
async def test_doc_promote_does_not_call_decompose(svc, tmp_path):
    """doc_promote must NOT call doc_decompose (→2938).

    Progress is derived from the file's own checkboxes; minting ledger rows
    at promote time created unwanted AC rows (exhibit A: →2939–2943).
    """
    import services.ostk as _mod
    specs_dir = tmp_path / "myos" / "specs"

    _make_draft(tmp_path)

    with patch.object(svc, "doc_decompose", new_callable=AsyncMock) as mock_decompose:
        mock_decompose.return_value = {"result": "", "task_ids": []}
        with patch.object(_mod, "USER_SPECS_DIR", specs_dir):
            result = await svc.doc_promote("docs/draft/my-spec.md")

    promoted_path = str(specs_dir / "my-spec.md")
    mock_decompose.assert_not_called()
    assert result == promoted_path


@pytest.mark.asyncio
async def test_doc_promote_succeeds_and_moves_file(svc, tmp_path):
    """doc_promote must move the draft file and return the new path."""
    import services.ostk as _mod
    specs_dir = tmp_path / "myos" / "specs"

    draft = _make_draft(tmp_path)

    with patch.object(_mod, "USER_SPECS_DIR", specs_dir):
        result = await svc.doc_promote("docs/draft/my-spec.md")

    promoted_path = str(specs_dir / "my-spec.md")
    assert result == promoted_path
    assert (specs_dir / "my-spec.md").exists()
    assert not draft.exists()
