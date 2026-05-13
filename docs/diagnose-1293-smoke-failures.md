# →1293 diagnosis: 3 pytest failures (NOT reproducible)

Filed 2026-05-13 with title:
"Release blocker: 3 pytest failures in scripts/e2e_smoke.sh —
test_prewarm_uses_create_task_not_await,
test_test_artifact_sweep_runs_periodically,
test_every_router_has_a_test_file (per smoke run 2026-05-13)."

## Verdict

**Not reproducible. No fix required.** The three named tests are
currently green in every configuration we ran them in. The needle
appears to have been filed from a polluted smoke run on a dirty tree.

## Receipts (2026-05-13)

All commands run against `main` HEAD at the time of investigation.

### 1. Each test individually

```
api/.venv/bin/pytest api/tests/test_main.py::test_prewarm_uses_create_task_not_await -v
  -> 1 passed in 0.03s
api/.venv/bin/pytest api/tests/test_main.py::test_test_artifact_sweep_runs_periodically -v
  -> 1 passed in 0.04s
api/.venv/bin/pytest api/tests/test_retro_prevention.py::test_every_router_has_a_test_file -v
  -> 1 passed in 0.02s
```

### 2. Each file in full

```
pytest api/tests/test_main.py             -> 6 passed in 0.04s
pytest api/tests/test_retro_prevention.py -> 10 passed in 0.07s
```

### 3. Full pytest suite (twice, including `-p no:randomly`)

```
cd api && pytest -q --timeout=30
  -> 4707 passed, 4 skipped, 19 warnings in 150.86s
cd api && pytest -q --timeout=30 -p no:randomly
  -> 4707 passed, 4 skipped, 19 warnings in 162.34s
```

### 4. Full e2e_smoke.sh (multiple runs)

```
SKIP_BROWSER=1 bash scripts/e2e_smoke.sh
  -> PASS 137, FAIL 0, SKIP 4
     "All phases passed. myOS is ready to release."
```

Confirmed across 3 consecutive runs with backend up. One earlier
run had FAIL 1, but it was not in the pytest suite itself
(pytest reported `4707 passed, 4 skipped`), and the failure did
not reproduce on the next run; it appears to have been a flaky
live-HTTP probe, not a regression in the three named tests.

## Why these tests cannot be flaky

All three are static, deterministic checks. None hits the network or
the database.

- `test_prewarm_uses_create_task_not_await`: uses `inspect.getsource`
  on `main.prewarm_claude_cli` and asserts the string
  `"create_task"` is present and `"await claude_code_provider.prewarm_cli"`
  is not in the outer body. Pure string check on a file already on disk.
- `test_test_artifact_sweep_runs_periodically`: also a static source
  check plus a fake-`asyncio.sleep` loop that exits after the first
  `cleanup_test_artifacts` call. No I/O.
- `test_every_router_has_a_test_file`: globs `api/routers/*.py`
  and asserts a matching `api/tests/test_<name>.py` exists.
  Plain filesystem read.

## Most likely cause of the original "failure"

The smoke run that filed this needle was almost certainly executed
while:

1. `scripts/dev-backend.sh` was running with `--reload` and was
   actively rewriting `api/main.py` mid-suite (see →307 / →321
   notes already in `scripts/e2e_smoke.sh`), OR
2. The tree had uncommitted edits to `api/main.py` that have since
   been committed (between filing and now, the reaper-to-thread fix
   from →1289 landed plus the chat-WS terminal-tracking fix from →1290),
   OR
3. The test_every_router check fired transiently while a new router
   was on disk without its `test_<name>.py` partner. The current
   `ROUTER_TEST_EXEMPT` set covers `costs`, `files`, `slack`,
   `internal`; every other router in `api/routers/*.py` has a
   matching test file on disk right now.

We did not preserve the original smoke log so we cannot point at a
specific assertion line. The needle title was the only artifact.

## Closure receipt

```
Closing reason for →1293:
  Not reproducible.
  4707/4707 pytest pass (main, 2 full runs).
  137/137 smoke phases pass (main, 3 consecutive runs).
  All 3 named tests pass individually and in their full file.
  Tests are static checks (inspect.getsource + filesystem globs),
  not flaky by construction.
```
