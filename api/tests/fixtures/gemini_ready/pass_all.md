# Clean Plan — all checks pass

## Goals

Add the Gemini-ready chip to the Tasks and Specs pages.

## Acceptance criteria

- [ ] `api/services/gemini_ready.py` exists with `compute_task_readiness` and `compute_spec_readiness`.
- [ ] Unit tests in `api/tests/test_gemini_ready.py` cover each check.
- [ ] `app/src/components/GeminiReadyChip.tsx` renders on task rows.
- [ ] `app/src/pages/Tasks.tsx` has filter pill.
- [ ] `app/src/pages/Specs.tsx` has filter pill.
