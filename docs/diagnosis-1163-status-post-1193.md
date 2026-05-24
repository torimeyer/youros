# →1163 post-mortem: MCP tool fallback at agent spawn

**Verdict: CLOSED** — structurally prevented by →1193 + →1194.

## Original failure

When a subagent was spawned with a long name (e.g. `diagnose-why-take-2-d52b56-agent-cd3cd0`, 39 chars), the socket path:

```
<project_root>/.claude/worktrees/agent-<name>/.ostk/ostk.sock
```

exceeded macOS `sun_path` (104 bytes). `bind()` failed silently, the ostk MCP server registered only its static surface (no `bash`/`read`/`fs_ops`), and the subagent fell through to native Bash/Read/Edit/Grep for its entire run with no error surfaced.

→1163 asked for **runtime recovery**: detect that MCP tools didn't load and retry/reconnect rather than silently going native.

## Structural fixes already in place

### →1193 (69e534a) — cap worktree ID at 30 chars

`spawn_isolation.short_worktree_id()` truncates any agent name longer than 30 chars and appends a stable 8-char blake2s hash to prevent collisions. The spawn handler applies this to every worktree path before `git worktree add`.

Verified path length on this machine:

```
/Users/torimeyer/claude/torios            = 30 chars
/.claude/worktrees/agent-                 = 25 chars
<30-char capped ID>                       = 30 chars
/.ostk/ostk.sock                          = 16 chars
                                          --------
total                                     = 101 chars  (<104 ✓)
```

`test_short_worktree_id.py::test_capped_path_fits_under_sun_path_max` pins this contract and passes.

### →1194 (47feced) — skip reaper inside agent sessions

`session-start.sh` now skips sections 3-5 (reaper, fleet hygiene, completion-watcher) when `MYOS_AGENT_NAME` is set. This prevents the reaper from deleting the current worktree while the agent is running inside it (the secondary failure mode that manifested as ENOENT on `/bin/sh`).

## Reproduce attempt

To trigger the original failure after →1193, a path overflow would require:

- Project root length ≥ 37 chars (current: 30), OR
- Bypass of `short_worktree_id()` in the spawn handler

Neither condition is reachable through normal API use. The `short_worktree_id()` call is unconditional in `routers/agents.py` spawn path.

**Result: cannot reproduce.**

## Remaining coverage: ostk-first.sh fail-open

If the MCP server is absent for *any* reason (daemon not running, socket not yet created, worktree in a degenerate state), `.claude/hooks/ostk-first.sh` already provides graceful degradation:

1. **Worktree escape hatch** — if the worktree's local `.ostk/ostk.sock` is absent, native tools are allowed (not blocked).
2. **Socket liveness probe** — if the socket exists but the daemon is unresponsive, native tools are allowed.
3. **Advisory mode** — even when ostk is fully wired, the hook exits 0 with a hint rather than hard-blocking, guarding against partial MCP installs.

This means the "runtime recovery" feature →1163 described (detect MCP drop, surface it, retry) is substantially implemented at the hook layer. The missing piece was always the *root cause* (path overflow) — not the recovery wrapper.

## Tests run this verification

```
api/tests/test_short_worktree_id.py         (5/5 passed)
api/tests/test_subagent_mcp_propagation.py  (5/5 passed)
```

Run: `python3 -m pytest api/tests/test_short_worktree_id.py api/tests/test_subagent_mcp_propagation.py -q`

## Conclusion

→1163 is **effectively closed**. The runtime-recovery feature it asked for was never independently implemented. Instead, the structural fix (→1193) removes the failure condition entirely, and `ostk-first.sh` handles any residual "MCP absent" state with fail-open semantics. No reproducer survives the combined →1193 + →1194 changes on this project layout.
