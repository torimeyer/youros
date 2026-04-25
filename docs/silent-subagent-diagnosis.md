# Silent subagent no-op diagnosis (2026-04-25)

## Findings

### Victim examined
`p2-f-worktree-base-staleness-guard` (spawned 2026-04-24T21:04:51Z), `l23-hook-worktree-rsync-isolation` (spawned 2026-04-24T20:43:43Z)

Both had `transcript_bytes=0` on `{name}.md`, `tokens_used=0`, status=`completed`, and produced zero commits.

### What `smoke-test-p3-personal-mode-badg-5b60f0` actually was
NOT a no-op. Transcript file at `transcripts/smoke-test-p3-personal-mode-badg-5b60f0.md` has 943 bytes and commit `b1623d7` is real. The API reported `transcript_bytes=0` and `tokens_used=0` only because `tokens_used` is never populated for any agent (universal gap, not per-agent).

### Transcript resolution for bridge-spawned agents
Both victims had `hook_preregister=true`. They were spawned via the Claude Code Task tool, not the REST spawn path. Their actual transcripts live in `/private/tmp/claude-<uid>/.../*.output` files (78 bytes for p2-f, 77 bytes for l23). Those `.output` files contain monitoring poll logs (`[16:05] poll-fail`, `[16:06] running`, `[16:17] terminal state, exit`), not real conversation output.

The `{name}.md` files were always 0 bytes because no backend subprocess wrote to them.

## Root cause

**`ghost_reaper.reap_ghost_agents` used `{name}.md` as the sole liveness signal.**

```python
t_path = transcripts_dir / f"{name}.md"
if t_path.exists() and t_path.stat().st_size > 0:
    continue  # real transcript content present
```

Bridge-spawned agents never write to `{name}.md`. After `STALE_HEARTBEAT_SECONDS=300` (5 min) of no heartbeat, the reaper classified them as ghosts and deleted their registry entries.

Once deleted from `agent_metadata`:
- `/heartbeat` → 404
- `/complete` → 404
- Agent aborts without committing

Timeline for p2-f: spawned 21:04Z, last heartbeat 21:05Z, reaper fires ~21:10Z, parent monitor detects terminal state at 21:17Z.

## Fix landed

`api/services/ghost_reaper.py`: After checking `{name}.md`, also check the `transcript_path` stored in agent metadata. If that file exists and has content, the agent is alive and must not be reaped.

```python
if not transcript_ok:
    raw_tp = meta.get("transcript_path")
    if raw_tp:
        try:
            tp = Path(raw_tp)
            if tp.exists() and tp.stat().st_size > 0:
                transcript_ok = True
        except OSError:
            pass
```

Two new tests in `api/tests/test_ghost_reaper.py`:
- `test_does_not_reap_bridge_agent_with_nonempty_transcript_path`
- `test_reaps_bridge_agent_when_transcript_path_also_empty`

## Secondary findings (not fixed here)

1. **`tokens_used` always 0**: No mechanism updates this field for bridge-spawned agents. Cost tracking is blind for all Task-tool subagents.
2. **`_pick_fresher` can choose empty JSONL over real .md**: The transcript resolver prefers the fresher file by mtime. If a subagent JSONL is created fresh (0 bytes) alongside an older but populated `.md`, the UI reports 0 bytes. Seen for `hook-batch-2-p2-d-p3-g-64f520` which committed `b493a14` but showed `transcript_bytes=0`.
