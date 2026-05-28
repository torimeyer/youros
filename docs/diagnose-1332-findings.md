# →1332 Root cause: deprecate-groups-1330 agent silent death

**Date:** 2026-05-14  
**Dead agent:** `deprecate-groups-for-labels-1330-db5b1c`  
**Dead worktree:** `agent-deprecate-groups-for-7ae39275` (reaped)  
**Transcript:** `0af6d20f-df05-4e06-ac97-4d4c0d35f9fa.jsonl` (201652 bytes, 43 lines)

## Root cause

**ostk daemon saturation caused a 3m36s tool call stall. When the agent tried its next MCP interaction after receiving the delayed results, the connection was broken. Claude Code exited silently.**

## Timeline (all UTC)

| Time | Event |
|------|-------|
| 01:47:33Z | Agent spawned, transcript starts |
| 01:47:46Z | First assistant turn (ToolSearch) |
| 01:51:57Z | Last assistant turn — sends two parallel `mcp__ostk__bash` calls (Tasks.tsx, test_threads.py) |
| ~01:42Z | Watchdog: "transient miss, recovered on retry" — backend already degraded |
| 01:55:33Z | Tool results finally arrive (3m36s for simple cat/sed — **massively abnormal**) |
| 01:55:33Z | `todo_reminder` attachment injected (L43 of transcript) |
| *(silence)* | No L44 — no subsequent assistant turn ever written |
| ~02:10Z | TTL reaper (→1308) cleans up agent row, worktree, branch after 900s of silence |

## What did NOT cause this

- **pre-tool-guard / worktree_cwd_guard (→1311)**: guard only blocks git write ops without `cwd=`. All the agent's bash calls used explicit worktree `cwd=`. No hook rejection in transcript.
- **max_turns**: not configured in agents.py spawner.
- **Context window limit**: agent ran only 8 minutes; context would not be full.
- **Backend SIGKILL at 20:58Z (May 13)**: That was the yourOS API backend. The agent survived that event (transcript shows activity at 01:47-01:55Z next day). The ostk MCP daemon is a separate process; watchdog does not monitor it.

## Evidence

- Transcript L42 (user with tool results) + L43 (todo_reminder) = harness had built the next user message but the Claude Code process died before making the API call
- Gap: assistant turn at `01:51:57Z`, results at `01:55:33Z` = **216 seconds** for commands that normally complete in 4ms
- Watchdog log: 19 total entries, last at `01:42:01Z` — "transient miss" — backend was intermittently degraded throughout
- `.ostk/` heartbeat files: `claude-code-3628` mtime `20:55` local = 20 proc entries active simultaneously (massive concurrent load on ostk daemon)
- No hook-trace entries from the dead agent at or after 20:55 local time

## Memory rule written

`feedback_ostk_daemon_saturation_kills_agents.md` — covers the full detection pattern and prevention guidance.
