# →1503 gemini-ready: tighten readiness checks

**Status:** DONE — commit `7bab69e`

## Summary of changes

`api/services/gemini_ready.py` went from 6 checks (with early-return) to 9 checks
(all evaluated every time). Three new checks were added. The vague-token list was
expanded. The `has_file_paths` check got stricter. The frontend chip now shows all
9 checks in a tooltip on both ready and not-ready states.

---

## New check definitions

| # | Name | Rule |
|---|------|------|
| 1 | `plan_path_present` | Description mentions a docs/spec, ~/.myos/specs, or ~/.claude/plans path |
| 2 | `file_exists` | That file exists on disk |
| 3 | `has_ac_checkboxes` | File has at least one `- [ ]` line |
| 4 | `no_vague_ac` | No AC line contains vague tokens (TBD, ?, TODO, review, maybe, consider, explore, discuss, clarify, figure out, we'll see, either, depends) |
| 5 | `has_file_paths` | At least one non-self path in the spec body resolves to a real file in the repo |
| 6 | `ac_count_threshold` | **NEW** — at least 3 unchecked AC items |
| 7 | `referenced_files_exist` | **NEW** — at least 50% of non-self file paths in body resolve to real files |
| 8 | `in_repo_scope` | **NEW** — title doesn't start with "upstream" prefix; body doesn't mention "upstream ostk", "different repo", etc. |
| 9 | `is_unblocked` | No open blockers (tasks only; specs auto-pass) |

---

## Key decisions

**`\bor\b` token dropped**: Too common in legitimate AC ("add or update"). Spec left the decision to judgment — skipped.

**`has_file_paths` stricter**: Previously any `.md` matched (including the spec itself). Now requires at least one non-self path that resolves to a real file on disk.

**50% threshold for `referenced_files_exist`**: Allows specs that mention future-tense files ("will create api/routers/git.py") to still pass if at least half their references resolve.

**No early-return**: All 9 checks are evaluated regardless. `ready = all(c.passed for c in checks)`. Guards exist (skips disk reads if file doesn't exist) but still emit a row for every check.

---

## →1472 false positive: before vs after

**Before**: →1472 "Upstream ostk: promote backfiller in promote.rs" passed all 6 checks because it linked to a real spec file with AC checkboxes and file paths. `ready=True`.

**After**: The `in_repo_scope` check fires immediately on the title prefix "Upstream ostk:". `in_repo_scope.passed=False` → `ready=False`. The spec's own `.md` path is also excluded from `has_file_paths` since it's the plan path itself.

---

## Test results

### pytest (51 targeted, 262 with test_tasks.py)

```
51 passed in 0.20s   # api/tests/test_gemini_ready.py alone
262 passed, 1 warning in 2.55s   # + test_tasks.py
```

### vitest (GeminiReadyChip)

```
Test Files  2 passed (2)
     Tests  14 passed (14)
  Duration  495ms
=== EXIT 0 ===
```

---

## Files changed

- `api/services/gemini_ready.py` — 9 checks, no early-return, new helpers
- `api/tests/test_gemini_ready.py` — 51 tests (up from ~30), new test classes for checks 6/7/8
- `api/tests/fixtures/gemini_ready/fail_too_few_ac.md` — 2 AC items fixture
- `api/tests/fixtures/gemini_ready/fail_self_ref_only.md` — self-reference-only fixture
- `api/tests/fixtures/gemini_ready/pass_mixed_file_refs.md` — 1 real + 1 nonexistent file
- `app/src/components/GeminiReadyChip.tsx` — tooltip shows all 9 checks with ✓/✗
- `app/src/components/GeminiReadyChip.test.tsx` — 8 new component tests
