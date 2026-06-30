# Rule-enforcement hooks (installed 2026-04-18)

Three hooks installed from `docs/rule-following-diagnosis.md`:

- `ostk-first.sh` — PreToolUse (matcher `Bash|Read|Edit|Write|Grep|Glob`). Blocks native tools with exit 2 when the ostk backend is reachable, telling Claude to use the equivalent `mcp__ostk__*` tool. Whitelists pytest/vitest/tsc/scripts/*.
- `saa-must-spawn.sh` — PreToolUse (same matcher). Reads the latest transcript and blocks any non-Agent tool when the user's last message starts with `saa `, `diagnose `, or `fix `.
- `standing-rules.sh` — UserPromptSubmit. Prepends the top 3 non-negotiable rules to every user turn, then appends a live snapshot of currently running user-spawned agents pulled from `/api/agents` (filter: `source=claude-code`, `status=running`, name does not start with `claude-code-`, excluding ack/heartbeat/e2e bots). Cap 8 rows, then `+N more`. 2s connect / 3s total budget on curl; on backend failure or unparseable JSON it emits a single plain-language "couldn't reach yourOS backend" line so the block never blows up a turn. Backend override via `MYOS_BACKEND_URL` (used by tests). The snapshot exists because the parent Claude Code session's in-memory list of spawned agents is append-only and only reconciles through `<task-notification>` user-turn reminders, so rapid turns or mixed reminders cause it to narrate stale "still running" state. Tests: `.claude/hooks/tests/standing-rules-snapshot.sh` (happy path + backend-down path, 20s budget).
- `native-block-recovery.sh` — PostToolUse (matcher `Bash|Read|Edit|Write|Grep|Glob`). Detects when `ostk-first.sh` blocked the native tool (response contains `Blocked: use mcp__ostk__`), keeps a per-session counter at `~/.youros/subagents/<session-id>-blocks.count`, and once a subagent has been blocked twice within 60s writes a stronger recovery message to stderr telling Claude to call `ToolSearch('select:mcp__ostk__...')` and retry via `mcp__ostk__*`. Resets after 5 blocks or on the first clean tool response. Targets Agent-tool subagents that bounce between blocked native tools instead of reloading ostk. Logs to `/tmp/native-block-recovery.log` (1MB rotation).

Existing hooks and permissions preserved. To pick up the settings changes, restart Claude Code.

after this edit, restart Claude Code once to re-register the hook matcher on the running process

## ADHD depth-probe Monitor template

When ADHD mode is active and a background agent is in flight, the
`[ADHD DEPTH-PROBE RULE]` (injected by `prompt-header.sh`) requires each
60-second check-in to include at least 2 ground-truth signals.

**The failure mode**: the orchestrator arms Monitor with a coarse transcript
poll and then *relays the tick* ("transcript 2901, holding") as its depth
probe. That satisfies zero signals. This template bakes both signals directly
into the Monitor command so every tick is inherently valid.

Arm Monitor with this command (substitute `PID`, `WORKTREE`, `CADENCE`):

```bash
while true; do
  echo "=== probe $(date +%H:%M:%S) ==="
  ps -p PID -o pid,%cpu,etime= 2>/dev/null || echo "pid=PID dead"
  git -C WORKTREE log --oneline -2 2>/dev/null
  sleep CADENCE
done
```

- `ps -p PID` → signal 1 (pid alive + CPU/elapsed advancing)
- `git log --oneline -2` → signal 3 (worktree commits advancing)

For all 4 signals, also add:
```bash
  curl --connect-timeout 2 -m 4 -sSk https://127.0.0.1:8000/api/agents/AGENT_NAME \
    2>/dev/null | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print('step='+str(d.get('current_step','?')), 'bytes='+str(d.get('transcript_bytes',0)))" 2>/dev/null
```

Two consecutive cycles where `ps` shows dead/flat CPU AND `git log` repeats
the same commit = surface "agent may be stalled" and ask whether to cancel.
