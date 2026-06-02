# Release-flight retro — 2026-06-02

Session: worktree cleanup, SPA fallback fix, vacuous test sweep, e2e smoke failures, multi-agent parallel fix.
Commits landed: 780bfb72, d77143d5.

---

## What went well

- **Parallel multi-agent fix pattern worked cleanly.** Three agents fixed three independent failures (import path, async flake, cert probe) on non-overlapping files with a "do not commit" scope pin. Parent re-verified and committed once. No race, no git conflict, no double-commit. This is the right pattern.
- **HTTPS probe catch prevented a false negative.** When `curl` returned HTTP 000 on an http:// probe against an HTTPS server, the probe was re-run with `-k https://` before concluding the backend was down. Avoided a disruptive backend restart based on bad signal.
- **SPA fallback bug (task #6) was a real fix.** `api/services/staticfiles_ws_guard.py` previously served `index.html` for ANY 404 including `/api/...` paths. The fix is in place and the logic is clean (see `staticfiles_ws_guard.py:13-15`). The dead-pid reorder in `agents.py:_is_agent_genuinely_live` with the corrected test fixture (`424242` → `os.getpid()`) was methodical.

---

## What cost time or risked correctness

### 1. Worktree reaper: `--lock` worktrees required `-f -f`, not `-f`

402 worktrees classified; removal loop used `git worktree remove --force`. Worktrees created with `git worktree add --lock` refuse single `--force` with "cannot remove a locked working tree". Only 16/175 absorbed worktrees were removed on the first pass. The flag mismatch was not caught until after inspecting git's error output.

**Cost:** One extra discovery round plus a re-run of the removal loop (159 failures before fix).

### 2. Bad pytest import killed entire test collection

`api/tests/test_2023_imessage_newline_escaping.py` had `from api.services.imessage` (wrong) instead of `from services.imessage`. Pytest aborts collection for the entire test file on an import error. The three smoke failures were discovered together, but the import abort pattern is a stealth failure mode: one bad line silently drops all tests in that file from the results.

**Risk:** If the import had been in a shared conftest, it would have aborted the entire suite while still exiting 0 in some runner configurations.

### 3. e2e_smoke.sh SKIPs live phases when server is unreachable — even in RELEASE_MODE

`scripts/e2e_smoke.sh` header says: "If no server is running, the live HTTP and WebSocket phases are skipped with a warning." The reachability probe used `http://` while the backend serves HTTPS; the probe failed. Phases 4 and 5 were SKIPPED, not FAILED. A release gate that silently skips its live verification and exits 0 is worse than no gate. The cert probe fix (d77143d5) adds `-k` to the curl, but the underlying SKIP-on-unreachable logic is still a correctness risk.

**Risk:** A future run where the backend is genuinely down (or misconfigured) would report "smoke passed" with the live phases never running.

### 4. Subagent started shared backend, exit reaped the server

A subagent that ran `dev-backend.sh` as part of its own setup took process ownership of uvicorn. When the agent exited, uvicorn was killed. The main session's backend was gone. One agent misdiagnosed this as "the session is crashlooping." Ground truth was `/tmp/dev-backend.log` showing clean `Application startup complete` every time a new agent's backend came up and was then reaped.

The `dev-backend.sh` in-flight agent guard (`_running` check before kill) only fires when a *living* backend is asked to restart while agents are running. It does not prevent an agent from being the one who started the backend in the first place. The guard is necessary but not sufficient for this scenario.

**Cost:** One round trip (misdiagnosis + re-probe). Potential for silent data loss if an agent with uncommitted work was running when another agent's backend exit killed the server.

### 5. Long test runs with `| tail -N` gave zero progress visibility

`pytest ... | tail -50` buffers all output through the pipe until exit. Monitoring agents saw nothing for the duration of the run and could not distinguish "running" from "hung." The fix was to re-run with a log file and tail sentinel, which added a full test cycle.

`run-vitest.sh` (gen=4, current) and the pytest invocation pattern are both affected.

### 6. spawn alias collisions burned retries

`mcp__ostk__spawn` with a duplicate alias name (`backend`, `imsg`) fails with "alias already in use." Agents that assumed aliases were scoped to their session had to discover this at runtime, retry with a unique alias, and continue. Minor, but visible friction in multi-agent sessions.

### 7. Vacuous tests passed silently for unknown time

- `Settings.integration.test.tsx`: asserted a nav label that no longer renders. Always passed.
- `Sidebar.test.tsx`: test name promised a badge-absence check; the body had no `expect()` call. Always passed.

These were discovered only because a human looked. Green suite ≠ real coverage.

---

## Prioritized improvements (cheapest first)

### P1 — One-line doc: worktree reaper must use `-f -f` for locked trees
**File:** `scripts/worktree-reaper.sh`

Change every `git worktree remove --force` call to `git worktree remove --force --force`. The second `-f` bypasses the lock flag without additional risk (the lock was set by the creation tooling, not a human operator marking the tree as important). Add a comment: "Need -f -f: worktrees created with --lock refuse single --force."

This is a 2-line change. No behavior change in dry-run mode; only affects `--apply` runs.

---

### P2 — Script guard: e2e_smoke.sh exits 1 on unreachable server in RELEASE_MODE
**File:** `scripts/e2e_smoke.sh`

Replace the SKIP-and-continue logic for phases 4 and 5 with a hard `exit 1` when `RELEASE_MODE=1` and the server fails the reachability probe. The script already exports `RELEASE_MODE` and branches on it for the uvicorn `--reload` decision (see `dev-backend.sh:~line 215`). Use the same branch here.

Proposed change (~5 lines):

```bash
if [ "${RELEASE_MODE:-0}" = "1" ] && ! probe_server; then
  echo "ERROR: RELEASE_MODE is set but backend is unreachable. Phases 4/5 cannot run." >&2
  echo "Start the backend with RELEASE_MODE=1 before running the smoke test." >&2
  exit 1
fi
```

In non-release mode the existing SKIP-with-warning stays, since dev runs without a live server are legitimate.

---

### P3 — Convention: subagents must never start or kill shared servers
**File:** `CLAUDE.md` (## Agent rules section)

Add one rule:

> Subagents must never start, restart, or kill shared server processes (`dev-backend.sh`, `dev-frontend.sh`, `start.sh`). Use `curl -sSk --connect-timeout 3 -m 5 https://127.0.0.1:8000/api/health` to check reachability. If the server is down, post to the mailbox and wait. Server lifecycle is the orchestrator's responsibility.

No hook needed. The `dev-backend.sh` in-flight agent guard already prevents the main session's backend from being killed while agents are running, but it cannot protect against an agent that started the backend itself taking ownership of the process. The convention closes this gap.

---

### P4 — Long test runs: log to file, never `| tail -N`
**File:** `CLAUDE.md` (## Development rules section)

Add:

> For any test run expected to take >30s, redirect to a log file rather than piping through `| tail -N`. Example: `pytest ... > /tmp/test-run.log 2>&1; tail -50 /tmp/test-run.log`. For real-time progress from agents, use `mcp__ostk__spawn(alias=..., cmd="pytest ...", wait_for="passed|failed|error")` or `interact(action="read_tail")`.

No script change required. `| tail -N` is a pipe that buffers all output until exit; it is not a progress window.

---

### P5 — Test quality: a test with no `expect()` is a broken test
**File:** `CLAUDE.md` (## Development rules section)

Add:

> A test body with no `expect()` / `assert` call always passes and covers nothing. When writing or reviewing tests, treat "no assertion" as a bug equivalent to a failing assertion. Before committing a test file, confirm every `it()`/`test()` block has at least one `expect()` or `assert_` call.

The cheapest enforcement is a one-line note in the development rules. The next level is a vitest `expect.hasAssertions()` guard added to shared test setup (`app/vitest.setup.ts` if it exists), which causes any assertion-free test to fail rather than pass.

---

## Specific verdicts

| Question | Verdict |
|---|---|
| Should subagents be forbidden from starting/restarting shared servers? | **Yes.** Convention in CLAUDE.md is sufficient (P3 above). A runtime hook would be overkill and would block legitimate health-check scenarios. |
| Should `e2e_smoke.sh` fail-loud when servers are unreachable in RELEASE_MODE? | **Yes, always.** A gate that skips its live verification is not a gate. Non-release mode can still skip with a warning (current behavior is fine for dev). |
| Should the worktree reaper use `-f -f` by default? | **Yes.** The `--lock` flag is set by the creation tooling, not to mark a tree as important. Double-force is safe and the single-force failure mode is silent until you check error output. |
| Should long-run wrappers stop using `| tail` and log to file instead? | **Yes.** `| tail -N` gives zero progress visibility and buffers everything. Log-to-file + `tail -f` or `spawn`/`interact` is strictly better with no downside. |
