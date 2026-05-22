# →1568: Fix Pre-existing Vitest Failures

## Status: IN PROGRESS

## Problem
164 vitest test failures across 8 test files. Verified pre-existing (not regressions from recent work).
Root cause: setup/library drift OR DOM/testid renames that tests didn't catch up to.

## Files to fix

1. `app/src/components/GeminiReadyChip.test.tsx` (6/6 fail)
2. `app/src/components/ChatPanel.test.tsx` + variants (10+ fail)
3. `app/src/components/ReceiptsWarning.test.tsx` (2 fail)
4. `app/src/components/PeerChatTurnsPicker.test.tsx` (9 fail)
5. `app/src/components/__tests__/OnboardingWizard.tracking.test.tsx` (3 fail)
6. `app/src/components/ExecUpdateWidget.test.tsx` (1 fail)
7-8. Additional files from vitest run output

## Approach

For each file:
1. Run vitest on the file alone (`mcp__ostk__spawn` + `interact`)
2. Read failures, identify root cause
3. Fix TESTS to match current behavior (not production code)
4. Re-run to confirm green
5. Commit per-file

## Acceptance criterion

0 vitest failures (or documented residual failures with reasoning)
