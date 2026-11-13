"""Regression tests for →3107: Specs History shows all completed specs.

Covers:
1. All completed status synonyms (complete, completed, done, shipped,
   implemented, closed, archived) resolve to status='complete' and
   stage='complete' (never 'draft' or 'ready', even without checkboxes),
   and never inflate /api/specs/counts unfinished.
2. Archived specs in ~/.youros/specs/archive/ (top-level timestamped files,
   compact timestamps, headerless markdown specs, and nested batch
   subdirectories) appear in GET /api/specs as complete, deduplicate older
   snapshots of the same slug, exclude deferred/non-spec files, and are not
   re-archived on subsequent polls.
3. Completed specs in repo docs/spec/, docs/specs/, docs/archive/, and
   docs/spec/archive/ appear in GET /api/specs as complete, while unfinished
   repo specs/drafts remain excluded (preserving UAT item 8).
4. Linked tasks closed in rotated .ostk/needles/issues.jsonl.1 and singular
   `Task: →NNN` frontmatter keys resolve task_summary and flip specs to
   complete.
5. Newly auto-archived shipped specs stay visible in History (no black hole)
   and >50 completed specs are returned without any artificial cap.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def isolated_dirs(monkeypatch):
    """Create clean temporary repo + user directories without '/test_' in paths.

    Note: _is_test_artifact_spec matches '/test_' in paths to suppress leaked
    test-fixture filenames, so pytest's default tmp_path (which embeds the test
    function name '/test_...') would trigger the path filter when absolute
    user-dir paths are inspected. Using a neutral prefix exercises real
    production path shapes cleanly.
    """
    from services import ostk as ostk_module

    base = Path(tempfile.mkdtemp(prefix="youros_specs_hist_"))
    repo_root = base / "workspace"
    user_specs = base / "home_youros" / "specs"
    user_drafts = base / "home_youros" / "drafts"
    needles_dir = repo_root / ".ostk" / "needles"

    repo_root.mkdir(parents=True, exist_ok=True)
    user_specs.mkdir(parents=True, exist_ok=True)
    user_drafts.mkdir(parents=True, exist_ok=True)
    needles_dir.mkdir(parents=True, exist_ok=True)
    (needles_dir / "issues.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr("config.PROJECT_ROOT", repo_root)
    monkeypatch.setattr("config.OSTK_DIR", repo_root / ".ostk")
    monkeypatch.setattr(ostk_module, "PROJECT_DIR", str(repo_root))
    monkeypatch.setattr(ostk_module, "OSTK_DIR", repo_root / ".ostk")
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", user_specs)
    monkeypatch.setattr(ostk_module, "USER_DRAFTS_DIR", user_drafts)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(repo_root))
    monkeypatch.setenv("OSTK_PROJECT_ROOT", str(repo_root))

    try:
        yield {
            "base": base,
            "repo_root": repo_root,
            "user_specs": user_specs,
            "user_drafts": user_drafts,
            "needles_dir": needles_dir,
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.mark.asyncio
async def test_all_completed_status_synonyms_resolve_to_complete_stage(
    client, isolated_dirs, monkeypatch
):
    from services import ostk as ostk_module

    async def fake_list_tasks():
        return []

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    synonyms = [
        ("complete", "Spec Alpha Complete"),
        ("completed", "Spec Beta Completed"),
        ("done", "Spec Gamma Done"),
        ("Shipped", "Spec Delta Shipped"),
        ("Implemented", "Spec Epsilon Implemented"),
        ("closed", "Spec Zeta Closed"),
        ("archived", "Spec Eta Archived"),
    ]
    user_specs = isolated_dirs["user_specs"]
    for raw_status, title in synonyms:
        slug = title.lower().replace(" ", "-")
        # Intentionally minimal body with zero checkboxes: completed specs
        # must NEVER be classified as husk='True' / stage='draft'.
        (user_specs / f"{slug}.md").write_text(
            f"---\ntitle: {title}\nstatus: {raw_status}\n---\n\nShort summary.\n",
            encoding="utf-8",
        )

    resp = await client.get("/api/specs")
    assert resp.status_code == 200
    docs = resp.json()["docs"]
    by_title = {d["title"]: d for d in docs}

    for _, title in synonyms:
        assert title in by_title, f"Missing completed spec {title!r}; got {list(by_title)}"
        doc = by_title[title]
        assert doc["status"] == "complete", f"{title} had status={doc['status']!r}"
        assert doc["stage"] == "complete", f"{title} had stage={doc['stage']!r}"
        assert doc.get("husk") is False, f"{title} must not be marked husk"
        assert not doc.get("needs_clarity"), f"{title} must not be flagged needs_clarity"

    counts_resp = await client.get("/api/specs/counts")
    assert counts_resp.status_code == 200
    counts = counts_resp.json()
    assert counts["unfinished"] == 0
    assert counts["by_stage"].get("complete") == len(synonyms)


@pytest.mark.asyncio
async def test_archived_user_specs_top_level_and_nested_subdirs_included_in_history(
    client, isolated_dirs, monkeypatch
):
    from services import ostk as ostk_module

    async def fake_list_tasks():
        return []

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    archive_dir = isolated_dirs["user_specs"] / "archive"
    batch_dir = archive_dir / "2026-05-17-mychat-merge"
    batch_dir.mkdir(parents=True, exist_ok=True)

    # 1. Newer timestamped archive snapshot with quoted title and pre-archive status: spec
    (archive_dir / "2026-06-05T195341Z-slides-preview.md").write_text(
        '---\ntitle: "Slides Preview Inside Drive"\nstatus: spec\n'
        "promoted_at: 2026-06-05T18:00:00Z\n---\n\n## Problem\nRender slides.\n",
        encoding="utf-8",
    )
    # Older duplicate snapshot of the same canonical slug -> must deduplicate
    (archive_dir / "2026-05-01T000000Z-slides-preview.md").write_text(
        "---\ntitle: Old Slides Draft\nstatus: draft\n---\n\n## Problem\nOld.\n",
        encoding="utf-8",
    )

    # 2. Compact timestamp prefix + status: done
    (archive_dir / "20260702T202913Z-phone-command-ingest.md").write_text(
        "---\ntitle: Phone Command Ingest\nstatus: done\n---\n\n## Objective\nSMS bridge.\n",
        encoding="utf-8",
    )

    # 3. Headerless archived spec (no YAML frontmatter, H1 # Spec: heading)
    (archive_dir / "gemini-cli-integration.md").write_text(
        "# Spec: Gemini CLI Integration\n\n## Objective\nWire Gemini CLI.\n\n"
        "## Acceptance Criteria\n- [x] Works end to end\n",
        encoding="utf-8",
    )

    # 4. Nested batch subdirectory inside archive/
    (batch_dir / "01-foundation-layer.md").write_text(
        '---\ntitle: "MyChat Spec 1: Foundation Layer"\nstatus: draft\n---\n\n'
        "## Problem\nCore chat layout.\n",
        encoding="utf-8",
    )

    # 5. Deferred spec in archive/ -> explicitly NOT completed, must be excluded
    (archive_dir / "20260702T202921Z-deferred-watcher.md").write_text(
        "---\ntitle: Deferred Watcher\nstatus: deferred\n---\n\n## Problem\nLater.\n",
        encoding="utf-8",
    )

    # 6. Stray non-spec digest in archive/ -> must be excluded
    (archive_dir / "2026-05-20T234417Z-intel-digest.md").write_text(
        "## Intel Digest\n\nDaily bullet notes with no spec structure.\n",
        encoding="utf-8",
    )

    resp1 = await client.get("/api/specs")
    assert resp1.status_code == 200
    docs1 = resp1.json()["docs"]
    titles = {d["title"] for d in docs1}

    assert "Slides Preview Inside Drive" in titles
    assert "Old Slides Draft" not in titles
    assert "Phone Command Ingest" in titles
    assert "Spec: Gemini CLI Integration" in titles or "Gemini CLI Integration" in titles
    assert "MyChat Spec 1: Foundation Layer" in titles
    assert "Deferred Watcher" not in titles
    assert not any("intel" in d["title"].lower() for d in docs1)

    for d in docs1:
        assert d["status"] == "complete", f"Archived spec {d['title']} status={d['status']}"
        assert d["stage"] == "complete", f"Archived spec {d['title']} stage={d['stage']}"

    # Poll a second time to verify archived files were NOT re-renamed with a double timestamp
    files_before = sorted(p.name for p in archive_dir.rglob("*.md"))
    resp2 = await client.get("/api/specs")
    assert resp2.status_code == 200
    files_after = sorted(p.name for p in archive_dir.rglob("*.md"))
    assert files_after == files_before


@pytest.mark.asyncio
async def test_completed_repo_specs_and_archives_included_without_leaking_unfinished_husks(
    client, isolated_dirs, monkeypatch
):
    from services import ostk as ostk_module

    async def fake_list_tasks():
        return []

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    repo_root = isolated_dirs["repo_root"]
    repo_spec = repo_root / "docs" / "spec"
    repo_specs_plural = repo_root / "docs" / "specs"
    repo_archive = repo_root / "docs" / "archive"
    repo_draft = repo_root / "docs" / "draft"
    for d in (repo_spec, repo_specs_plural, repo_archive, repo_draft):
        d.mkdir(parents=True, exist_ok=True)

    # Completed repo specs across formats & directories
    (repo_spec / "yaml-shipped.md").write_text(
        "---\ntitle: YAML Shipped Repo Spec\nstatus: shipped\n---\n\n## Problem\nDone.\n",
        encoding="utf-8",
    )
    (repo_spec / "inline-implemented.md").write_text(
        "# Inline Implemented Repo Spec\n\n**Status:** Implemented\n\n## Problem\nDone.\n",
        encoding="utf-8",
    )
    (repo_spec / "all-ac-checked.md").write_text(
        "---\ntitle: All Checked Repo Spec\nstatus: spec\n---\n\n"
        "## Acceptance criteria\n- [x] Criterion one\n- [x] Criterion two\n",
        encoding="utf-8",
    )
    (repo_specs_plural / "plural-completed.md").write_text(
        "---\ntitle: Plural Dir Completed Spec\nstatus: completed\n---\n\n## Problem\nDone.\n",
        encoding="utf-8",
    )
    (repo_archive / "archived-repo-spec.md").write_text(
        "---\ntitle: Archived Repo Spec\nstatus: spec\n---\n\n## Objective\nDone.\n",
        encoding="utf-8",
    )

    # Unfinished repo husk & draft -> MUST NOT leak (UAT item 8 invariant)
    (repo_spec / "unfinished-repo-husk.md").write_text(
        "---\ntitle: Unfinished Repo Husk\nstatus: spec\n---\n\n- [ ] Not built yet\n",
        encoding="utf-8",
    )
    (repo_draft / "unfinished-repo-draft.md").write_text(
        "---\ntitle: Unfinished Repo Draft\nstatus: draft\n---\n\n## Problem\nWIP.\n",
        encoding="utf-8",
    )

    resp = await client.get("/api/specs")
    assert resp.status_code == 200
    docs = resp.json()["docs"]
    titles = {d["title"] for d in docs}

    expected_completed = {
        "YAML Shipped Repo Spec",
        "Inline Implemented Repo Spec",
        "All Checked Repo Spec",
        "Plural Dir Completed Spec",
        "Archived Repo Spec",
    }
    assert expected_completed.issubset(titles), f"Missing completed repo specs: {expected_completed - titles}"
    assert "Unfinished Repo Husk" not in titles
    assert "Unfinished Repo Draft" not in titles

    for d in docs:
        if d["title"] in expected_completed:
            assert d["status"] == "complete"
            assert d["stage"] == "complete"


@pytest.mark.asyncio
async def test_rotated_closed_needles_and_singular_task_frontmatter_flip_spec_to_complete(
    client, isolated_dirs, monkeypatch
):
    from services import ostk as ostk_module

    # Active list_tasks() only knows about open task 3107; closed tasks 1706,
    # 1801, 1802 were rotated into issues.jsonl.1.
    async def fake_list_tasks():
        return [{"id": "→3107", "title": "Current task", "status": "open"}]

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    needles_dir = isolated_dirs["needles_dir"]
    rotated_lines = [
        json.dumps({"id": 1706, "title": "Discord service", "status": "closed"}),
        json.dumps({"id": 1801, "title": "Part 1", "status": "closed"}),
        json.dumps({"id": 1802, "title": "Part 2", "status": "closed"}),
    ]
    (needles_dir / "issues.jsonl.1").write_text(
        "\n".join(rotated_lines) + "\n", encoding="utf-8"
    )

    user_specs = isolated_dirs["user_specs"]
    (user_specs / "discord-connected-service.md").write_text(
        "---\ntitle: Discord as a Connected Service\nstatus: spec\nTask: →1706\n---\n\n"
        "## Problem\nConnect Discord.\n\n## Acceptance criteria\n- [ ] Bot connects\n",
        encoding="utf-8",
    )
    (user_specs / "rotated-task-counts.md").write_text(
        '---\ntitle: Rotated Task Counts\nstatus: complete\ntasks: ["1801", "1802"]\n---\n\n'
        "## Acceptance criteria\n- [x] First\n- [x] Second\n",
        encoding="utf-8",
    )

    resp = await client.get("/api/specs")
    assert resp.status_code == 200
    by_title = {d["title"]: d for d in resp.json()["docs"]}

    discord = by_title["Discord as a Connected Service"]
    assert discord["status"] == "complete"
    assert discord["stage"] == "complete"
    assert discord["task_summary"] == {"total": 1, "open": 0, "closed": 1}

    rotated = by_title["Rotated Task Counts"]
    assert rotated["status"] == "complete"
    assert rotated["stage"] == "complete"
    assert rotated["task_summary"] == {"total": 2, "open": 0, "closed": 2}


@pytest.mark.asyncio
async def test_auto_archived_shipped_spec_remains_visible_in_history_and_no_pagination_cap(
    client, isolated_dirs, monkeypatch
):
    from services import ostk as ostk_module

    async def fake_list_tasks():
        return [{"id": "→9001", "title": "Ship feature", "status": "closed"}]

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    repo_root = isolated_dirs["repo_root"]
    ref_file = repo_root / "app" / "src" / "Feature.tsx"
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text("export const Feature = () => null;\n", encoding="utf-8")

    user_specs = isolated_dirs["user_specs"]
    archive_dir = user_specs / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    # 30 completed specs in user_specs + 30 archived specs in user_specs/archive = 60
    for idx in range(30):
        (user_specs / f"completed-alpha-{idx:02d}.md").write_text(
            f"---\ntitle: Completed Alpha {idx:02d}\nstatus: complete\n---\n\n- [x] Done\n",
            encoding="utf-8",
        )
        (archive_dir / f"2026-06-01T1200{idx:02d}Z-archived-beta-{idx:02d}.md").write_text(
            f"---\ntitle: Archived Beta {idx:02d}\nstatus: spec\n---\n\n## Problem\nDone.\n",
            encoding="utf-8",
        )

    # Plus 1 newly shipped spec in user_specs top-level that triggers auto-archive
    (user_specs / "newly-shipped-feature.md").write_text(
        "---\ntitle: Newly Shipped Feature\nstatus: spec\n---\n\n"
        "Implemented in app/src/Feature.tsx via →9001.\n",
        encoding="utf-8",
    )

    resp1 = await client.get("/api/specs")
    assert resp1.status_code == 200
    docs1 = resp1.json()["docs"]
    assert len(docs1) == 61, f"Expected all 61 completed specs with no cap, got {len(docs1)}"

    newly1 = next((d for d in docs1 if d["title"] == "Newly Shipped Feature"), None)
    assert newly1 is not None, "Auto-archived shipped spec vanished on first poll"
    assert newly1["status"] == "complete"
    assert newly1["stage"] == "complete"
    assert not (user_specs / "newly-shipped-feature.md").exists()

    # Second poll must also return all 61 from archive + user_specs
    resp2 = await client.get("/api/specs")
    assert resp2.status_code == 200
    docs2 = resp2.json()["docs"]
    assert len(docs2) == 61
    newly2 = next((d for d in docs2 if d["title"] == "Newly Shipped Feature"), None)
    assert newly2 is not None
    assert newly2["status"] == "complete"
    assert newly2["stage"] == "complete"
