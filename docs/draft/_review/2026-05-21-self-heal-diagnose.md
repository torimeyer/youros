# Self-heal diagnose — 2026-05-21

## Failure 1: pre-agent-guard backend-blip false block

- **Root cause:** `.claude/hooks/lib/rules/isolation_bridge.sh` had a retry loop that attempted the full `/api/agents/spawn` POST up to 3 times, with `sleep 5` between each attempt. The spawn POST uses `--connect-timeout 3 -m 5`, so the total window for 3 retries is ~25 seconds. If the backend experiences a brief connect blip lasting less than 500 ms, the retry loop may not catch it because all three attempts happen in sequence with 5-second pauses. The key gap was the absence of a cheap, fast probe: the code only retried the expensive spawn operation, not a lightweight health check. When `.ostk/` exists in `CLAUDE_PROJECT_DIR` (which it does in worktrees), a 000 response on all retries triggers the fail-closed `deny` with "myOS backend unreachable" — even when the backend was alive and responded to a manual curl 30 seconds after the block.

- **Fix:** Added a fast pre-probe to `/api/health` (3 retries, 300 ms between, 1 s connect timeout, 2 s total) before the spawn POST in `_isolation_bridge_check`. The probe uses a plain GET with no body — no side effects, total overhead under 1 second. Fail-open/fail-closed logic now fires only after the probe exhausts all retries, not after the spawn attempt. The existing spawn retry loop is preserved for the rare case where the spawn itself times out after a confirmed-alive probe.
  - File changed: `.claude/hooks/lib/rules/isolation_bridge.sh` (added 18 lines before `# POST to backend`)

- **Regression test:** `.claude/hooks/tests/test_pre_agent_guard_retry.sh` — 4 cases:
  1. All probes fail + no `.ostk` → FAIL-OPEN (allow native Task)
  2. All probes fail + `.ostk` present → DENY (exit 2)
  3. Healthy backend → PROBE-OK
  4. Transient fail (1st probe closes connection, 2nd succeeds) → PROBE-OK

  Run: `bash .claude/hooks/tests/test_pre_agent_guard_retry.sh`
  Expected: `5 passed, 0 failed` (test 2 checks both exit code and deny message)

- **Commit:** see `fix(hooks): pre-agent-guard retry on transient backend probe`

---

## Failure 2: Monitor internal error

### Sub-question A: Was the 7805acd fix actually deployed?

YES. The current on-disk files exactly match what commit `392e774` added:
- `_adhd_monitor_pairing_disarm()` is present in `.claude/hooks/lib/rules/adhd_monitor_pairing.sh`
- The `Monitor)` case in `.claude/hooks/post-tool-watch.sh` detects `internal error` / `tool result missing` and calls `_disarm`, then emits the ADHD-MODE MONITOR FAILURE reminder

The 7805acd fix is fully deployed.

### Sub-question B: Does wrong-port cause "internal error"?

No, not directly. Monitor calls using `http://localhost:8765/api/agents` (wrong port) cause curl to fail with "connection refused". The Monitor script exits early with an error message, which Claude Code reports as a COMPLETED tool (with non-zero exit or error output), not as "Tool result missing due to internal error".

"Internal error" specifically means Claude Code cancelled the pending Monitor tool call — this happens at the harness level, not because the script failed. Wrong-port is a silent failure mode (script exits early, loop ends, monitor produces no useful output) but it's a different failure than "internal error". Both are bad: wrong-port produces a dead monitor that looks alive, while "internal error" produces a cancelled tool.

The canonical `scripts/monitor-agent.sh` uses `https://127.0.0.1:8000` (correct) and has a strict=False JSON parser and circuit breaker. Custom one-liner polls on `localhost:8765` bypass all of that.

### Sub-question C: Does the ADHD pairing rule force the dangerous same-turn pattern?

No. `_adhd_monitor_pairing_check` accepts any sentinel younger than `sentinel_ttl_seconds` (default 120 s). A Monitor armed in a previous turn (say 30 seconds ago) has a sentinel age of ~30 s, which is < 120 s, so it passes. The rule does NOT require Monitor and Agent to be in the same turn. The 120 s window is intentionally sized to cover "Monitor in the prior turn or two."

The dangerous pattern (Monitor + Agent in same response batch) still occurs because nothing in the current hook *prevents* the model from doing both in one turn — the check only verifies a sentinel exists, not that the Monitor is in a different turn. The 7805acd fix correctly handles recovery: when Monitor fails, the sentinel is disarmed so the next spawn is blocked and the model is prompted to re-arm first.

### Root cause (Failure 2):

Three Monitor calls returned "internal error" because of the →1563 cancellation pattern: Monitor + Agent spawn in the same response batch, where the Agent completes almost immediately via the REST spawn hook (~100 ms), the model continues calling more tools, then a user interaction cancels all pending tool calls including the Monitor. This is an architectural limitation of the Claude Code harness — there is no way to prevent cancellation of a pending Monitor if the user types while tools are in flight.

The 7805acd fix mitigates by forcing re-arm after failure. The remaining gap is that the model needs to learn to put Monitor in the previous turn, not the same turn as Agent spawns.

### Fix:

Updated the deny message in `_adhd_monitor_pairing_check` to explicitly suggest `bash scripts/monitor-agent.sh <agent-name>` as the Monitor command. This guides the model toward the canonical helper that uses the correct port and handles JSON parse errors — preventing the wrong-port silent failure mode described in Sub-question B.
- File changed: `.claude/hooks/lib/rules/adhd_monitor_pairing.sh` (deny message updated)

- **Commit:** see `fix(hooks): ADHD pairing deny suggests monitor-agent.sh canonical helper`

---

## What still won't self-heal

1. **Monitor + Agent same-turn cancellation (→1563):** The harness will still cancel a Monitor tool call if the Agent completes in ~100 ms and the user types. No hook can prevent this — it's a property of the Claude Code tool-batching architecture. The 7805acd disarm loop is the best available mitigation.

2. **Backend down for > 1 second (genuine outage):** The new probe loop covers blips up to ~600 ms total. If the backend is restarting (uvicorn reload takes 5–15 s), all 3 probes will fail and the spawn is correctly denied. This is not a false block — the backend really is down.

3. **Spawn POST slow but backend alive:** If the backend is alive (probe passes) but `/api/agents/spawn` takes > 5 s (e.g., waiting on a lock), the spawn retry loop will produce 000 and fall into the fail-closed path. The probe doesn't help here because it probes `/api/health`, not `/api/agents/spawn`. To fix this, the spawn timeout would need to be raised or the lock contention resolved upstream.
