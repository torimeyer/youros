# →1569 pytest pollution diagnosis plan

## Problem
`test_env_flag_no_agentfile_falls_back_to_bespoke` (test 2 in `test_1305_ostk_run_canonical.py`) hangs
forever when run after `test_env_flag_routes_research_template_to_ostk_run` (test 1). In isolation,
test 2 passes in 0.04s.

Hang stack: asyncio `_loop.run_until_complete → run_once → _selector.control (kqueue waiting for any event)`

## Investigation plan

### Step 1: Identify what background tasks the lifespan starts
- `main.py` lifespan starts 20+ background tasks via `_keep(asyncio.create_task(...))` on startup
- Key ones: `_agents_snapshot_loop` (0.5s sleep), `_merge_debt_tick_loop` (60s sleep),
  `_spawn_lock_sweep_loop` (30s sleep)
- Lifespan SHUTDOWN only calls `notify_chat_clients_on_shutdown()` — does NOT cancel these tasks

### Step 2: Confirm pytest-asyncio loop scope
- No `asyncio_mode` in pyproject.toml
- Default: new event loop per test (function scope)
- Each `async with AsyncClient(ASGITransport(app=app))` triggers lifespan → new bg tasks on each loop

### Step 3: Find what makes test 2 hang (not test 1)
- Test 1 uses ostk run path (returns early) — bespoke subprocess code never reached
- Test 2 hits bespoke spawn path
- `_install_bespoke_doubles` patches `agents_mod.asyncio.create_subprocess_exec` and `create_task`
- But NOT: `spawn_isolation.asyncio.create_subprocess_exec` (git/rsync commands)
- And NOT: background tasks started by lifespan (snapshot_loop, merge_debt_loop)

### Hypothesis ranking
1. **H1 (primary)**: Background tasks from lifespan startup (`_agents_snapshot_loop`,
   `_merge_debt_tick_loop`) are not cancelled when the `async with AsyncClient` block exits.
   These tasks involve `run_in_executor` + subprocess I/O (git, ps) → kqueue waits.
   Fix: cancel all background tasks in lifespan shutdown OR add `_cancel_background_tasks`
   autouse fixture.

2. **H2**: `spawn_isolation.create_worktree` runs real git subprocesses (not patched).
   If called for test 2's bespoke path and git hangs → kqueue wait.
   Fix: check if `create_worktree` is called for isolation="none".

3. **H3**: `_schedule_ostk_lock_create` uses `asyncio.ensure_future` to schedule a real
   `ostk lock create` subprocess. If this is called during bespoke spawn, it leaks into
   the event loop.

## Fix strategy
Cheapest-first:
1. Add `_cancel_background_tasks` autouse fixture that cancels all pending asyncio tasks after each test
2. If not sufficient: patch the subprocess calls in spawn_isolation as well
3. If not sufficient: configure the app to not start bg loops in test mode

## Acceptance criteria
- [ ] `test_env_flag_no_agentfile_falls_back_to_bespoke` passes when run after test 1
- [ ] Full pytest suite completes without hanging
- [ ] No regressions in other tests
