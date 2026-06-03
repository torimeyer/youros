## →2130 backend suite hang — fix summary

### Symptom
Full backend suite (6434 tests) wedged 10+ min. Reproduced as
`+++ Timeout +++` in `kqueue.control(None, …)` at test_1305 within ~2%.

### Real root cause
`api/services/spawn_throttle.py:128` — module-level singleton
`_throttle = SpawnBurstThrottle()` whose:
- `asyncio.Lock` binds to the first test's event loop, leaving every
  later test's loop unable to acquire it cleanly.
- `Deque[float]` sliding window accumulates spawn timestamps across
  tests because nothing reset it.

After 3 spawns within the 30s window, the 4th call to
`_throttle.acquire()` falls into `await asyncio.sleep(~30s)` —
combined with the dead-loop lock, the await never returns.
pytest-timeout's `signal` method can't unblock async sleep waits, so the
suite hangs until a hard SIGKILL.

### Why prior diagnosis was wrong
The earlier note ("leaked ThreadPoolExecutor blocking on
`work_queue.get(block=True)`") was incorrect: `work_queue` does not
exist anywhere in `api/`. The off-task commits (7a2ffe63, 28bae3aa)
that "fixed →2130" only suppressed →2067 assertion-guard noise on
unrelated tests; they did not touch the throttle.

### Fix
Added autouse fixture `_reset_spawn_throttle` to `api/tests/conftest.py`
that calls the existing-but-unused `spawn_throttle.reset_for_testing()`
before and after each test.

### Receipts
- `pytest api/tests/test_1305_ostk_run_canonical.py`
    before: 1 of 10 passed, then `+++ Timeout +++`
    after:  `10 passed in 0.16s`
- `pytest api/tests/test_2130_spawn_throttle_isolation.py` (new
   regression test): `3 passed in 0.03s`
- `pytest -k "test_1 or test_2130 or test_2067 or test_cross_search"`:
   `334 passed, 4 failed, 1 skipped` in 19.27s — no timeouts,
   suite completes; the 4 failures are pre-existing (assertion-guard,
   test_1303, test_1694, test_1739) — unrelated to the throttle.

Commit: e3d2a8a5
