# Diagnose: status-lie + fs_ops leak (→1234, →1227, →1225)

_Agent: retry-status-lie-leak-diagnose-1-2080ed | Date: 2026-05-12_

---

## Bug A: Premature-complete / status-lie (→1227)

### Observed evidence

1. Agent `onboarding-tracking-step-folder-025d66`:
   - Spawned `2026-05-12T22:46:33Z`, `source=claude-code`, `isolation=worktree`
   - Last heartbeat in stub: `22:50:18Z`
   - `completed_at=22:52:23Z` (124s after last heartbeat)
   - `tokens_used=0`, `worktree_path` set, no `pid` field in metadata
   - `current_step="reading codebase, planning edits"` (still working at completion)

2. This diagnosing agent (`retry-status-lie-leak-diagnose-1-2080ed`):
   - Registered at ~23:36:47Z, heartbeat ACK at 23:37:15Z (`ok: true`)
   - Next heartbeat at ~23:38 rejected: "already in terminal status 'completed'"
   - Marked complete in under 60 seconds of active work

### Root cause — two independent paths, both broken

#### Path A: transcript-idle check uses the wrong file

`_autocomplete_idle_subagents` (agents.py:1660–1686) calls
`_resolve_transcript_source(name)` to find the agent's transcript.
`STALE_AGENT_TRANSCRIPT_GRACE_SECONDS = 120`.

The resolver (agents.py:2332–2521) prefers the fresher of:
- `legacy_md` — `transcripts/{name}.md` (the **heartbeat stub**, written by the backend's `/heartbeat` handler, NOT the Claude conversation)
- `subagent_hit` — JSONL under `~/.claude/projects/<label>/*/subagents/agent-*.jsonl` (the actual conversation)

**The bug**: the heartbeat stub and the Claude conversation JSONL are two different files with independent mtimes. When the agent is actively making model calls but not posting heartbeats (perfectly normal — heartbeats are fire-and-forget), the stub goes idle while the JSONL is still streaming. After 120s of stub idle, Path A marks the agent complete.

For the specific incident: `tokens_used=0` means no model calls ever succeeded — the subprocess crashed before its first Claude API call. No JSONL exists. The stub was the only transcript. Sweep correctly fired 124s after subprocess death. The "lie" window is the 2 minutes the row showed `running` while the process was already dead.

For this diagnosing agent: marked complete in <60 seconds. No transcript JSONL exists for my registered name (I registered directly via API, not via Agent tool). The sweep's Path B (heartbeat age > 300s) should not have fired. **Most likely**: `complete-agent.sh` fired via the `last.name` fallback. When the two pre-existing agents (`py-spy-stack-dump-capture-before-690ec7`, `cache-status-clock-to-dodge-subp-63d5e0`) finished, their PostToolUse fired `complete-agent.sh`. If their per-tool-use name files were missing (bridge-blocked calls = no register-agent.sh), the hook fell back to `last.name` — which is unrelated to my registration but might have been stale from a prior spawn. OR complete-agent.sh fired for a parent session Agent call and used `last.name` which happened to resolve to my name.

#### Path A fix (agents.py)

In `_autocomplete_idle_subagents`, before marking complete via idle transcript, check `last_heartbeat_at`:

```python
# PROPOSED FIX — add before line 1668 (the _transcript_grew_recently check)
_last_hb = _parse_iso(meta.get("last_heartbeat_at"))
if _last_hb and (now - _last_hb).total_seconds() <= STALE_AGENT_TRANSCRIPT_GRACE_SECONDS:
    continue  # agent heartbeated recently; don't trust idle transcript
```

This ensures the sweep never fires on transcript idle alone when the agent is actively heartbeating. Heartbeats are already written to `last_heartbeat_at` by the `/heartbeat` endpoint.

#### Path B: complete-agent.sh `last.name` fallback causes wrong-agent completions

`complete-agent.sh` (line ~170–190) has two-tier name resolution:
1. Per-tool-use: `~/.myos/subagents/by-tool-use/<tool_use_id>.name`
2. Fallback: `~/.myos/subagents/last.name`

When Tier 1 fails (no per-tool-use file — expected for bridge-blocked calls), it falls back to `last.name`. `last.name` is written by `register-agent.sh` and cleared by `complete-agent.sh`. If a prior bridge-spawn wrote to `last.name` but the bridge-blocked complete-agent.sh cleared it... actually the bridge's exit-2 prevents PostToolUse from firing, so complete-agent.sh never runs and `last.name` is NEVER cleared.

Next Agent tool call in the same parent session: register-agent.sh writes a new name to `last.name`. That call's PostToolUse fires complete-agent.sh, which correctly reads the new per-id file. OK so far.

But if a race exists — e.g., two parallel Agent calls complete nearly simultaneously — Tier 1 (per-id) should handle them correctly. Tier 2 (`last.name`) is only racy for the single-agent case.

**The real exposure**: manually registered agents (not via Agent tool) never touch `last.name`, but a stale `last.name` from a previous spawn can point at them if the name happens to match. This is an unlikely but real race.

**Path B fix**: In `complete-agent.sh`, verify the agent row status before POSTing `/complete`. If the row is already terminal, skip. This prevents a stale `last.name` from completing a live agent.

---

## Bug B: fs_ops worktree leak (→1225)

### Root cause — OSTK_SOCKET bypasses worktree root

The spawn endpoint (agents.py:4519):
```python
_spawn_env["OSTK_SOCKET"] = str(_main_sock)  # → main repo's .ostk/ostk.sock
```

`OSTK_SOCKET` causes the subagent's ostk CLI (and therefore the MCP server) to connect to the **main daemon's socket**, not to start a new daemon. The main daemon's project root is `PROJECT_ROOT` (main checkout). When the subagent calls `fs_ops(path="src/foo.py", ...)`, the main daemon resolves `src/foo.py` relative to `PROJECT_ROOT`, writing to the main checkout instead of the worktree.

`OSTK_PROJECT_ROOT` is also set (to the short /tmp symlink of the worktree), but when `OSTK_SOCKET` is set, the daemon is the main daemon — it ignores the caller's `OSTK_PROJECT_ROOT` and uses its own baked-in root.

Why `OSTK_SOCKET` was added (agents.py:4513–4518): macOS sun_path limit is 104 bytes. Long worktree names overflow `<worktree>/.ostk/ostk.sock`. Without `OSTK_SOCKET`, the subagent's ostk tries to bind a socket at that overflowing path, fails, and falls back to degraded mode (no bash/read/fs_ops).

### Fix proposal

**Option A (minimal)**: The main daemon should respect a `--cwd` or per-call project root from the MCP request. The subagent passes its `OSTK_PROJECT_ROOT` in MCP call metadata; the daemon resolves relative paths against that, not its own root.

**Option B (structural)**: Don't set `OSTK_SOCKET` for worktree agents. Instead, start a lightweight per-worktree ostk daemon that uses `OSTK_PROJECT_ROOT` (the short /tmp symlink) as its root. The socket is at `{OSTK_PROJECT_ROOT}/.ostk/ostk.sock` which is short enough (under 104 bytes via /tmp symlink).

**Option C (workaround, already documented in CLAUDE.md)**: Subagent briefs say "use native Edit/Write for file writes, NOT mcp__ostk__fs_ops". This is the current standing workaround. It doesn't fix the root cause but prevents the leak in practice.

The recommended fix is **Option B**: each worktree gets its own ostk daemon, socket path is short (via /tmp symlink), main daemon is not involved in the worktree's file writes. This requires:
1. Remove `OSTK_SOCKET` from the spawn env for worktree agents
2. Ensure the subagent's ostk starts its own daemon with `OSTK_PROJECT_ROOT` as root
3. Socket at `{OSTK_PROJECT_ROOT}/.ostk/ostk.sock` (short path, under limit)

### Confirming evidence

- `agents.py:4519`: `_spawn_env["OSTK_SOCKET"] = str(_main_sock)` — points at main daemon
- `agents.py:4465–4474`: `OSTK_PROJECT_ROOT` and `OSTK_ROOT` set to short/tmp path, but these are only used if `OSTK_SOCKET` is NOT set (daemon start path, not call routing)
- The bridge comment (task-isolation-bridge.sh:~1200): "The ostk MCP server in the subprocess then traverses UP from the worktree through the filesystem until it finds .ostk/ at the main repo root, rooting all bash calls there." — confirms the leak mechanism

---

## Key file locations

- `api/routers/agents.py:1660–1686` — Path A sweep (transcript idle → complete)
- `api/routers/agents.py:1709–1730` — Path B sweep (heartbeat age → complete)  
- `api/routers/agents.py:2266–2279` — `_resolve_transcript_source` cached wrapper
- `api/routers/agents.py:2332–2521` — resolver uncached (steps 0–5)
- `api/routers/agents.py:4519` — `OSTK_SOCKET` set to main daemon (Bug B root)
- `.claude/hooks/complete-agent.sh` — PostToolUse hook, closes agent row; has last.name fallback
- `STALE_AGENT_TRANSCRIPT_GRACE_SECONDS = 120` (agents.py:532)
- `STALE_AGENT_AUTOCOMPLETE_SECONDS = 300` (agents.py:527)

---

## Recommended fixes (implementation order)

1. **Bug A fix (Path A)** — `api/routers/agents.py:1667`: Add heartbeat freshness guard before transcript idle check. ~5 lines. Low risk.
2. **Bug A fix (complete-agent.sh fallback)** — verify agent status via API before POST /complete in the `last.name` fallback branch. ~10 lines.
3. **Bug B fix** — Remove `OSTK_SOCKET` from worktree spawn env; ensure per-worktree ostk daemon starts with the short-path root. Bigger change, needs socket-path testing.
