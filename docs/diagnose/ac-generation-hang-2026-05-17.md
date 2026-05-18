# AC Generation Hang Diagnosis — →1463

**Date:** 2026-05-17  
**Agent:** diagnose-ac-generation-hang-1463-ed8288  
**Status:** Root cause confirmed. Fix proposed.

---

## TL;DR

When running on subscription auth (no `ANTHROPIC_API_KEY`), both
`POST /specs/draft` and `POST /specs/from-roadmap-line` silently skip
AC generation. The draft file lands with `status: draft` and an empty
body. The frontend shows "Generating acceptance criteria..." for any
`status: draft` doc with no checkboxes — with no timeout and no escape
hatch. Three drafts are currently stuck this way.

---

## Evidence trail

### Affected drafts (as of 2026-05-17)

| File | status | has_ac | body_len | Created |
|---|---|---|---|---|
| `docs/draft/hooks-review-2026-05-15.md` | draft | False | 86 | 2026-05-18T03:31:26Z |
| `docs/draft/pattern-watcher-v2.md` | draft | False | 81 | 2026-05-18T03:47:36Z |
| `docs/draft/user-memory-store-improvements.md` | draft | False | 93 | 2026-05-18T03:47:36Z |

All three files contain only YAML front matter, empty body, no `- [ ]`
checkboxes.

Healthy drafts (have AC, not stuck):
- `docs/draft/bdd-invariant-test-rule-for-saa-bug-fix-briefs.md` — has_ac=True
- `docs/draft/spec-quality-gaps-audit.md` — has_ac=True

### Timeline

- `hooks-review-2026-05-15.md` created at `2026-05-18T03:31:26Z` (UTC)  
- Commit `498fdbe` (hook-title guard) committed at `2026-05-17 20:32:01 -0700` = `2026-05-18T03:32:01Z` (UTC)
- Gap: 35 seconds — the hooks draft was created just before the guard landed
- `pattern-watcher-v2.md` and `user-memory-store-improvements.md` created 16 minutes AFTER the guard at `2026-05-18T03:47:36Z`; their titles have no "hook" so they were not blocked

---

## Root cause

### Primary: `_resolve_api_key` returns `""` on subscription auth

`api/services/chat_providers.py:783-826` — `_resolve_api_key("anthropic_api_key")` checks:
1. Org keychain
2. `ostk secret get ANTHROPIC_API_KEY`
3. `settings.json`
4. `os.environ["ANTHROPIC_API_KEY"]`

None of these paths know about the Claude subscription. When running on
subscription auth, all four return empty. The function returns `""`.

### How that breaks `POST /specs/draft` (`api/routers/specs.py:366-472`)

```python
api_key = await _resolve_api_key("anthropic_api_key")   # returns ""
if api_key:                                              # False on subscription
    ...generate AC...
    ac_written = True
# ac_written stays False
# draft is not promoted, status stays "draft"
```

The entire AC block is also wrapped in `except Exception: pass` (line 433),
so any other error is also swallowed without logging.

### How that breaks `POST /specs/from-roadmap-line` / `_finish_ac_and_promote`

The background task has TWO silent early-return paths:

```python
async def _finish_ac_and_promote() -> None:
    if not header_written:       # Path 1 — no log, silently returns
        return
    try:
        api_key = await _resolve_api_key("anthropic_api_key")
        if not api_key:          # Path 2 — no log, silently returns on subscription
            return
        ...
    except Exception:
        logger.exception(...)    # This one IS logged, at least
```

If `header_written` is False (file path check failed) OR `api_key` is `""`,
the task returns with nothing written and no log entry. The draft stays stuck.

### Why the spinner never clears

`app/src/pages/Specs.tsx:1211-1220`:

```tsx
{doc.status === "draft" && !(doc.acceptance_criteria?.length) && (
  <div data-testid="generating-criteria-indicator">
    <span className="animate-spin" />
    <p>Generating acceptance criteria...</p>
  </div>
)}
```

This fires for **any** `status: draft` doc with no AC checkboxes — regardless
of whether generation was ever attempted or whether it was skipped. There is
no timeout, no "generation failed" state, and no escape hatch UI.

### Existing test documents the broken behavior

`api/tests/test_specs.py:100-144` — `test_create_draft_leaves_as_draft_when_ac_generation_fails`:

```python
# No API key -> the route skips the AI branch entirely.
monkeypatch.setattr("services.chat_providers._resolve_api_key",
                    AsyncMock(return_value=None))
...
assert data["status"] == "draft"
assert data["promoted_path"] is None
assert draft_file.exists()
```

Test passes but does NOT assert the draft file has any placeholder content.
The test describes this as "graceful fallback" — but the UI renders
it as an infinite spinner. Test and UI are misaligned.

---

## Hypothesis (confirmed)

**Single root cause:** `_resolve_api_key` has no subscription auth path.
When the user is on Claude subscription (no API key), both create flows
silently skip AC generation, leaving drafts with empty bodies. The frontend
spinner has no timeout and fires indefinitely for empty-body drafts.

The `doc_promote` function (checked) does NOT have a hook-title guard — only
`doc_draft` does (added in `498fdbe`). So the promote path is not the
blocker; the blocker is that AC is never written, so promote is never called.

---

## Proposed fix

### What to change

**File: `api/routers/specs.py`** (in conflict zone — do NOT apply, unified diff below)

**Change 1:** In `create_draft`, when `api_key` is empty, log a warning and
write a user-editable placeholder AC block. This means:
- The draft gets a real checkbox in the body
- `acceptance_criteria` becomes `[{"text": "...", "checked": false}]`
- The spinner clears immediately
- The frontend shows "Next step: 1 acceptance criteria defined. Promote..."
- User can edit the placeholder then promote

**Change 2:** In `_finish_ac_and_promote` (roadmap flow), do the same: when
`api_key` is empty, write the placeholder and promote, instead of silently
returning.

**Change 3:** Add a `logger.warning` to both no-key paths so future
occurrences are visible in `/tmp/dev-backend.log`.

### Unified diff

```diff
--- a/api/routers/specs.py
+++ b/api/routers/specs.py
@@ create_draft, lines ~392-434 @@
     ac_written = False
     try:
         from services.chat_providers import _resolve_api_key
         import anthropic

         api_key = await _resolve_api_key("anthropic_api_key")
         if api_key:
             client = anthropic.AsyncAnthropic(api_key=api_key)
             ...
             if "- [ ]" in ac_text:
                 ac_written = True
-    except Exception:
-        pass  # If AI generation fails, the draft is still created without AC
+    except Exception:
+        logger.warning("create_draft: AC generation failed for %r", body.title, exc_info=True)
+
+    if not ac_written:
+        # No API key (subscription auth) or AI call failed.
+        # Write a user-editable placeholder so the draft isn't stuck
+        # showing "Generating acceptance criteria..." forever. The user
+        # edits the placeholder before promoting.
+        logger.warning(
+            "create_draft: AC generation skipped for %r (no API key or error). "
+            "Writing placeholder.",
+            body.title,
+        )
+        from pathlib import Path
+        _draft_path = result.strip()
+        _docs_root = (Path(ostk.cwd) / "docs").resolve()
+        _full_path = (Path(ostk.cwd) / _draft_path).resolve()
+        if _full_path.exists() and _full_path.is_relative_to(_docs_root):
+            _placeholder = (
+                "\n## Acceptance criteria\n\n"
+                "- [ ] (replace with your first acceptance criterion)\n"
+                "- [ ] (replace with your second acceptance criterion)\n"
+                "- [ ] (replace with your third acceptance criterion)\n"
+            )
+            _full_path.write_text(_full_path.read_text() + _placeholder)
+            ac_written = True

@@ _finish_ac_and_promote, lines ~1063-1074 @@
     async def _finish_ac_and_promote() -> None:
         if not header_written:
+            logger.warning(
+                "from_roadmap_line: header not written for %r, skipping AC generation",
+                draft_path,
+            )
             return
         try:
             from services.chat_providers import _resolve_api_key
             import anthropic

             api_key = await _resolve_api_key("anthropic_api_key")
             if not api_key:
-                return
+                logger.warning(
+                    "from_roadmap_line: no API key for %r, writing placeholder AC",
+                    draft_path,
+                )
+                _placeholder = (
+                    "\n## Acceptance criteria\n\n"
+                    "- [ ] (replace with your first acceptance criterion)\n"
+                    "- [ ] (replace with your second acceptance criterion)\n"
+                    "- [ ] (replace with your third acceptance criterion)\n"
+                )
+                _content = full_path.read_text()
+                full_path.write_text(_content.rstrip() + _placeholder)
+                try:
+                    await ostk.doc_promote(draft_path)
+                except OstkError:
+                    pass
+                return
```

### Files affected by the fix
- `api/routers/specs.py` — main fix (conflict zone: wreath agent touches `_paths_for_spec_build`)
- `api/tests/test_specs.py` — update existing test + add RED test first

### Test strategy (RED first, then GREEN)

**RED test to write first:**
```python
@pytest.mark.asyncio
async def test_create_draft_no_api_key_writes_placeholder_not_stuck(
    client, tmp_path, monkeypatch
):
    """No API key (subscription auth): draft should have placeholder AC,
    not show infinite 'Generating...' spinner.

    Regression test for →1463.
    """
    # ... setup ...
    monkeypatch.setattr(
        "services.chat_providers._resolve_api_key",
        AsyncMock(return_value=""),  # subscription auth, no API key
    )
    resp = await client.post(
        "/api/specs/draft", json={"title": "my plan", "kind": "spec"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "draft"
    # Draft must have at least one checkbox so the frontend spinner clears
    draft_text = draft_file.read_text()
    assert "- [ ]" in draft_text, "No-key path must write placeholder AC to unblock spinner"
```

This test FAILS before the fix (no `- [ ]` in the draft). It PASSES after.

**Update existing test:**
`test_create_draft_leaves_as_draft_when_ac_generation_fails` should additionally
assert `"- [ ]" in draft_file.read_text()` after the fix lands.

### Risk assessment

- **Conflict with wreath agent:** Low. The wreath agent adds `_paths_for_spec_build`
  in a different function. The AC generation code in `create_draft` and
  `_finish_ac_and_promote` is separate from `handleBuild`. A merge conflict
  is possible (same file) but not a logic conflict.
- **Conflict with →1458 work:** None observed. 1458 is a narrative backend.
- **Regression risk:** Low. The placeholder path only fires when `ac_written`
  is False — i.e., when AI generation was already skipped. No change to
  the happy path (API key available).

---

## Immediate relief (no code change needed)

The three stuck drafts can be manually unblocked right now by editing each
file to add `- [ ]` checkboxes, then using "Promote to Spec" in the UI.
Or delete the drafts if they were accidental.

---

## Secondary issue: `doc_draft` guard prevents re-creating hook-titled drafts

Commit `498fdbe` added a guard: any title containing "hook" raises `OstkError`
in `doc_draft`. The `hooks-review-2026-05-15.md` draft was created 35 seconds
before the guard landed. If the user tries to recreate it via the UI, they
will get a 400 error. The guard is correct behavior (hooks reviews belong in
`~/.myos/hooks/`), but the stuck draft itself is an artifact of the timing.

**No fix needed here** — the draft is stuck due to the AC issue, not the guard.
The guard correctly prevents new hooks drafts. The stuck file should just be
deleted or manually promoted.

---

## Summary checklist

- [x] Root cause identified: `_resolve_api_key` has no subscription path
- [x] Both affected endpoints documented (`create_draft` + `from_roadmap_line`)
- [x] All stuck drafts found: 3 files
- [x] `doc_promote` verified: NO hook-title guard (not the blocker)
- [x] Existing tests baseline: 2 passed
- [x] Fix proposed as unified diff (not implemented — conflict zone)
- [x] RED test written (not committed — needs fix to land first)
- [x] Risk assessment: low conflict risk with wreath agent
