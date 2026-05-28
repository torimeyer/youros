# pre-design-audit.py — 3 signal bug fixes (2026-05-28)

Fixes →1795, →1796, →1797 in `/Users/torimeyer/.myos/pre-design-audit.py`.

## Bugs fixed

### →1795 — Needles/Specs false-positive on spec content

**Problem**: `search_specs` searched the full *content* of every spec `.md` file for
the concept string. A spec that uses the concept name as an example inside its body
(e.g. the pre-design-audit spec itself using "SourceBadge" as a test case) triggered
a false MATCH FOUND.

**Root cause**: `re.compile(re.escape(concept), re.IGNORECASE)` was run against
`md.read_text()` — any mention anywhere in the file counted as a hit.

**Fix**: Replaced content search with filename-slug token matching. The new logic
splits the file's stem on `-`/`_`/whitespace into slug words, then checks that every
camelCase token of the concept appears as a slug word. A spec named
`source-badge-v2.md` matches "SourceBadge"; `pre-design-audit-catch-existing-…md`
does not.

**Lines changed**: replaced the body of `search_specs` (was ~10 lines, now ~15 lines).

---

### →1796 — Literal codebase scan matched transcript/log filenames

**Problem**: The `find` command in `search_codebase` searched from `str(repo_root)` —
the entire repository root — with only coarse exclusions. Concepts like "ChatPanel"
matched `transcripts/diagnose-1464-chatpanel-tab-flak-c48009.md.stderr.log` and 19+
other transcript/log entries, triggering MATCH FOUND for a concept that may or may not
have a real source-code implementation.

**Fix**: Changed the 1a filename glob from one `find str(repo_root) …` call to a loop
over `_SRC_DIRS` (same directories already used by the grep signal). Added explicit
`-not` exclusions for `*/transcripts/*`, `*/.ostk/*`, and `*.log` filenames.

**Lines changed**: replaced the single `find str(repo_root)` block with a loop over
`_SRC_DIRS` + added 3 new `-not` flags.

---

### →1797 — Semantic scan walked into api/.venv (third-party libs)

**Problem**: `search_semantic` iterates over `_SRC_DIRS` and calls `src_dir.rglob("*")`.
Since `api/` is one of those dirs and `api/.venv/` is inside it, the rglob recurses
into virtualenv site-packages. This surfaced pygments, setuptools, and other
third-party files as POSSIBLE MATCHes.

**Fix**: Added a `continue` guard at the top of the inner loop that skips any path
whose `parts` contain `.venv`, `node_modules`, or `__pycache__`.

**Lines changed**: 3-line guard block inserted immediately after the `if not path.is_file()` check.

---

## Verification output

### Before fix

```
# SourceBadge
| Codebase (literal)  | none |
| Codebase (semantic) | app/src/components/ClaimSourceChip.tsx [chip, claim, source];
                         api/.venv/lib/python3.11/site-packages/pygments/lexers/_sourcemod_builtins.py [origin, source, tag];
                         api/services/source_library.py [source, tag] (+3 more) |
| Git log             | none |
| Needles/Specs       | docs/spec/pre-design-audit-catch-existing-patterns-before-proposing-new-infrastructure.md |
→ MATCH FOUND  (wrong — both →1795 and →1797 triggered)

# ChatPanel
| Codebase (literal)  | transcripts/diagnose-1464-chatpanel-tab-flak-c48009.md.stderr.log,
                         transcripts/swap-chatpanel-to-ws-subscriptio-fc01a0.md.stderr.log,
                         transcripts/diagnose-1464-chatpanel-tab-flak-c48009.md (+19 more) |
→ MATCH FOUND  (technically correct verdict but sources are transcripts/logs, not code — →1796)

# ZxqfQuantumWidget
→ CLEAR  (correct)
```

### After fix

```
# SourceBadge
| Codebase (literal)  | none |
| Codebase (semantic) | app/src/components/ClaimSourceChip.tsx [chip, claim, source];
                         api/services/source_library.py [source, tag] |
| Git log             | none |
| Needles/Specs       | none |
→ POSSIBLE MATCH  ✓ (semantic only, no false literal match)

# ChatPanel
| Codebase (literal)  | app/src/components/GemChatPanel.tsx, app/src/components/ChatPanel.tsx,
                         app/src/stores/notifications.ts (+9 more) |
| Codebase (semantic) | app/src/components/AgentChatThread.tsx [card, chat, panel]; … |
| Git log             | none |
| Needles/Specs       | none |
→ MATCH FOUND  ✓ (source files only, no transcripts)

# ZxqfQuantumWidget
→ CLEAR  ✓
```

## Files touched

| File | Change |
|------|--------|
| `/Users/torimeyer/.myos/pre-design-audit.py` | 3 edits — search_specs body, search_codebase 1a block, search_semantic inner loop guard |
| `docs/draft/pre-design-audit-script-fix-2026-05-28.md` | this file |
