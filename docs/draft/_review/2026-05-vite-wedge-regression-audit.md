# Vite Wedge Regression Audit — May 2026

**Needle:** →1435 (P2)
**Audit date:** 2026-05-17
**Status of current `app/vite.config.ts`:** 3 of 4 fixes present. One fix
(proxyTimeout on backend restart) was applied to a worktree branch and never
merged into main.

---

## Summary (plain language)

The "Vite wedge" is when the app's local development server stops responding
to browser requests. Five separate agents tried to fix it over several weeks.
Each agent diagnosed a real problem and wrote a real fix. But the fixes kept
coming back for two reasons:

1. **There are actually four distinct wedge failure modes, not one.** Each
   agent fixed the mode they observed in isolation. When one mode disappeared,
   a different one became visible. Nobody had a test that covered all four at
   the same time.

2. **One fix (proxyTimeout, →1378) landed only in a worktree branch and was
   never merged into main.** When the →1431 agent re-applied the TLS fix to
   main, it branched from a point that predated →1378's work, so the
   proxyTimeout was silently dropped. The "diff == empty" claim in that commit
   was comparing the wrong baseline.

---

## The four failure modes

| # | Mode | Root cause | First fixed |
|---|------|-----------|-------------|
| 1 | IPv6 idle wedge | Vite bound to `::1` (IPv6) by default; idle socket entered a stuck state | →1138/→1142 |
| 2 | Dep-optimizer stall | `holdUntilCrawlEnd:true` (Vite default) let a burst of module requests reset the 50ms idle timer forever | →1145 |
| 3 | Backend restart hang | After uvicorn restarts, new TCP connections are accepted by the reloader process but not read; proxied requests hung for up to 75 s (OS TCP timeout) | →1378 |
| 4 | TLS handshake burst | Vite 8 enables HTTP/2, so 50+ concurrent streams hit the proxy at dashboard mount; each became a fresh TLS handshake to the backend, saturating Node's event loop | →1431 |

---

## Each prior agent's work

### Agent 1 — diagnose-card-compass-vite-wedge-91ad77-fix

**Commits merged to main:** `7575f8f` (2026-05-11)

**What it changed:**  
Added `host: '127.0.0.1'` to `vite.server` config, forcing Vite to bind
exclusively to IPv4 instead of defaulting to `::1`.

**Assumption:** The wedge was an IPv6 socket stability issue after idle periods.

**What proved it worked:** Added `scripts/test-vite-no-wedge.sh` — starts Vite,
idles 30 seconds, hits the server 5 times sequentially.

**Why the wedge returned:** The test only covered the post-idle sequential case.
It did not exercise the proxy at all (it hit the Vite dev server directly), so
it could not catch any of the remaining three failure modes.

---

### Agent 2 — diagnose-vite-wedge-in-both-repo-d45f8a / re-diagnose-vite-wedge-with-stat-1d1c60

**Commits:** `85d2f56` on main (2026-05-11); `09600e9` on worktree branch only

**What it changed:**  
Added `optimizeDeps: { holdUntilCrawlEnd: false }`. This tells Vite to apply
dep-optimization results as soon as the initial scan finishes, rather than
waiting for a 50 ms idle window that a burst of requests resets indefinitely.

**Assumption:** The wedge was a dep-optimizer stall triggered by concurrent
module requests after reconnect.

**What proved it worked:** Updated `test-vite-no-wedge.sh` with a Phase 1
concurrent burst (10 simultaneous requests immediately after startup). 3/3 runs
passed.

**Why the wedge returned:** Same gap as Agent 1 — the test never touched the
proxy layer. Failure modes 3 and 4 (backend restart hang, TLS burst) were
still live.

---

### Agent 3 — agent-1378-vite-proxy-wedge-145194 (fix-vite-wedge-tight-scope-retry-549cbe)

**Commits:** `10228f7` on branch `agent-1378-vite-proxy-wedge-145194` only — **NEVER MERGED TO MAIN**

**What it changed:**  
- Added `proxyTimeout: 5000` to both `/api` and `/ws` proxy configs, making
  hanging requests fail in 5 seconds instead of the OS-level 75 second TCP
  timeout.
- Added `VITE_DEV_BACKEND` env-var override so the test could point Vite at a
  mock server.
- Added `scripts/test-vite-proxy-restart.sh` — a test that simulated a backend
  restart cycle and verified the proxy recovered within the timeout.

**Assumption:** The wedge was caused by uvicorn's reloader holding the port
open during restart while no worker read from it.

**What proved it worked:** `scripts/test-vite-proxy-restart.sh` passed.

**Why the fix was lost:**  
The `agent-1378-vite-proxy-wedge-145194` branch was never merged into main.
`git merge-base --is-ancestor 10228f7 dfb7a96` returns false — confirming
`10228f7` is not in main's ancestry. The branch sits as a dead worktree branch
with no PR or merge commit.

Additionally, `scripts/test-vite-proxy-restart.sh` does not exist in the main
repo — it was never carried forward. Without it, no CI gate could catch this
regression.

---

### Agent 4 — worktree-agent-diagnose-fix-1431-vit-94399d33 (re-diagnose-vite-wedge-with-stat-1d1c60)

**Commits:** `b84891e` on worktree branch; cherry-picked as `dfb7a96` to main (2026-05-16)

**What it changed:**  
Added a persistent `https.Agent` (`keepAlive:true, maxSockets:10`) shared
across all proxied requests. This amortises TLS handshake cost across the 50+
concurrent HTTP/2 streams Vite 8 fires on dashboard mount.

**Assumption:** The wedge was caused by Vite 8's HTTP/2 multiplexing sending a
burst of requests that each performed a fresh TLS handshake, saturating Node's
event loop.

**What proved it worked:** `tsc -b` exited 0. `openssl s_client` showed a
complete TLS handshake. The commit claimed "diff vs worktree == empty."

**Why the wedge may return / what the claim got wrong:**  
The "diff == empty" claim compared `b84891e:app/vite.config.ts` to
`dfb7a96:app/vite.config.ts` — and they are byte-for-byte identical. However,
the worktree branch (`worktree-agent-diagnose-fix-1431-vit-94399d33`) was
created from a commit that **predates** `10228f7`. The branch tip `b84891e`
never included `proxyTimeout: 5000`, so `dfb7a96` inherits that absence.

The result: `dfb7a96` (current main) has the TLS agent fix (mode 4) but is
missing the proxyTimeout fix (mode 3). A backend restart still produces
requests that hang for up to 75 seconds.

---

### Agent 5 — diagnose-card-compass-vite-wedge-91ad77-fix (second pass)

This agent name appears in the needle history but produced `601be6e` — a
duplicate of `7575f8f` (the IPv4 fix) on the `worktree-agent-fix-vite-wedge-tight-scope-retry-549cbe`
branch. It was a re-attempt at mode 1 after the wedge reappeared, likely
because mode 2 was still active at that time. The commit never merged to main.

---

## The common regression pattern

Every agent followed the same sequence:
1. Observed a wedge, reproduced it locally.
2. Diagnosed one failure mode correctly.
3. Fixed that mode and wrote a test for it.
4. The test only covered the Vite server layer or one specific failure path.
5. The fix landed on a worktree branch; some were merged, some were not.
6. When one mode was fixed, the next mode became the visible problem.
7. The next agent started from scratch, not from the accumulated fixes.

The core issue: **the tests never covered the full proxy round-trip under the
conditions that actually produce each wedge** — concurrent TLS load, backend
restart, and idle reconnect — in a single test run.

---

## What is missing from main right now

| Missing item | Where it lives | Impact |
|---|---|---|
| `proxyTimeout: 5000` on `/api` proxy | `agent-1378-vite-proxy-wedge-145194` branch only | Backend restarts cause 75s request hangs |
| `proxyTimeout: 5000` on `/ws` proxy | Same branch only | WS reconnect after restart hangs |
| `VITE_DEV_BACKEND` env override | Same branch only | Can't run proxy restart test without it |
| `scripts/test-vite-proxy-restart.sh` | Same branch only (never in main) | No CI gate for backend-restart wedge |
| End-to-end proxy test (TLS burst + restart + idle) | Does not exist anywhere | No single test validates all four modes |

---

## Recommendations for →1431 / whoever fixes this next

1. **Cherry-pick `10228f7` onto main.** It adds `proxyTimeout: 5000` to both
   `/api` and `/ws`, and includes `scripts/test-vite-proxy-restart.sh`. Run
   the test to verify it still passes. The `backendTarget` env-var change can
   be dropped if not needed, but the proxyTimeout is essential.

2. **Do not branch from main at the current tip and re-implement.** Every
   prior re-implementation lost at least one prior fix because the agent
   compared against the wrong baseline. Cherry-pick the dead branch commit
   instead.

3. **Merge all fixes into one test run.** `test-vite-no-wedge.sh` covers
   modes 1 and 2. The proxy restart test covers mode 3. A new test is needed
   for mode 4 (TLS burst through the proxy to the real backend). Without all
   three scripts passing, the fix is incomplete.

4. **Gate on the proxy test, not just `tsc -b`.** TypeScript compilation
   cannot catch runtime proxy behavior. The regression test suite should run
   `scripts/test-vite-no-wedge.sh` and `scripts/test-vite-proxy-restart.sh`
   as part of the release gate.

5. **Do not claim "diff == empty" against a worktree branch.** Always verify
   the worktree branch includes all prior fixes before declaring the diff
   clean. A diff against a worktree that was never rebased onto main's fixes
   will miss them.

---

## Commit reference table

| Commit | Date | Mode fixed | Branch | In main? |
|--------|------|-----------|--------|---------|
| `7575f8f` | 2026-05-11 | Mode 1 (IPv4) | main ancestry | Yes |
| `601be6e` | 2026-05-11 | Mode 1 (IPv4 dupe) | 549cbe worktree only | No |
| `85d2f56` | 2026-05-11 | Mode 2 (holdUntilCrawlEnd) | main ancestry | Yes |
| `09600e9` | 2026-05-11 | Mode 2 (dupe) | 1d1c60 worktree only | No |
| `10228f7` | 2026-05-15 | Mode 3 (proxyTimeout) | agent-1378 branch only | **No** |
| `b84891e` | ~2026-05-16 | Mode 4 (TLS agent) | worktree-1431 branch | No (cherry-picked) |
| `dfb7a96` | 2026-05-16 | Mode 4 (TLS agent) | main | Yes (missing mode 3) |
