# Fix doc: →1467 Gemini-ready chip and spawn

*Agent: build-1467-gemini-ready-chip-spa-85725a | 2026-05-17*

## What we're building

A "Gemini-ready" chip on the Tasks and Specs pages that auto-computes whether a task/spec can be handed to Gemini. One click spawns a Gemini agent following the linked plan file.

## Files being created (NEW)

- `api/services/gemini_ready.py` — `compute_task_readiness()` and `compute_spec_readiness()` returning `Readiness(ready, file_path, checks[6])`
- `api/tests/test_gemini_ready.py` — unit tests for all 6 readiness checks
- `api/tests/fixtures/gemini_ready/` — small markdown fixtures (pass/fail per check)
- `app/src/components/GeminiReadyChip.tsx` — chip component (ready=green, partial=muted, absent if 0 checks pass)
- `app/src/components/__tests__/GeminiReadyChip.test.tsx` — frontend unit tests
- `app/src/components/SpawnGeminiModal.tsx` — confirmation modal with file path + AC preview
- `app/src/components/__tests__/SpawnGeminiModal.test.tsx` — frontend unit tests

## Files being modified (EXISTING)

- `api/routers/agents.py` — add `gemini_ready` + `gemini_ready_checks` to task response, `?gemini_ready=true` filter
- `api/routers/specs.py` — same for spec responses, accept `model` query param on build endpoint
- `api/services/chat_providers.py` — confirm gemini path (read-only unless stub found)
- `app/src/pages/Tasks.tsx` — chip per row + filter pill
- `app/src/pages/Specs.tsx` — chip per row + filter pill
- `app/src/lib/spawn.ts` — extend `buildSpec` to accept `model?: "claude" | "gemini"`

## Readiness rule (all 6 must hold)

1. Description/spec contains a path matching docs/spec/*.md, ~/.myos/specs/*.md, or ~/.claude/plans/*.md
2. That file exists on disk
3. File has at least one `- [ ]` AC checkbox
4. No AC line contains TBD / ? / "should we" / "decide" / TODO (case-insensitive)
5. File body contains at least one file path (regex `[\w./-]+\.(py|tsx|...)`)
6. Task not blocked (blocked_by empty or all closed)

## TDD plan

1. RED: write all tests first
2. GREEN: implement to pass
3. Quality gate: pytest + vitest + tsc before done claim
