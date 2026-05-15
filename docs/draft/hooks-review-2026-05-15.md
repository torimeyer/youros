# Hook System Review — 2026-05-15

A plain-language tour of every active hook in `.claude/hooks/` and `~/.claude/hooks/`. Written for Tori, not engineers.

---

## Quick-scan table

| Hook | When it fires | Worth keeping? |
|------|--------------|----------------|
| `complete-agent.sh` | After a subagent finishes | Keep — closes Agents page rows automatically |
| `heartbeat-and-drain.sh` | Before every tool call (except Agent spawns) | Keep — keeps session rows alive, sweeps idle agents |
| `ostk-agent-start.sh` | When a subagent subprocess starts | Keep — belt-and-suspenders for Agents page reliability |
| `ostk-agent-stop.sh` | When a subagent subprocess stops | Keep — catches cases where the parent session dies before cleanup |
| `post-agent-watch.sh` | After every Agent tool call | Keep — ADHD mode monitoring + scaffold commit alerts |
| `post-tool-filter.sh` | After every tool call | Keep — saves money by stabilizing AI's context cache |
| `post-tool-watch.sh` | After file edits and shell commands | Keep — rule-gated, low cost when rules are off |
| `pre-agent-guard.sh` | Before every Agent/Task spawn | Keep — enforces ADHD monitor pairing + worktree isolation |
| `pre-tool-guard.sh` | Before every tool call (except Agent/Task) | Keep — main safety net for dangerous patterns |
| `prompt-header.sh` | At the start of every user turn | Keep — injects standing rules, agent status, reminders |
| `register-agent.sh` *(project)* | Before every Agent tool call | Keep — core: puts subagents on Agents page before they start |
| `session-end.sh` | When Claude Code exits | Keep — closes session task so Tasks page stays clean |
| `session-start.sh` | When Claude Code opens | Keep — registers session, drains queues, runs hygiene |
| `test-register-agent.sh` | Not a hook — manual test only | Keep — quick safety check, run it when editing register-agent.sh |
| `no-emdashes.sh.disabled` | **Disabled** | Disabled — enforcement moved elsewhere |
| `~/.claude/hooks/register-agent.sh` | Before every Agent call (all projects) | Keep — same as project version, covers non-torios repos |
| `~/.claude/hooks/ostk-agent-start.sh` | When a subagent starts (all projects) | Keep — global copy, same purpose |
| `~/.claude/hooks/ostk-agent-stop.sh` | When a subagent stops (all projects) | Keep — global copy, same purpose |

---

## Project hooks — `.claude/hooks/`

---

### `complete-agent.sh`

**What it watches for.** Fires after any Agent tool call returns in the parent session — meaning the subagent is definitively done.

**What it does.** Marks the subagent's row on the Agents page as "completed." It figures out which agent just finished by reading a side-channel file that `register-agent.sh` wrote at spawn time (keyed by a unique ID so parallel agents don't step on each other). It retries the close call up to three times in case the backend was briefly restarting. If all three attempts fail, it parks the close request in a queue so the next hook cycle can replay it. For subagents that were run in the background and are still working, it detects that flag and skips the close entirely. Finally, if the agent committed to a worktree branch, it tries to fast-forward that branch onto main automatically.

**What would happen if we deleted it.** Finished subagents would stay marked "running" on the Agents page for 10-15 minutes until the stale sweep caught them. Every completed agent would look like it was still working. The nav badge count would be wrong. The worktree auto-merge would stop, leaving commits stranded on branches.

**Worth keeping?** Keep. This is the primary cleanup path for the Agents page.

---

### `heartbeat-and-drain.sh`

**What it watches for.** Fires before every tool call except Agent spawns (which are handled by `pre-agent-guard.sh`).

**What it does.** Three things, all fire-and-forget: (1) sends a heartbeat ping to keep the current session's agent row alive, (2) runs a background sweep every 60 seconds to find subagents whose transcript has gone quiet and marks them completed, (3) drains any completion announcements that were queued while the parent session wasn't looking, printing them into the next turn so you don't miss a landing.

**What would happen if we deleted it.** Session rows would time out after 15 minutes of inactivity. Subagents that exited without calling `/complete` themselves would stay "running" until the stale sweep fired hours later. Mid-turn completion announcements would stop.

**Worth keeping?** Keep. It's the heartbeat that keeps everything alive.

---

### `ostk-agent-start.sh`

**What it watches for.** Fires the moment a subagent subprocess actually starts (the `SubagentStart` event).

**What it does.** Two things: tells ostk's internal journal that an agent started (for its own tracking), then re-pings the Agents page to confirm the row is live. It reads the agent name from the side-channel file that `register-agent.sh` wrote moments earlier.

**What would happen if we deleted it.** ostk's journal would miss agent-start events. The Agents page would still show the row (registered in `register-agent.sh` a moment earlier), but the journal and UI would be slightly out of sync. Low severity.

**Worth keeping?** Keep. Belt-and-suspenders: if `register-agent.sh` had a flaky network moment, this is a second chance to confirm the row.

---

### `ostk-agent-stop.sh`

**What it watches for.** Fires when a subagent subprocess stops (the `SubagentStop` event) — this fires in the child process, not the parent.

**What it does.** Tells ostk's journal the agent stopped, then marks the agent's Agents page row as completed. The key difference from `complete-agent.sh` is that this fires from the subagent's own process, so it still works even if the parent session crashed or was killed before it could clean up.

**What would happen if we deleted it.** If the parent session dies mid-run (context cutoff, terminal kill, network drop), subagent rows would stay "running" until the 15-minute stale sweep. We'd see phantom running agents after every crash.

**Worth keeping?** Keep. This is the crash-resilience path.

---

### `post-agent-watch.sh`

**What it watches for.** Fires after every Agent tool call returns.

**What it does.** Delegates to two optional rules. First, `auto_monitor_spawn`: in ADHD mode, arms a Monitor tool to watch the new agent in the background. Second, `scaffold_commit_watcher`: checks whether the subagent made its first commit within a required deadline (the "scaffold commit" rule). Both rules are toggled on/off in the rules config, so this hook is low-overhead when they're off.

**What would happen if we deleted it.** ADHD mode would stop auto-arming monitors for new agents. The scaffold-commit deadline check would disappear, so agents could go silent without an alert.

**Worth keeping?** Keep, but it only matters when those two rules are enabled.

---

### `post-tool-filter.sh`

**What it watches for.** Fires after every tool call.

**What it does.** Strips two specific pieces of volatile content from ostk's tool output before it lands in the AI's context: the `[ctx]` block (which changes every turn and lists modified files, needles, etc.) and the `tok_calls` counter (which ticks up on every call). These two fields change every turn, which means without this hook, every tool result looks different to Anthropic's prompt cache, and the cache never matches. With this hook, the stable parts of tool output can be cached and reused.

**What would happen if we deleted it.** The prompt cache would effectively stop working for tool results. Every turn would be billed as a fresh cache miss instead of a cache hit. Sessions would run slower and cost more.

**Worth keeping?** Keep. This is directly tied to cost.

---

### `post-tool-watch.sh`

**What it watches for.** Fires after file edits, writes, bash commands, reads, greps, and globs.

**What it does.** Delegates to two rule-gated checks: `edit_postwatch` watches for patterns in recently edited files, and `bash_postwatch` handles a retry queue and recovery from failed native tool calls. Both are opt-in rules.

**What would happen if we deleted it.** The post-edit monitoring rules would stop. The retry queue draining that happens after tool calls would stop.

**Worth keeping?** Keep. Low cost when rules are off; needed when they're on.

---

### `pre-agent-guard.sh`

**What it watches for.** Fires before every Agent or Task tool call — before the subagent actually spawns.

**What it does.** Two checks: (1) ADHD monitor pairing — if you have ADHD mode on, this blocks the agent spawn and tells you to arm a Monitor first; (2) isolation bridge — for agents that will make file edits, this routes the spawn through a REST endpoint that gives the agent its own isolated worktree branch, so it can't accidentally commit to main.

**What would happen if we deleted it.** In ADHD mode, agents could be spawned without monitors, defeating the purpose of ADHD mode. Agents that edit files would work directly in the shared workspace, making it easier for parallel agents to step on each other's commits.

**Worth keeping?** Keep. The isolation bridge is especially important when running multiple agents at once.

---

### `pre-tool-guard.sh`

**What it watches for.** Fires before every tool call except Agent and Task spawns.

**What it does.** The main safety net. It dispatches multiple checks depending on which tool is being called:
- **Bash/shell calls**: blocks `curl` without a timeout (which can hang forever), enforces using ostk tools instead of raw shell, checks for "saa must spawn" patterns, prevents committing directly to main from the wrong context.
- **Read/Edit/Write/Grep/Glob**: reminds to use ostk equivalents; for edits, checks if the user's last message contained performance-related keywords and requires measuring first before changing hot code.
- **File ops (ostk)**: prevents writing to the wrong worktree path.
- **Needle creation**: checks that new work items are formatted correctly.

**What would happen if we deleted it.** Lots of guardrails would vanish: agents could hang by running `curl` without timeouts, could accidentally commit to main, could make edits to performance-sensitive code without measuring first.

**Worth keeping?** Keep. This is the most important safety hook in the project.

---

### `prompt-header.sh`

**What it watches for.** Fires at the start of every user message turn (before the AI sees your prompt).

**What it does.** The largest hook. It injects several blocks into every turn:
- **Standing rules** — five non-negotiable instructions printed at the top of every turn (ostk tools first, spawn agents for saa/diagnose/fix, etc.).
- **Receipts check and stall/death check** — printed reminders that the AI must attach proof before claiming something is "done" or "stalled."
- **Humanfile rules** — any per-session rules from the rules config.
- **ADHD depth-probe rule** — if an agent is running, reminds the AI to check in every 60 seconds with real evidence, not just "working on it."
- **Running agents snapshot** — a live list of which agents are currently running, pulled from the Agents page API.
- **Agents that just finished** — a list of agents that completed since your last turn (so you don't miss a landing).
- **Tool retry queue** — if any tool calls were interrupted (not denied by you), it replays them.
- **Recent hook denies** — shows what the hooks blocked in the last 5 minutes.
- **Scaffold commit alert, incremental commit warning, keep-going-pending-tasks, permission deny detector** — additional optional rule-gated blocks.

**What would happen if we deleted it.** Every turn would start cold with no standing rules, no agent status, no reminders. The AI would have no idea what agents are running or what just landed. Behavioral guardrails would stop working immediately.

**Worth keeping?** Keep. This is the engine that makes the rules visible every turn.

---

### `register-agent.sh` *(project-local)*

**What it watches for.** Fires before every Agent tool call — at spawn time, before the subagent starts.

**What it does.** Registers the about-to-start subagent with the Agents page so it appears immediately, before it has done any work. Derives a stable name from the agent's description. Also spawns a detached background process that pings the agent's heartbeat every 60 seconds for up to 45 minutes, and watches the agent's transcript to auto-close the row if the transcript goes quiet for 2 minutes. Saves the agent's name to a side-channel file so `ostk-agent-start.sh`, `ostk-agent-stop.sh`, and `complete-agent.sh` can all find it. Has a bridge guard that skips registration for edit-capable agents that get routed through the isolation bridge (to avoid a duplicate row).

**What would happen if we deleted it.** Subagents would not appear on the Agents page until they called `/register` themselves (which their brief asks them to do, but they can forget). No background heartbeat loop, so rows would vanish from the Agents page after 15 minutes even for long-running agents.

**Worth keeping?** Keep. This is where the Agents page gets its data.

---

### `session-end.sh`

**What it watches for.** Fires when Claude Code closes a session.

**What it does.** Closes the session's task on the Tasks page. Without this, each session would leave a permanently open task behind.

**What would happen if we deleted it.** The Tasks page would accumulate one open "Claude Code session" task per session, forever, until cleaned up manually.

**Worth keeping?** Keep. One small curl call with big cleanliness payoff.

---

### `session-start.sh`

**What it watches for.** Fires when Claude Code opens a new session.

**What it does.** Five things on every startup:
1. Records the current model name to `.ostk/current_model` so other hooks know what model we're running.
2. Registers the session itself as an agent on the Agents page.
3. Drains any pending subagent registrations that failed while the backend was temporarily down.
4. Runs the worktree reaper to clean up "absorbed" worktrees (branches already merged to main) — skips this if running inside a subagent worktree.
5. Runs the fleet reaper to trim stale entries from `.ostk/agents.jsonl` (which can block commits if it grows too large).
6. Starts the agent-completion-watcher daemon, which pushes mid-turn notifications when watched agents finish.

**What would happen if we deleted it.** Sessions wouldn't appear on the Agents page. Stale worktrees would pile up. The completion-watcher daemon wouldn't start. Pending registrations from network-down periods would never replay.

**Worth keeping?** Keep. Session hygiene depends on this.

---

### `test-register-agent.sh`

**What it watches for.** This is not a hook that fires automatically. It is a standalone test script run manually.

**What it does.** Scans every `curl` call in `register-agent.sh` and verifies that each one includes `--connect-timeout`. Prints PASS or FAIL per line.

**What would happen if we deleted it.** We'd lose a quick sanity check. If someone edited `register-agent.sh` and accidentally removed a timeout flag, the script would hang on every agent spawn. The test catches that before it reaches production.

**Worth keeping?** Keep, and run it after any edit to `register-agent.sh`.

---

### `no-emdashes.sh.disabled`

**Status: disabled.** This hook used to fire after every file edit and block the save if it found an em-dash character in the file. It is currently disabled — enforcement of the no-em-dash rule moved to `CLAUDE.md` instructions instead of a file-level hook. Mentioned here for completeness.

---

## Global hooks — `~/.claude/hooks/`

These three are global versions of project hooks. They fire for every Claude Code session on this machine, regardless of which project is open. This means the Agents page works even when Claude is running outside the torios repo.

---

### `~/.claude/hooks/register-agent.sh`

Nearly identical to the project-local version. The one difference is in name sanitization (slightly stricter regex). Installs the same heartbeat loop, side-channel files, bridge guard, and retry queue behavior. Needed so that non-torios Claude Code sessions still appear on the myOS Agents page.

**Worth keeping?** Keep. Without it, agents spawned from other project directories would be invisible to myOS.

---

### `~/.claude/hooks/ostk-agent-start.sh`

Identical to `.claude/hooks/ostk-agent-start.sh`. Fires for SubagentStart in any project. Tells ostk's journal an agent started and confirms the Agents page row.

**Worth keeping?** Keep. Same reason as project-local version, extended to all projects.

---

### `~/.claude/hooks/ostk-agent-stop.sh`

Identical to `.claude/hooks/ostk-agent-stop.sh`. Fires for SubagentStop in any project. Closes the Agents page row when the subprocess stops, catching cases where the parent session died.

**Worth keeping?** Keep. Same reason as project-local version, extended to all projects.

---

## Summary

All 15 active hooks are worth keeping. They form two categories:

**Agents page lifecycle** (`register-agent.sh`, `complete-agent.sh`, `ostk-agent-start.sh`, `ostk-agent-stop.sh`, `heartbeat-and-drain.sh`, `session-start.sh`, `session-end.sh`, plus the three global copies): together these ensure that every agent shows up on the Agents page when it starts, stays visible while it works, and disappears cleanly when it finishes.

**Behavioral guardrails** (`prompt-header.sh`, `pre-tool-guard.sh`, `pre-agent-guard.sh`, `post-agent-watch.sh`, `post-tool-watch.sh`, `post-tool-filter.sh`): these inject standing rules, block dangerous patterns, enforce ADHD mode monitoring, and keep the prompt cache efficient.

The disabled `no-emdashes.sh` hook enforced a style rule that is now handled through instructions rather than code. No action needed.
