# Fix →1463: Write Placeholder AC When No API Key (Subscription Auth)

**Date:** 2026-05-17
**Task:** →1463
**Status:** Complete

## Problem

When running on subscription auth (no `ANTHROPIC_API_KEY`), the `create_draft` and
`_finish_ac_and_promote` functions in `api/routers/specs.py` silently swallowed
exceptions and left drafts with no AC block. The Specs page spinner
(`status: draft` + no checkboxes) had no timeout and spun forever.

Three drafts were stuck: hooks-review-2026-05-15.md, pattern-watcher-v2.md,
user-memory-store-improvements.md.

## Root cause

`_resolve_api_key` has no subscription-auth path. On subscription (no
`ANTHROPIC_API_KEY`), `api_key` resolves to empty/None, the `if api_key:` guard
skips the AI branch, and bare `except Exception: pass` blocks ate any errors.
No placeholder was ever written, so drafts stayed body-empty.

## Fix

1. **`create_draft`**: after the AC generation block, if `ac_written` is False,
   write a 3-checkbox placeholder block. Log a warning. Do NOT set `ac_written = True`
   so the draft stays in `"draft"` state for the user to edit before promoting.

2. **`_finish_ac_and_promote`**: when `api_key` is empty, write the same
   placeholder, call `doc_promote`, and return. No longer silently exits.

3. Replaced `except Exception: pass` with `logger.warning(..., exc_info=True)`.

## Commits (on branch worktree-agent-apply-fix-1463-from-d-05827291)

- `b72226b` scaffold(→1463): create fix doc
- `6ef6b75` test(→1463): RED — draft must have placeholder AC when no API key
- `bda55a5` fix(→1463): write placeholder AC when no API key (subscription auth)
- `81541ea` fix(→1463): repair 3 stuck drafts with placeholder AC block

## Test result

```
api/tests/test_specs.py::test_create_draft_no_api_key_writes_placeholder_not_stuck PASSED
api/tests/test_specs.py::test_create_draft_leaves_as_draft_when_ac_generation_fails PASSED
46 passed in 0.48s
```

## Live POST note

The running backend has an API key configured so the live POST went through
the happy path (real AI-generated AC, auto-promoted to ready). The fix
takes effect for subscription-auth users (no `ANTHROPIC_API_KEY`) after
this branch merges and the backend restarts.
