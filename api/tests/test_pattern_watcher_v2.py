"""Tests for pattern_watcher v2 — reader, tier promotion, format, integration, resilience.

AC coverage:
  →1834  writer produces correctly formatted defer + vocab bullets
  →1835  reader calls recall_fault with intent="narrative", limit=5
  →1836  tier promotion writes decision key pattern:tier:<cluster_id>
  →1837  integration: write bullet → ostk-recall ingest → query round trip
  →1839  resilience: daemon stopped, turns still complete, observations still write
  →1833  resilience: same as 1839 — turn proceeds without errors when daemon is down
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# →1834 — bullet format tests
# ---------------------------------------------------------------------------

def test_defer_bullet_has_timestamp_kind_and_reason(tmp_path: Path) -> None:
    """task:defer bullet has ISO timestamp, 'task:defer', and reason= field."""
    from services.pattern_watcher import observe_turn

    tasks = tmp_path / "tasks.md"
    observe_turn("defer this later", "ok", tasks_path=tasks, vocab_path=tmp_path / "v.md")

    content = tasks.read_text()
    # Timestamp
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", content), "missing ISO timestamp"
    # Kind
    assert "task:defer" in content
    # reason= field
    assert "reason=" in content
    # No extra whitespace at end of bullet
    for line in content.splitlines():
        if line.startswith("- "):
            assert line == line.rstrip(), f"trailing whitespace in bullet: {repr(line)}"


def test_vocab_bullet_has_timestamp_kind_token_surrounding(tmp_path: Path) -> None:
    """vocab:new bullet has ISO timestamp, 'vocab:new', token=, surrounding= fields."""
    from services.pattern_watcher import observe_turn

    vocab = tmp_path / "vocab.md"
    observe_turn("use elit here", "sure", tasks_path=tmp_path / "t.md", vocab_path=vocab)

    content = vocab.read_text()
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", content), "missing ISO timestamp"
    assert "vocab:new" in content
    assert 'token="elit"' in content
    assert "surrounding=" in content


# ---------------------------------------------------------------------------
# →1835 — reader calls recall_fault with intent="narrative", limit=5
# ---------------------------------------------------------------------------

def test_read_context_for_turn_calls_recall_fault_with_correct_args() -> None:
    """read_context_for_turn calls _call_recall_fault with intent='narrative', limit=5."""
    from services.pattern_watcher import read_context_for_turn

    with patch("services.pattern_watcher._call_recall_fault") as mock_fault:
        mock_fault.return_value = "some patterns"
        result = read_context_for_turn("how do you handle deferrals")

    mock_fault.assert_called_once()
    _, kwargs = mock_fault.call_args
    # The first positional arg is the query
    args, _ = mock_fault.call_args
    assert kwargs.get("intent", args[1] if len(args) > 1 else None) == "narrative", \
        "intent must be 'narrative'"
    assert kwargs.get("limit", args[2] if len(args) > 2 else None) == 5, \
        "limit must be 5"


def test_read_context_for_turn_trims_query_to_200_chars() -> None:
    """The query passed to _call_recall_fault is trimmed to 200 chars."""
    from services.pattern_watcher import read_context_for_turn

    long_msg = "x" * 500
    with patch("services.pattern_watcher._call_recall_fault") as mock_fault:
        mock_fault.return_value = None
        read_context_for_turn(long_msg)

    args, _ = mock_fault.call_args
    assert len(args[0]) <= 200, "query not trimmed to 200 chars"


def test_read_context_for_turn_returns_labeled_section_when_content_found() -> None:
    """When recall returns content, reader wraps it in the WHAT MYOS HAS LEARNED label."""
    from services.pattern_watcher import read_context_for_turn

    with patch("services.pattern_watcher._call_recall_fault") as mock_fault:
        mock_fault.return_value = "- you defer P3s"
        result = read_context_for_turn("hello")

    assert result is not None
    assert "WHAT MYOS HAS LEARNED ABOUT YOU" in result
    assert "- you defer P3s" in result


def test_read_context_for_turn_returns_none_when_recall_returns_nothing() -> None:
    """Returns None when _call_recall_fault returns None."""
    from services.pattern_watcher import read_context_for_turn

    with patch("services.pattern_watcher._call_recall_fault") as mock_fault:
        mock_fault.return_value = None
        result = read_context_for_turn("hello")

    assert result is None


def test_read_context_for_turn_returns_none_on_exception() -> None:
    """Returns None without raising when _call_recall_fault raises."""
    from services.pattern_watcher import read_context_for_turn

    with patch("services.pattern_watcher._call_recall_fault", side_effect=RuntimeError("daemon down")):
        result = read_context_for_turn("hello")

    assert result is None


# ---------------------------------------------------------------------------
# →1836 — tier promotion writes decision key pattern:tier:<cluster_id>
# ---------------------------------------------------------------------------

def test_promote_tier_calls_ostk_decide_with_correct_key() -> None:
    """promote_tier(cluster_id, tier) calls subprocess with 'ostk decide pattern:tier:<id> <tier>'."""
    from services.pattern_watcher import promote_tier

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = promote_tier("abc123", 2)

    assert result is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "ostk"
    assert cmd[1] == "decide"
    assert cmd[2] == "pattern:tier:abc123"
    assert str(2) in cmd


def test_promote_tier_includes_reason_for_confirm() -> None:
    """Tier 2 promotion includes 'user confirmed in panel' as the reason."""
    from services.pattern_watcher import promote_tier

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        promote_tier("abc123", 2)

    cmd = mock_run.call_args[0][0]
    assert any("confirmed" in str(arg) for arg in cmd), "reason 'confirmed' not found in cmd"


def test_promote_tier_includes_reason_for_approve_silent() -> None:
    """Tier 3 promotion includes 'approved silent action' as the reason."""
    from services.pattern_watcher import promote_tier

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        promote_tier("abc123", 3)

    cmd = mock_run.call_args[0][0]
    assert any("silent" in str(arg) for arg in cmd), "reason 'silent' not found in cmd"


def test_promote_tier_returns_false_on_subprocess_failure() -> None:
    """Returns False when subprocess exits non-zero."""
    from services.pattern_watcher import promote_tier

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = promote_tier("abc123", 2)

    assert result is False


def test_promote_tier_returns_false_on_exception() -> None:
    """Returns False without raising when subprocess raises."""
    from services.pattern_watcher import promote_tier

    with patch("subprocess.run", side_effect=FileNotFoundError("ostk not found")):
        result = promote_tier("abc123", 2)

    assert result is False


# ---------------------------------------------------------------------------
# →1839 / →1833 — resilience: daemon stopped
# ---------------------------------------------------------------------------

def test_observe_turn_writes_markdown_when_recall_fault_raises(tmp_path: Path) -> None:
    """Even when _call_recall_fault raises (daemon down), observe_turn still writes bullets."""
    from services.pattern_watcher import observe_turn

    tasks = tmp_path / "tasks.md"
    vocab = tmp_path / "vocab.md"

    # Patch _call_recall_fault to simulate daemon down (should not affect writer)
    with patch("services.pattern_watcher._call_recall_fault", side_effect=RuntimeError("daemon down")):
        observe_turn("defer this later", "ok", tasks_path=tasks, vocab_path=vocab)

    assert tasks.exists(), "tasks.md not written when recall daemon is down"


def test_read_context_returns_none_when_ostk_not_found() -> None:
    """When ostk binary is missing, read_context_for_turn returns None without raising."""
    from services.pattern_watcher import read_context_for_turn

    with patch("services.pattern_watcher._call_recall_fault", side_effect=FileNotFoundError("ostk")):
        result = read_context_for_turn("hello")

    assert result is None


# ---------------------------------------------------------------------------
# →1837 — integration: write bullet → ostk-recall ingest → query round trip
# ---------------------------------------------------------------------------

def test_integration_recall_round_trip(tmp_path: Path) -> None:
    """→1837 — temp observations dir + temp ostk-recall corpus, real round trip.

    Writes a uniquely-tagged bullet BEFORE any ingest runs (the queued backlog),
    runs `ostk-recall scan` against an isolated config, and retrieves the chunk
    back through `ostk-recall inspect`. Proves: bullet write → ingest → query
    round trip, and that observations queued while no watcher was running
    ingest on the next scan (the backlog half of the resilience AC).

    Skipped only when the ostk-recall binary is not installed on this machine.
    """
    import shutil
    import sqlite3

    ostk_recall = shutil.which("ostk-recall")
    if not ostk_recall:
        pytest.skip("ostk-recall not available")

    obs_dir = tmp_path / "observations"
    obs_dir.mkdir()
    corpus_root = tmp_path / "corpus-root"
    corpus_root.mkdir()

    # Reuse the machine's shared model cache so the test never downloads anything.
    shared_models = Path.home() / ".local" / "share" / "ostk-recall" / "models"
    if shared_models.exists():
        (corpus_root / "models").symlink_to(shared_models)

    config = tmp_path / "config.toml"
    config.write_text(
        "[corpus]\n"
        f'root = "{corpus_root}"\n\n'
        "[embedder]\n"
        'model = "minishlab/potion-retrieval-32M"\n\n'
        "[reranker]\n"
        "enabled = false\n\n"
        "[watch]\n"
        "enabled = false\n\n"
        "[[sources]]\n"
        'kind = "markdown"\n'
        'project = "observations"\n'
        f'paths = ["{obs_dir}"]\n'
    )

    # Bullet written before any ingest runs — this is the queued backlog.
    marker = f"integration-marker-{int(time.time())}"
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (obs_dir / "tasks.md").write_text(f'- {ts} task:defer reason="{marker}"\n')

    init = subprocess.run(
        [ostk_recall, "init", "--config", str(config)],
        capture_output=True, text=True, timeout=120,
    )
    assert init.returncode == 0, f"init failed:\n{init.stderr[-2000:]}"

    scan = subprocess.run(
        [ostk_recall, "scan", "--config", str(config)],
        capture_output=True, text=True, timeout=120,
    )
    assert scan.returncode == 0, f"scan failed:\n{scan.stderr[-2000:]}"
    scan_out = scan.stdout + scan.stderr
    assert "upserted=1" in scan_out, f"bullet not ingested:\n{scan_out[-2000:]}"

    conn = sqlite3.connect(corpus_root / "ingest.sqlite")
    try:
        rows = conn.execute(
            "select chunk_id from ingest_chunks where source_id='tasks.md'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, f"expected 1 ingested chunk, got {rows}"

    inspect = subprocess.run(
        [ostk_recall, "inspect", rows[0][0], "--config", str(config)],
        capture_output=True, text=True, timeout=120,
    )
    assert inspect.returncode == 0, f"inspect failed:\n{inspect.stderr[-2000:]}"
    assert marker in inspect.stdout, (
        f"round trip failed — marker not retrievable:\n{inspect.stdout[-2000:]}"
    )
    assert '"project": "observations"' in inspect.stdout
