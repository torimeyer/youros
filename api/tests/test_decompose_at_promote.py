"""Tests: doc_promote calls doc_decompose at promotion time."""
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.ostk import OstkService, OstkError


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
async def test_doc_promote_calls_decompose(svc, tmp_path):
    """doc_promote must call doc_decompose once with the promoted path and auto=True."""
    import services.ostk as _mod
    specs_dir = tmp_path / "myos" / "specs"

    _make_draft(tmp_path)

    with patch.object(svc, "doc_decompose", new_callable=AsyncMock) as mock_decompose:
        mock_decompose.return_value = {"result": "", "task_ids": []}
        with patch.object(_mod, "USER_SPECS_DIR", specs_dir):
            result = await svc.doc_promote("docs/draft/my-spec.md")

    promoted_path = str(specs_dir / "my-spec.md")
    mock_decompose.assert_called_once_with(promoted_path, auto=True)
    assert result == promoted_path


@pytest.mark.asyncio
async def test_doc_promote_succeeds_if_decompose_raises(svc, tmp_path):
    """doc_promote must succeed even when doc_decompose raises."""
    import services.ostk as _mod
    specs_dir = tmp_path / "myos" / "specs"

    _make_draft(tmp_path)

    with patch.object(svc, "doc_decompose", new_callable=AsyncMock) as mock_decompose:
        mock_decompose.side_effect = Exception("ostk binary not found")
        with patch.object(_mod, "USER_SPECS_DIR", specs_dir):
            result = await svc.doc_promote("docs/draft/my-spec.md")

    promoted_path = str(specs_dir / "my-spec.md")
    assert result == promoted_path
    assert (specs_dir / "my-spec.md").exists()
    mock_decompose.assert_called_once()
